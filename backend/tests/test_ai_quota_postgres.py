"""Opt in with a disposable LOCAL TEST_POSTGRES_DATABASE_URL.

Each test owns a unique schema; connections are independent and setup committed.
No providers are imported/called. No production migrations are run.
"""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ai_usage_reservation import AIUsageReservation as Ledger, Operation, ReservationState as State
from app.services.ai_quota import (DuplicateReservation, QuotaExceeded, QuotaLimits,
                                  QuotaService, QuotaUnavailable, INTERACTIVE_AI_QUOTA_LOCK)

T = datetime(2026, 10, 3, 12, tzinfo=UTC)


@pytest.fixture
def pg():
    url = os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_DATABASE_URL not set; real PostgreSQL not exercised")
    parsed = make_url(url)
    if parsed.host not in ("localhost", "127.0.0.1", "::1") or "test" not in (parsed.database or "").lower():
        pytest.fail("Quota tests require an explicitly disposable local test database")
    admin = create_engine(url)
    schema = "quota_test_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'CREATE TABLE "{schema}".users (id integer PRIMARY KEY)'))
        conn.execute(text(f'INSERT INTO "{schema}".users VALUES (1), (2)'))
    engine = create_engine(url)
    @event.listens_for(engine, "connect")
    def configure(dbapi, _):
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
        dbapi.commit()
    path = Path(__file__).parents[1] / "alembic/versions/20261003_0003_ai_usage_reservations.py"
    spec = importlib.util.spec_from_file_location("quota_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    def migrate(action):
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, action)()
    migrate("upgrade")
    try:
        yield engine, migrate
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def service(pg, limits=None, clock=None):
    return QuotaService(pg[0], limits, clock=clock or (lambda _: T))


def reserve(s, user=1, key=None, op=Operation.EXTRACTION):
    return s.reserve(user, op, "a" * 64, key)


def rows(pg):
    with Session(pg[0]) as db:
        return db.scalars(select(Ledger)).all()


@pytest.mark.parametrize("limit", ["user_window", "user_daily", "global_daily"])
def test_separate_connection_race(pg, limit):
    s = service(pg, QuotaLimits(**{limit: 1}))
    barrier = Barrier(2)
    def attempt(user):
        barrier.wait(timeout=5)
        try:
            rid = reserve(s, user)
            assert s.mark_dispatched(rid)
            return "dispatch"
        except QuotaExceeded:
            return "reject"
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(attempt, u) for u in (1, 2 if limit == "global_daily" else 1)]
        assert sorted(f.result(timeout=10) for f in futures) == ["dispatch", "reject"]
    assert len(rows(pg)) == 1


def test_duplicate_and_cancellation_races(pg):
    s = service(pg)
    barrier = Barrier(2)
    def keyed():
        barrier.wait(timeout=5)
        try:
            return reserve(s, key="shared")
        except DuplicateReservation:
            return None
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(keyed) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert results.count(None) == 1
    rid = next(r for r in results if r)
    barrier = Barrier(2)
    def transition(fn):
        barrier.wait(timeout=5)
        return fn(rid)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(transition, fn) for fn in (s.cancel, s.mark_dispatched)]
        assert sorted(f.result(timeout=10) for f in futures) == [False, True]
    assert not s.cancel(rid)
    assert not s.mark_dispatched(rid)
    with pytest.raises(DuplicateReservation):
        QuotaService(pg[0], clock=lambda _: T + timedelta(days=1)).reserve(1, Operation.EXTRACTION, "b" * 64, "shared")


def test_lock_released_while_mock_provider_waits(pg):
    s = service(pg)
    rid = reserve(s)
    entered, release = Event(), Event()
    def mock_provider():
        assert s.mark_dispatched(rid)
        entered.set()
        assert release.wait(5)
        assert s.settle(rid, State.SUCCEEDED)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(mock_provider)
        try:
            assert entered.wait(5)
            reserve(s, op=Operation.RAG_QUERY)
        finally:
            release.set()
        future.result(timeout=10)
    assert len(rows(pg)) == 2


def test_exact_window_reduction_and_midnight(pg):
    for offset in (50, 40, 30, 20, 10):
        reserve(service(pg, clock=lambda _, offset=offset: T - timedelta(seconds=offset)))
    reduced = service(pg, QuotaLimits(user_window=2))
    with pytest.raises(QuotaExceeded) as exc:
        reserve(reduced)
    assert exc.value.retry_after == 40
    with pytest.raises(QuotaExceeded):
        reserve(service(pg, QuotaLimits(user_window=2), lambda _: T + timedelta(seconds=39, microseconds=999999)))
    reserve(service(pg, QuotaLimits(user_window=2), lambda _: T + timedelta(seconds=40)))
    assert len(rows(pg)) == 6
    midnight = T.replace(hour=0) + timedelta(days=1)
    old = reserve(service(pg, QuotaLimits(user_daily=10), lambda _: midnight - timedelta(microseconds=1)), 2)
    assert service(pg).mark_dispatched(old)
    reserve(service(pg, QuotaLimits(user_daily=1), lambda _: midnight), 2)
    with pytest.raises(QuotaExceeded) as exc:
        reserve(service(pg, QuotaLimits(user_daily=1), lambda _: midnight), 2)
    assert exc.value.retry_after == 86400


@pytest.mark.parametrize("state", [State.SUCCEEDED, State.FAILED, State.UNCERTAIN])
def test_terminal_transitions_no_refund(pg, state):
    s = service(pg, QuotaLimits(user_window=1))
    rid = reserve(s)
    assert not s.settle(rid, state)
    assert s.mark_dispatched(rid)
    assert not s.cancel(rid)
    assert s.settle(rid, state)
    assert not s.settle(rid, State.FAILED)
    row = rows(pg)[0]
    assert row.created_at <= row.dispatched_at <= row.finished_at
    with pytest.raises(QuotaExceeded):
        reserve(s)


@pytest.mark.parametrize("changes", [
    {"finished_at": T}, {"state": "FAILED"}, {"state": "DISPATCHED"},
    {"state": "CANCELLED"}, {"state": "BOGUS"}, {"operation": "pdf"},
    {"state": "DISPATCHED", "dispatched_at": T - timedelta(microseconds=1)},
    {"state": "CANCELLED", "finished_at": T - timedelta(microseconds=1)},
    {"state": "FAILED", "dispatched_at": T + timedelta(seconds=1), "finished_at": T},
])
def test_database_constraints(pg, changes):
    data = dict(id=uuid4(), user_id=1, operation="extraction", idempotency_key=str(uuid4()), request_hash="a" * 64, state="RESERVED", created_at=T)
    data.update(changes)
    with Session(pg[0]) as db, pytest.raises(IntegrityError):
        db.add(Ledger(**data))
        db.commit()


def test_migration_roundtrip_and_restrict(pg):
    s = service(pg)
    rid = reserve(s)
    assert s.cancel(rid)
    row = rows(pg)[0]
    assert row.dispatched_at is None and row.finished_at == row.created_at
    with pytest.raises(IntegrityError), pg[0].begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id=1"))
    pg[1]("downgrade")
    pg[1]("upgrade")
    assert not rows(pg)
    with pg[0].connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM users")) == 2


def test_production_clock_non_utc(pg):
    @event.listens_for(pg[0], "checkout")
    def timezone(dbapi, *_):
        with dbapi.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'Asia/Shanghai'")
        dbapi.commit()
    s = QuotaService(pg[0])
    reserve(s)
    row = rows(pg)[0]
    assert abs((datetime.now(UTC) - row.created_at).total_seconds()) < 5


def test_lock_wait_uses_clock_after_lock(pg):
    acquired, clock_read = Event(), Event()
    midnight = T.replace(hour=0) + timedelta(days=1)
    def clock(_):
        clock_read.set()
        return midnight
    with pg[0].begin() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(:a,:b)"), dict(zip(("a", "b"), INTERACTIVE_AI_QUOTA_LOCK)))
        with ThreadPoolExecutor(1) as pool:
            def waiter():
                acquired.set()
                return reserve(service(pg, clock=clock))
            future = pool.submit(waiter)
            assert acquired.wait(5)
            assert not clock_read.wait(0.1)
            holder.commit()
            future.result(timeout=10)
    assert rows(pg)[0].created_at == midnight


@pytest.mark.parametrize("stage", ["reserve", "dispatch", "settle"])
@pytest.mark.parametrize("commit_event", ["before_commit", "after_commit"])
def test_commit_failure_closed_and_settlement_safe(pg, stage, commit_event, caplog):
    s = service(pg)
    rid = None
    if stage != "reserve":
        rid = reserve(s)
    if stage == "settle":
        assert s.mark_dispatched(rid)
    def fail(_):
        raise SQLAlchemyError("private database diagnostic")
    event.listen(Session, commit_event, fail)
    try:
        if stage == "settle":
            assert not s.settle_best_effort(rid, State.SUCCEEDED)
            assert len(caplog.records) == 1
            assert "private database diagnostic" not in caplog.text
        else:
            with pytest.raises(QuotaUnavailable):
                reserve(s) if stage == "reserve" else s.mark_dispatched(rid)
            # The caller never receives admission/dispatch permission.
    finally:
        event.remove(Session, commit_event, fail)
    persisted = rows(pg)
    if stage == "settle":
        assert persisted[0].state in (State.DISPATCHED, State.SUCCEEDED)
    elif stage == "dispatch":
        assert persisted[0].state in (State.RESERVED, State.DISPATCHED)
    else:
        assert len(persisted) in (0, 1)


def test_exact_sixty_seconds_and_all_states_count(pg):
    s = service(pg, QuotaLimits(user_window=1))
    rid = reserve(s)
    assert s.cancel(rid)
    with pytest.raises(QuotaExceeded) as exc:
        reserve(service(pg, QuotaLimits(user_window=1), lambda _: T + timedelta(seconds=59, microseconds=999999)))
    assert exc.value.retry_after == 1
    reserve(service(pg, QuotaLimits(user_window=1), lambda _: T + timedelta(seconds=60)))


def test_lock_timeout_and_emergency_prevent_dispatch(pg):
    s = service(pg)
    rid = reserve(s)
    s.enabled = lambda: False
    with pytest.raises(QuotaUnavailable):
        reserve(s)
    with pytest.raises(QuotaUnavailable):
        s.mark_dispatched(rid)
    assert s.cancel(rid)
    with pg[0].begin() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(:a,:b)"), dict(zip(("a", "b"), INTERACTIVE_AI_QUOTA_LOCK)))
        with pytest.raises(QuotaUnavailable):
            reserve(service(pg))
    assert len(rows(pg)) == 1


def test_full_migration_chain_preserves_five_tables_and_metadata(pg):
    from alembic.autogenerate import compare_metadata
    from app.database import Base
    from app.models import Course, Document, DocumentChunk, DocumentStatus, Task, User
    # Replace this test's small prerequisite with the actual frozen migrations.
    pg[1]("downgrade")
    with pg[0].begin() as conn:
        conn.execute(text("DROP TABLE users"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
    # pgvector is installed in public; retain the isolated schema as first path.
    with pg[0].connect() as conn:
        schema = conn.scalar(text("SELECT current_schema()"))
    @event.listens_for(pg[0], "checkout")
    def full_search_path(dbapi, *_):
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}", public')
        dbapi.commit()
    for filename in ("20260914_0001_initial.py", "20260920_0002_knowledge_foundation.py"):
        path = Path(__file__).parents[1] / "alembic/versions" / filename
        spec = importlib.util.spec_from_file_location("baseline_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with pg[0].begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
    with Session(pg[0]) as db:
        user = User(email="quota@example.invalid", username="quota", password_hash="test")
        course = Course(user=user, name="baseline")
        document = Document(course=course, filename="baseline.pdf", storage_path="baseline.pdf", content_hash="a" * 64, file_size=1, status=DocumentStatus.READY)
        db.add_all([user, course, Task(user=user, course=course, title="baseline"), document])
        db.flush()
        db.add(DocumentChunk(document_id=document.id, chunk_index=0, content="baseline", page_number=1, embedding=[0.0] * 1024))
        db.commit()
    pg[1]("upgrade")
    pg[1]("downgrade")
    pg[1]("upgrade")
    with pg[0].connect() as conn:
        for table in ("users", "courses", "tasks", "documents", "document_chunks"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 1
        assert conn.scalar(text("SELECT vector_dims(embedding) FROM document_chunks")) == 1024
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []
