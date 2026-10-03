"""Standalone PostgreSQL quota protocol. Callers must dispatch only after True.

No provider calls, request Sessions, retries, refunds or response caches here.
"""
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
from math import ceil
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ai_usage_reservation import AIUsageReservation as Ledger, Operation, ReservationState as State

INTERACTIVE_AI_QUOTA_LOCK = (117947, 4)
logger = logging.getLogger(__name__)


class QuotaLimits(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    user_window: int = Field(default=5, gt=0)
    window_seconds: int = Field(default=60, gt=0)
    user_daily: int = Field(default=20, gt=0)
    global_daily: int = Field(default=100, gt=0)


class QuotaUnavailable(Exception):
    status_code = 503
    def __init__(self):
        super().__init__("AI quota service is temporarily unavailable")


class DuplicateReservation(Exception):
    status_code = 409
    def __init__(self):
        super().__init__("AI operation key has already been used")


class QuotaExceeded(Exception):
    status_code = 429
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__("AI operation allowance is temporarily exhausted")


def canonical_fingerprint(operation: Operation, inputs: Mapping) -> str:
    """Inputs must already be validated/trimmed; RAG includes course_id."""
    operation = Operation(operation)
    encoded = json.dumps({"operation": operation.value, "inputs": dict(inputs)},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def validate_key(key: str | None) -> str:
    if key is None:
        return str(uuid4())
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or not key.isascii():
        raise ValueError("Idempotency key must contain 1-128 ASCII characters")
    return key


def utc_bounds(t: datetime) -> tuple[datetime, datetime]:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("Quota clock must be timezone aware")
    start = t.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def retry_after(t: datetime, rolling: Sequence[datetime], user_daily: int,
                global_daily: int, limits: QuotaLimits) -> int | None:
    _, midnight = utc_bounds(t)
    waits = []
    if len(rolling) >= limits.user_window:
        expiry = sorted(rolling)[len(rolling) - limits.user_window] + timedelta(seconds=limits.window_seconds)
        waits.append(expiry)
    if user_daily >= limits.user_daily or global_daily >= limits.global_daily:
        waits.append(midnight)
    return max(1, ceil((max(waits) - t).total_seconds())) if waits else None


class QuotaService:
    def __init__(self, engine: Engine, limits: QuotaLimits | None = None,
                 *, enabled: Callable[[], bool] = lambda: True,
                 clock: Callable[[Session], datetime] | None = None):
        self.engine = engine
        self.limits = limits or QuotaLimits()
        self.enabled = enabled
        # Test-only injection is read AFTER acquiring the lock, just like DB time.
        self.clock = clock or (lambda session: session.scalar(select(func.clock_timestamp())))

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Session, datetime]]:
        if self.engine.dialect.name != "postgresql":
            raise QuotaUnavailable()
        try:
            with self.engine.connect().execution_options(isolation_level="READ COMMITTED") as connection:
                with Session(bind=connection, expire_on_commit=False) as session:
                    with session.begin():
                        session.execute(text("SET LOCAL lock_timeout = '2s'"))
                        session.execute(text("SET LOCAL statement_timeout = '5s'"))
                        session.execute(text("SELECT pg_advisory_xact_lock(:a, :b)"),
                                        dict(zip(("a", "b"), INTERACTIVE_AI_QUOTA_LOCK)))
                        t = self.clock(session)
                        utc_bounds(t)
                        yield session, t.astimezone(UTC)
        except SQLAlchemyError:
            raise QuotaUnavailable() from None

    def reserve(self, user_id: int, operation: Operation, request_hash: str,
                key: str | None = None) -> UUID:
        operation = Operation(operation)
        key = validate_key(key)
        if len(request_hash) != 64 or any(c not in "0123456789abcdef" for c in request_hash):
            raise ValueError("Invalid SHA-256 fingerprint")
        if not self.enabled():
            raise QuotaUnavailable()
        reservation_id = uuid4()
        with self._transaction() as (session, t):
            if not self.enabled():
                raise QuotaUnavailable()
            duplicate = session.scalar(select(Ledger.id).where(
                Ledger.user_id == user_id, Ledger.operation == operation.value,
                Ledger.idempotency_key == key))
            if duplicate is not None:
                raise DuplicateReservation()
            start, end = utc_bounds(t)
            window = session.scalars(select(Ledger.created_at).where(
                Ledger.user_id == user_id,
                Ledger.created_at > t - timedelta(seconds=self.limits.window_seconds),
                Ledger.created_at <= t)).all()
            daily = (Ledger.created_at >= start, Ledger.created_at < end)
            user_count = session.scalar(select(func.count()).select_from(Ledger).where(*daily, Ledger.user_id == user_id))
            global_count = session.scalar(select(func.count()).select_from(Ledger).where(*daily))
            wait = retry_after(t, window, user_count, global_count, self.limits)
            if wait is not None:
                raise QuotaExceeded(wait)
            session.add(Ledger(id=reservation_id, user_id=user_id, operation=operation.value,
                               idempotency_key=key, request_hash=request_hash,
                               created_at=t, state=State.RESERVED.value))
        # Session commit acknowledgement and connection close precede this return.
        return reservation_id

    def _transition(self, reservation_id: UUID, target: State) -> bool:
        with self._transaction() as (session, t):
            if target == State.DISPATCHED and not self.enabled():
                raise QuotaUnavailable()
            source = State.RESERVED if target in (State.DISPATCHED, State.CANCELLED) else State.DISPATCHED
            conditions = [Ledger.id == reservation_id, Ledger.state == source.value]
            if source == State.RESERVED:
                conditions.append(Ledger.dispatched_at.is_(None))
            timestamp = func.greatest(t, Ledger.created_at, func.coalesce(Ledger.dispatched_at, Ledger.created_at))
            values = {"state": target.value,
                      "dispatched_at" if target == State.DISPATCHED else "finished_at": timestamp}
            changed = session.execute(update(Ledger).where(*conditions).values(**values)).rowcount == 1
        return changed

    def mark_dispatched(self, reservation_id: UUID) -> bool:
        return self._transition(reservation_id, State.DISPATCHED)

    def cancel(self, reservation_id: UUID) -> bool:
        return self._transition(reservation_id, State.CANCELLED)

    def settle(self, reservation_id: UUID, state: State) -> bool:
        state = State(state)
        if state not in (State.SUCCEEDED, State.FAILED, State.UNCERTAIN):
            raise ValueError("Settlement requires a dispatched terminal state")
        return self._transition(reservation_id, state)

    def settle_best_effort(self, reservation_id: UUID, state: State) -> bool:
        """Caller preserves its original result/error; never replay provider work."""
        try:
            if self.settle(reservation_id, state):
                return True
        except QuotaUnavailable:
            pass
        logger.warning("AI quota settlement failed", extra={"event": "ai_quota_settlement_failed", "reason": "settlement_unconfirmed"})
        return False
