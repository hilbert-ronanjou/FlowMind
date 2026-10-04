"""Small PostgreSQL PDF admission/claim/fencing protocol; never calls providers."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.document_processing_attempt import DocumentProcessingAttempt as Attempt
from app.services.ai_guard import ensure_paid_ai_enabled
from app.services.ai_quota import utc_bounds

DOCUMENT_PROCESSING_LOCK = (117947, 5)  # Independent of M4-A's lock and ledger.
DOCUMENT_EXECUTION_LOCK_NAMESPACE = 117948
USER_EXECUTION_LOCK_NAMESPACE = 117949
INTERRUPTED_REASON = "Document processing was interrupted. Please retry."


class DocumentProtectionUnavailable(RuntimeError):
    pass


class DocumentAllowanceExceeded(RuntimeError):
    def __init__(self, retry_after: int, *, busy: bool = False):
        self.retry_after = retry_after
        self.code = "document_processing_busy" if busy else "document_processing_quota"
        super().__init__("A document is already processing. Please wait." if busy else
                         "Daily PDF processing allowance exhausted. Try after UTC midnight.")


class ObsoleteAttempt(RuntimeError):
    pass


def lock_processing(db: Session) -> datetime:
    """Called FIRST, before any Course/Document row lock. Fail closed off PG."""
    if db.get_bind().dialect.name != "postgresql":
        raise DocumentProtectionUnavailable()
    if db.connection().get_isolation_level() != "READ COMMITTED":
        raise DocumentProtectionUnavailable()
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    db.execute(text("SET LOCAL statement_timeout = '5s'"))
    db.execute(text("SELECT pg_advisory_xact_lock(:a, :b)"),
               dict(zip(("a", "b"), DOCUMENT_PROCESSING_LOCK)))
    return db.scalar(select(func.clock_timestamp())).astimezone(UTC)


@contextmanager
def processing_transaction(db: Session):
    """Caller has ended read-only preflight; commit acknowledgement gates work."""
    try:
        yield lock_processing(db)
        db.commit()
    except SQLAlchemyError:
        abort_processing(db)
        raise DocumentProtectionUnavailable() from None
    except BaseException:
        abort_processing(db)
        raise


def abort_processing(db: Session) -> None:
    """Commit acknowledgement may fail after the session is already committed."""
    try:
        db.rollback()
    except SQLAlchemyError:
        db.close()


def _timestamp(attempt: Attempt, now: datetime) -> datetime:
    # SQLite unit regressions use naive DB timestamps; runtime PG is always aware.
    values = [now, attempt.created_at, attempt.dispatched_at or attempt.created_at]
    return max(value.replace(tzinfo=UTC) if value.tzinfo is None else value for value in values)


def recover_expired(db: Session, now: datetime) -> int:
    """Under the PDF lock, expire slots, never refund or automatically resume."""
    attempts = db.scalars(select(Attempt).where(
        Attempt.finished_at.is_(None), Attempt.expires_at <= now)).all()
    for attempt in attempts:
        document = db.scalar(select(Document).where(
            Document.id == attempt.document_id).with_for_update())
        if document is not None and document.current_attempt_id == attempt.id and document.status == DocumentStatus.PROCESSING:
            document.status = DocumentStatus.FAILED
            document.failure_reason = INTERRUPTED_REASON
            document.updated_at = now
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        attempt.state = "UNCERTAIN" if attempt.dispatched_at is not None else "CANCELLED"
        attempt.finished_at = _timestamp(attempt, now)
    db.flush()  # Release partial-unique slots before inserting another admission.
    return len(attempts)


def reserve_attempt(db: Session, document: Document, user_id: int, now: datetime) -> UUID:
    """Document creation/reset and reservation share the SAME locked transaction."""
    ensure_paid_ai_enabled()
    settings = get_settings()
    active = db.scalar(select(Attempt).where(
        Attempt.user_id == user_id, Attempt.finished_at.is_(None)))
    if active is not None:
        expires = active.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        raise DocumentAllowanceExceeded(max(1, ceil((expires - now).total_seconds())), busy=True)
    start, end = utc_bounds(now)
    daily = (Attempt.created_at >= start, Attempt.created_at < end)
    user_count = db.scalar(select(func.count()).select_from(Attempt).where(*daily, Attempt.user_id == user_id))
    total = db.scalar(select(func.count()).select_from(Attempt).where(*daily))
    if user_count >= settings.document_user_daily_limit or total >= settings.document_global_daily_limit:
        raise DocumentAllowanceExceeded(max(1, ceil((end - now).total_seconds())))
    db.flush()
    # Non-blocking only: a stale but still executing worker must finish before
    # Retry can create another paid chain. Released with this short transaction.
    for namespace, identity in ((USER_EXECUTION_LOCK_NAMESPACE, user_id),
                                (DOCUMENT_EXECUTION_LOCK_NAMESPACE, document.id)):
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:a, :b)"),
                         {"a": namespace, "b": identity}):
            raise DocumentAllowanceExceeded(60, busy=True)
    rid = uuid4()
    db.add(Attempt(id=rid, user_id=user_id, document_id=document.id, state="RESERVED",
                   created_at=now, expires_at=now + timedelta(seconds=settings.document_processing_stale_seconds)))
    document.current_attempt_id = rid
    document.status = DocumentStatus.PROCESSING
    document.failure_reason = None
    document.updated_at = now
    db.flush()
    return rid


@contextmanager
def execution_guard(engine, document_id: int, user_id: int):
    """Per-document session lock, with NO open transaction during provider I/O.

    One connection is occupied until this in-process job exits. Quota locks and
    Document row locks are separate short transactions, not held here.
    """
    if engine.dialect.name != "postgresql":
        raise DocumentProtectionUnavailable()
    with engine.connect() as connection:
        # Enter cleanup protection BEFORE setting isolation: setup can fail too.
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        acquired = []
        try:
            for namespace, identity in ((USER_EXECUTION_LOCK_NAMESPACE, user_id),
                                        (DOCUMENT_EXECUTION_LOCK_NAMESPACE, document_id)):
                parameters = {"a": namespace, "b": identity}
                if not connection.scalar(text("SELECT pg_try_advisory_lock(:a, :b)"), parameters):
                    raise ObsoleteAttempt()
                acquired.append(parameters)
            pid = connection.scalar(text("SELECT pg_backend_pid()"))

            def check_connection():
                if connection.closed or connection.invalidated:
                    raise DocumentProtectionUnavailable()
                if connection.scalar(text("SELECT pg_backend_pid()")) != pid:
                    raise DocumentProtectionUnavailable()

            yield check_connection
        finally:
            # Do not turn an already finalized outcome into an error if DB dies
            # during cleanup. PostgreSQL releases locks when the session dies.
            try:
                if not connection.closed and not connection.invalidated:
                    for parameters in reversed(acquired):
                        connection.execute(text("SELECT pg_advisory_unlock(:a, :b)"), parameters)
            except SQLAlchemyError:
                connection.invalidate()


def _current(db: Session, document_id: int, attempt_id: UUID, now: datetime):
    attempt = db.get(Attempt, attempt_id)
    document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if (attempt is None or document is None or attempt.document_id != document_id or
            document.current_attempt_id != attempt_id or document.status != DocumentStatus.PROCESSING or
            attempt.finished_at is not None):
        raise ObsoleteAttempt()
    expiry = attempt.expires_at
    if (expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry) <= now:
        raise ObsoleteAttempt()
    return document, attempt


def claim_attempt(db: Session, document_id: int, attempt_id: UUID, now: datetime) -> str:
    document, attempt = _current(db, document_id, attempt_id, now)
    if attempt.state != "RESERVED":
        raise ObsoleteAttempt()
    attempt.state = "RUNNING"
    return document.storage_path


def dispatch_batch(db: Session, document_id: int, attempt_id: UUID, now: datetime) -> None:
    ensure_paid_ai_enabled()
    document, attempt = _current(db, document_id, attempt_id, now)
    if attempt.state not in ("RUNNING", "DISPATCHED"):
        raise ObsoleteAttempt()
    attempt.state = "DISPATCHED"
    if attempt.dispatched_at is None:
        attempt.dispatched_at = _timestamp(attempt, now)
    attempt.expires_at = now + timedelta(seconds=get_settings().document_processing_stale_seconds)
    document.updated_at = now


def finish_attempt(db: Session, document_id: int, attempt_id: UUID, now: datetime,
                   *, chunks=None, vectors=None, reason: str | None = None,
                   failure: str = "FAILED") -> None:
    document, attempt = _current(db, document_id, attempt_id, now)
    if attempt.state not in ("RUNNING", "DISPATCHED"):
        raise ObsoleteAttempt()
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    if reason is None:
        if attempt.dispatched_at is None:
            raise ObsoleteAttempt()
        db.add_all([DocumentChunk(document_id=document_id, chunk_index=index,
                                  page_number=chunk.page_number, content=chunk.content, embedding=vector)
                    for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))])
        document.status = DocumentStatus.READY
        attempt.state = "SUCCEEDED"
    else:
        document.status = DocumentStatus.FAILED
        attempt.state = failure if attempt.dispatched_at is not None else "CANCELLED"
    document.failure_reason = reason
    document.updated_at = now
    attempt.finished_at = _timestamp(attempt, now)
