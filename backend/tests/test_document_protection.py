"""PDF protection acceptance: independent disposable PG connections, mock providers."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from openai import APITimeoutError, InternalServerError
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import documents as routes
from app.core import config
from app.database import Base, get_db
from app.main import app
from app.models.course import Course
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.document_processing_attempt import DocumentProcessingAttempt as Attempt
from app.models.ai_usage_reservation import AIUsageReservation, Operation
from app.models.user import User
from app.services import document_protection as protection, embedding
from app.services.ai_quota import QuotaService
from app.services.knowledge import ingestion
from conftest import register_user
from test_document_ingestion import make_pdf

REAL_LOCK = protection.lock_processing
REAL_EXECUTION = protection.execution_guard


def test_execution_guard_setup_failure_returns_connection(monkeypatch):
    from sqlalchemy.pool import QueuePool

    engine = create_engine("sqlite://", poolclass=QueuePool)
    # Exercise the PostgreSQL-only guard without contacting a provider or DB.
    monkeypatch.setattr(engine.dialect, "name", "postgresql")

    def fail_setup(_connection, _options):
        raise SQLAlchemyError("execution connection setup failed")

    event.listen(engine, "set_connection_execution_options", fail_setup)
    try:
        with pytest.raises(SQLAlchemyError) as error:
            with protection.execution_guard(engine, 1, 1):
                pytest.fail("Failed setup must not start processing")
        assert "setup failed" in str(error.value)
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


@pytest.fixture
def pdf_pg(monkeypatch):
    url = os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_DATABASE_URL not set; real PostgreSQL not exercised")
    parsed = make_url(url)
    if parsed.host not in ("localhost", "127.0.0.1", "::1") or "test" not in (parsed.database or "").lower():
        pytest.fail("PDF protection tests require an explicitly disposable local test database")
    admin = create_engine(url)
    schema = "pdf_test_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def search_path(dbapi, _):
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}", public')
        dbapi.commit()

    # public contains the migrated acceptance DB. Never let checkfirst treat its
    # visible tables as ours: this freshly created schema must own every table.
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_schema()")) == schema
    Base.metadata.create_all(engine, checkfirst=False)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with sessions.begin() as db:
        for index in (1, 2):
            db.add(Course(user=User(email=f"user{index}@example.invalid", username="Test", password_hash="mock"), name=f"Course {index}"))
    monkeypatch.setattr(protection, "lock_processing", REAL_LOCK)
    monkeypatch.setattr(routes, "lock_processing", REAL_LOCK)
    monkeypatch.setattr(ingestion, "SessionLocal", sessions)
    monkeypatch.setattr(ingestion, "execution_guard", REAL_EXECUTION)

    def override_db():
        with sessions() as db:
            yield db
    old_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = override_db
    try:
        yield engine, sessions
    finally:
        app.dependency_overrides[get_db] = old_override
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def admit(pg, *, user=1, document_id=None, payload=None, now=None):
    with pg[1]() as db:
        with protection.processing_transaction(db) as database_now:
            timestamp = now or database_now
            protection.recover_expired(db, timestamp)
            if document_id is None:
                key = uuid4().hex
                source = ingestion.resolve_storage_path(key + ".pdf")
                source.write_bytes(payload or make_pdf(["Embedding content"]))
                document = Document(course_id=user, filename=key + ".pdf", storage_path=source.name,
                                    content_hash=key * 2, file_size=source.stat().st_size,
                                    status=DocumentStatus.PROCESSING)
                db.add(document)
            else:
                document = db.get(Document, document_id)
                assert document.status == DocumentStatus.FAILED
            rid = protection.reserve_attempt(db, document, user, timestamp)
            did = document.id
        return did, rid


@pytest.mark.parametrize("failure", ["body", "partial_acquisition", "unlock"])
def test_execution_guard_failures_release_locks_and_connections(pdf_pg, failure):
    engine = pdf_pg[0]
    baseline = engine.pool.checkedout()

    def fail_unlock(_conn, _cursor, statement, _parameters, _context, _many):
        if "pg_advisory_unlock" in statement:
            raise SQLAlchemyError("unlock failed")

    if failure == "partial_acquisition":
        # Hold only the second lock: the guard must release its first user lock.
        with engine.connect() as holder:
            holder = holder.execution_options(isolation_level="AUTOCOMMIT")
            holder.execute(text("SELECT pg_advisory_lock(117948, 1)"))
            try:
                with pytest.raises(protection.ObsoleteAttempt) as error:
                    with protection.execution_guard(engine, 1, 2):
                        pytest.fail("The Document lock is already held")
                assert error.value is not None
                assert engine.pool.checkedout() == baseline + 1
            finally:
                holder.execute(text("SELECT pg_advisory_unlock(117948, 1)"))
    elif failure == "body":
        with pytest.raises(RuntimeError) as error:
            with protection.execution_guard(engine, 1, 2):
                raise RuntimeError("local processing failed")
        assert error.value is not None
    else:
        event.listen(engine, "before_cursor_execute", fail_unlock)
        try:
            with protection.execution_guard(engine, 1, 2):
                pass
        finally:
            event.remove(engine, "before_cursor_execute", fail_unlock)
    assert engine.pool.checkedout() == baseline
    # A separate DB session can acquire both locks after every exit path.
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT pg_try_advisory_xact_lock(117949, 2)"))
        assert connection.scalar(text("SELECT pg_try_advisory_xact_lock(117948, 1)"))


def saved(pg, did, rid):
    with pg[1]() as db:
        return db.get(Document, did), db.get(Attempt, rid), list(db.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == did)))


@pytest.fixture
def provider(monkeypatch, pdf_pg):
    def embed(texts, *, max_retries):
        assert max_retries == 0
        # Separate connection proves commit visibility and no held quota lock.
        with pdf_pg[0].begin() as connection:
            assert connection.scalar(text("SELECT pg_try_advisory_xact_lock(117947,5)"))
            assert connection.scalar(select(func.count()).select_from(Attempt).where(Attempt.state == "DISPATCHED")) > 0
            # Execution exclusion retains a session lock, not a long transaction.
            assert connection.scalar(text("SELECT bool_and(a.xact_start IS NULL) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='advisory' AND l.classid=117948 AND l.granted")) is True
        return [[0.1] * 1024 for _ in texts]
    mock = Mock(side_effect=embed)
    monkeypatch.setattr(ingestion, "embed_texts", mock)
    return mock


@pytest.mark.parametrize("bound", ["pages", "text_chars", "chunks"])
def test_bounds_reject_before_any_paid_call(pdf_pg, provider, bound):
    setattr(config.get_settings(), "document_max_" + bound, 1)
    did, rid = admit(pdf_pg, payload=make_pdf(["First page", "Second page"]))
    ingestion.process_document(did, rid)
    document, attempt, chunks = saved(pdf_pg, did, rid)
    assert document.status == DocumentStatus.FAILED and "limit exceeded" in document.failure_reason
    assert attempt.state == "CANCELLED" and attempt.dispatched_at is None and attempt.finished_at is not None
    assert not chunks and provider.call_count == 0


@pytest.mark.parametrize("bound,value", [("pages", 1), ("text_chars", 17), ("chunks", 1)])
def test_exact_bounds_still_process(pdf_pg, provider, bound, value):
    setattr(config.get_settings(), "document_max_" + bound, value)
    did, rid = admit(pdf_pg)
    ingestion.process_document(did, rid)
    document, attempt, chunks = saved(pdf_pg, did, rid)
    assert document.status == DocumentStatus.READY and attempt.state == "SUCCEEDED"
    assert len(chunks) == 1 and provider.call_count == 1


@pytest.mark.parametrize("global_limit", [False, True])
def test_daily_limits_failures_count_without_interactive_debits(pdf_pg, provider, global_limit):
    if global_limit:
        config.get_settings().document_global_daily_limit = 2
    # Pre-provider failures still count as admitted attempts, never refund.
    config.get_settings().document_max_text_chars = 1
    for _ in range(2):
        did, rid = admit(pdf_pg)
        ingestion.process_document(did, rid)
    with pytest.raises(protection.DocumentAllowanceExceeded) as error:
        admit(pdf_pg, user=2 if global_limit else 1)
    assert not error.value.code.endswith("busy") and 1 <= error.value.retry_after <= 86400
    with pdf_pg[1]() as db:
        assert db.scalar(select(func.count()).select_from(Attempt)) == 2
        assert db.scalar(select(func.count()).select_from(AIUsageReservation)) == 0
    assert provider.call_count == 0
    # Interactive admission remains independent.
    QuotaService(pdf_pg[0]).reserve(1, Operation.EXTRACTION, "a" * 64)


@pytest.mark.parametrize("race", ["slot", "user_day", "global_day"])
def test_atomic_concurrent_admission(pdf_pg, provider, race):
    if race != "slot":
        setattr(config.get_settings(), "document_" + ("global" if race == "global_day" else "user") + "_daily_limit", 1)
    barrier = Barrier(2)
    def submit(user):
        barrier.wait(timeout=5)
        try:
            return admit(pdf_pg, user=user)
        except protection.DocumentAllowanceExceeded:
            return None
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(submit, user) for user in (1, 2 if race == "global_day" else 1)]
        results = [future.result(timeout=10) for future in futures]
    assert results.count(None) == 1
    did, rid = next(result for result in results if result is not None)
    ingestion.process_document(did, rid)
    assert provider.call_count == 1


def test_duplicate_worker_claim_and_late_second_invocation(pdf_pg, provider):
    did, rid = admit(pdf_pg)
    entered, release = Event(), Event()
    original = provider.side_effect
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    provider.side_effect = blocked
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(ingestion.process_document, did, rid)
        try:
            assert entered.wait(5)
            pool.submit(ingestion.process_document, did, rid).result(timeout=5)
        finally:
            release.set()
        first.result(timeout=10)
    ingestion.process_document(did, rid)
    assert provider.call_count == 1 and saved(pdf_pg, did, rid)[0].status == DocumentStatus.READY


@pytest.mark.parametrize("new_success", [False, True])
def test_retry_fences_old_success_and_failure(pdf_pg, provider, new_success):
    did, old = admit(pdf_pg)
    with pdf_pg[1]() as db:
        with protection.processing_transaction(db) as now:
            protection.claim_attempt(db, did, old, now)
            protection.dispatch_batch(db, did, old, now)
    with protection.execution_guard(pdf_pg[0], did, 1):
        old_vectors = provider(["Old content"], max_retries=0)
    with pdf_pg[1].begin() as db:
        old_attempt = db.get(Attempt, old)
        old_attempt.created_at -= timedelta(hours=1)
        old_attempt.dispatched_at -= timedelta(hours=1)
        old_attempt.expires_at = old_attempt.created_at + timedelta(minutes=30)
    new_did, new = admit(pdf_pg, document_id=did)
    assert new != old and new_did == did
    if not new_success:
        config.get_settings().document_max_text_chars = 1
    ingestion.process_document(did, new)
    # Delayed old persistence/failure callbacks, including after connection loss.
    with pytest.raises(protection.ObsoleteAttempt):
        ingestion._persist_ready_document(did, [ingestion.ChunkDraft(1, "Old content")], old_vectors, old)
    ingestion._mark_document_failed(did, "old failure", old)
    ingestion.process_document(did, old)
    document, attempt, chunks = saved(pdf_pg, did, new)
    assert document.current_attempt_id == new
    assert attempt.state == ("SUCCEEDED" if new_success else "CANCELLED")
    assert document.status == (DocumentStatus.READY if new_success else DocumentStatus.FAILED)
    assert (len(chunks) == 1) == new_success
    assert saved(pdf_pg, did, old)[1].state == "UNCERTAIN"
    assert provider.call_count == (2 if new_success else 1)


def test_retry_cannot_overlap_stale_but_still_executing_provider(pdf_pg, provider):
    did, old = admit(pdf_pg)
    entered, release = Event(), Event()
    original = provider.side_effect
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    provider.side_effect = blocked
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(ingestion.process_document, did, old)
        try:
            assert entered.wait(5)
            with pdf_pg[1].begin() as db:
                attempt = db.get(Attempt, old)
                attempt.created_at -= timedelta(hours=1)
                attempt.dispatched_at -= timedelta(hours=1)
                attempt.expires_at = attempt.created_at + timedelta(minutes=30)
            with pytest.raises(protection.DocumentAllowanceExceeded) as error:
                admit(pdf_pg, document_id=did)
            assert error.value.code == "document_processing_busy"
            # A different Document must not bypass the same user's live worker.
            with pytest.raises(protection.DocumentAllowanceExceeded):
                admit(pdf_pg)
            with pdf_pg[1]() as db:
                assert db.scalar(select(func.count()).select_from(Attempt)) == 1
            assert provider.call_count == 1
        finally:
            release.set()
        future.result(timeout=10)
    provider.side_effect = original
    _, new = admit(pdf_pg, document_id=did)
    ingestion.process_document(did, new)
    assert saved(pdf_pg, did, new)[0].status == DocumentStatus.READY
    assert provider.call_count == 2


@pytest.mark.parametrize("failure", ["known", "timeout", "db_before_dispatch", "db_second_batch", "shutdown"])
def test_failure_states_and_zero_replay(pdf_pg, provider, monkeypatch, failure, caplog):
    did, rid = admit(pdf_pg, payload=make_pdf(["One", "Two"]))
    config.get_settings().embedding_batch_size = 1
    request = httpx.Request("POST", "https://provider.invalid/v1")
    if failure == "known":
        cause = InternalServerError("private key and PDF", response=httpx.Response(500, request=request), body=None)
    else:
        cause = APITimeoutError(request=request)
    if failure in ("known", "timeout"):
        wrapped = embedding.EmbeddingProviderError("safe")
        wrapped.__cause__ = cause
        provider.side_effect = wrapped
    elif failure.startswith("db_"):
        real_dispatch = ingestion.dispatch_batch
        count = 0
        def dispatch(*args):
            nonlocal count
            count += 1
            if count == (1 if failure == "db_before_dispatch" else 2):
                raise SQLAlchemyError("private database diagnostic")
            return real_dispatch(*args)
        monkeypatch.setattr(ingestion, "dispatch_batch", dispatch)
    else:
        config.get_settings().ai_paid_operations_enabled = False
    ingestion.process_document(did, rid)
    document, attempt, chunks = saved(pdf_pg, did, rid)
    assert document.status == DocumentStatus.FAILED and not chunks
    assert attempt.state == ("UNCERTAIN" if failure == "timeout" else
                             "CANCELLED" if failure in ("db_before_dispatch", "shutdown") else "FAILED")
    assert provider.call_count == (0 if failure in ("db_before_dispatch", "shutdown") else 1)
    assert "private" not in caplog.text and "provider.invalid" not in document.failure_reason
    ingestion.process_document(did, rid)
    assert provider.call_count <= 1


def test_database_unavailable_or_unacknowledged_claim_never_dispatches(pdf_pg, provider, monkeypatch):
    did, rid = admit(pdf_pg)
    def fail(_):
        raise SQLAlchemyError("unacknowledged commit")
    event.listen(Session, "after_commit", fail)
    try:
        ingestion.process_document(did, rid)
    finally:
        event.remove(Session, "after_commit", fail)
    assert provider.call_count == 0 and saved(pdf_pg, did, rid)[1].state == "RUNNING"
    monkeypatch.setattr(ingestion, "SessionLocal", Mock(side_effect=SQLAlchemyError("db down")))
    ingestion.process_document(did, rid)
    assert provider.call_count == 0


def test_midnight_timezone_and_expired_slots(pdf_pg, provider):
    midnight = datetime(2026, 10, 5, tzinfo=UTC)
    @event.listens_for(pdf_pg[0], "checkout")
    def non_utc(dbapi, *_):
        with dbapi.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'Asia/Shanghai'")
        dbapi.commit()
    with pdf_pg[1]() as db:
        with protection.processing_transaction(db) as now:
            assert db.scalar(text("SHOW TIMEZONE")) == "Asia/Shanghai"
            assert now.utcoffset() == timedelta(0)
    for _ in range(2):
        did, rid = admit(pdf_pg, now=midnight - timedelta(hours=1))
        # Explicit stale recovery releases slot, not the old daily debit.
        with pdf_pg[1]() as db:
            with protection.processing_transaction(db):
                protection.recover_expired(db, midnight - timedelta(minutes=1))
    with pytest.raises(protection.DocumentAllowanceExceeded):
        admit(pdf_pg, now=midnight - timedelta(microseconds=1))
    assert admit(pdf_pg, now=midnight)[1] is not None


def test_delete_preserves_quota_and_partial_unique_constraints(pdf_pg, provider):
    did, rid = admit(pdf_pg)
    with pdf_pg[1]() as db:
        with protection.processing_transaction(db) as now:
            protection.claim_attempt(db, did, rid, now)
            protection.finish_attempt(db, did, rid, now, reason="local failure")
    with pdf_pg[1].begin() as db:
        db.delete(db.get(Document, did))
    assert saved(pdf_pg, did, rid)[1].document_id is None
    did, rid = admit(pdf_pg)
    with pdf_pg[1]() as db:
        active = db.get(Attempt, rid)
        db.add(Attempt(id=uuid4(), user_id=1, document_id=did, state="RESERVED",
                       created_at=active.created_at, expires_at=active.expires_at))
        with pytest.raises(IntegrityError):
            db.commit()


def test_migration_roundtrip_preserves_old_data_and_interactive_ledger(pdf_pg):
    path = Path(__file__).parents[1] / "alembic/versions/20261004_0004_document_processing_attempts.py"
    spec = importlib.util.spec_from_file_location("pdf_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    def migrate(name):
        with pdf_pg[0].begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                getattr(migration, name)()
    migrate("downgrade")
    with pdf_pg[0].begin() as connection:
        connection.execute(text("INSERT INTO documents (course_id, filename, storage_path, content_hash, file_size, status, created_at, updated_at) VALUES (1, 'old.pdf', 'old.pdf', :hash, 1, 'PROCESSING', clock_timestamp(), clock_timestamp())"), {"hash": "a" * 64})
    qid = QuotaService(pdf_pg[0]).reserve(1, Operation.EXTRACTION, "b" * 64)
    migrate("upgrade")
    with pdf_pg[1]() as db:
        old = db.scalar(select(Document))
        assert old.status == DocumentStatus.FAILED and old.current_attempt_id is None
        assert db.get(AIUsageReservation, qid).state == "RESERVED"
    migrate("downgrade")
    migrate("upgrade")
    with pdf_pg[1]() as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 1
        assert db.get(AIUsageReservation, qid) is not None


@pytest.mark.parametrize("failure", ["quota", "busy", "database", "shutdown"])
def test_authenticated_api_rejection_without_background_dispatch(pdf_pg, client, monkeypatch, failure):
    token = register_user(client, "api-test@example.com")
    headers = {"Authorization": "Bearer " + token["access_token"]}
    cid = client.post("/api/v1/courses", headers=headers, json={"name": "API test"}).json()["id"]
    task = Mock()
    monkeypatch.setattr(routes, "process_document", task)
    endpoint = f"/api/v1/courses/{cid}/documents"
    if failure == "shutdown":
        config.get_settings().ai_paid_operations_enabled = False
    elif failure == "database":
        monkeypatch.setattr(routes, "lock_processing", Mock(side_effect=SQLAlchemyError("private db")))
    else:
        if failure == "quota":
            config.get_settings().document_global_daily_limit = 1
        first = client.post(endpoint, headers=headers, files={"file": ("one.pdf", make_pdf(["One"]), "application/pdf")})
        assert first.status_code == 202
        if failure == "quota":
            with pdf_pg[1]() as db:
                with protection.processing_transaction(db) as now:
                    document = db.get(Document, first.json()["id"])
                    protection.claim_attempt(db, document.id, document.current_attempt_id, now)
                    protection.finish_attempt(db, document.id, document.current_attempt_id, now, reason="failure")
    previous = task.call_count
    response = client.post(endpoint, headers=headers, files={"file": ("two.pdf", make_pdf(["Two"]), "application/pdf")})
    assert response.status_code == (429 if failure in ("quota", "busy") else 503)
    if response.status_code == 429:
        assert int(response.headers["Retry-After"]) >= 1
    assert task.call_count == previous and "private" not in response.text
    assert client.post(endpoint, files={"file": ("no-auth.pdf", make_pdf(["No auth"]), "application/pdf")}).status_code == 401


def test_retry_api_race_gets_one_attempt(pdf_pg, client, monkeypatch):
    token = register_user(client, "retry-api@example.com")
    headers = {"Authorization": "Bearer " + token["access_token"]}
    cid = client.post("/api/v1/courses", headers=headers, json={"name": "Retry"}).json()["id"]
    with pdf_pg[1].begin() as db:
        source = ingestion.resolve_storage_path("retry-api.pdf")
        source.write_bytes(make_pdf(["Retry"]))
        document = Document(course_id=cid, filename=source.name, storage_path=source.name,
                            content_hash="a" * 64, file_size=source.stat().st_size, status=DocumentStatus.FAILED)
        db.add(document)
        db.flush()
        did = document.id
    task = Mock()
    monkeypatch.setattr(routes, "process_document", task)
    barrier = Barrier(2)
    def retry():
        barrier.wait(timeout=5)
        return client.post(f"/api/v1/documents/{did}/retry", headers=headers).status_code
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(retry) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [202, 409]
    assert task.call_count == 1
    with pdf_pg[1]() as db:
        assert db.scalar(select(func.count()).select_from(Attempt)) == 1


def test_real_embedding_sdk_gets_zero_retries(pdf_pg, monkeypatch):
    from types import SimpleNamespace
    sdk = Mock()
    sdk.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.1] * 1024)])
    factory = Mock(return_value=sdk)
    monkeypatch.setattr(embedding, "OpenAI", factory)
    did, rid = admit(pdf_pg)
    ingestion.process_document(did, rid)
    assert saved(pdf_pg, did, rid)[0].status == DocumentStatus.READY
    assert factory.call_args.kwargs["max_retries"] == 0
    sdk.embeddings.create.assert_called_once_with(model="text-embedding-v4", input=["Embedding content"], dimensions=1024)


@pytest.mark.parametrize("cause", ["shutdown", "lost_execution_connection"])
def test_between_batches_stops_without_refund(pdf_pg, provider, cause):
    config.get_settings().embedding_batch_size = 1
    did, rid = admit(pdf_pg, payload=make_pdf(["One", "Two"]))
    original = provider.side_effect
    def first(*args, **kwargs):
        result = original(*args, **kwargs)
        if cause == "shutdown":
            config.get_settings().ai_paid_operations_enabled = False
        else:
            # Only terminate this test Document's disposable execution session.
            with pdf_pg[0].begin() as connection:
                assert connection.scalar(text("SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype='advisory' AND classid=117948 AND objid=:did AND granted"), {"did": did})
        return result
    provider.side_effect = first
    ingestion.process_document(did, rid)
    document, attempt, chunks = saved(pdf_pg, did, rid)
    assert document.status == DocumentStatus.FAILED and attempt.state == "FAILED"
    assert attempt.dispatched_at is not None and not chunks and provider.call_count == 1


@pytest.mark.parametrize("commit_event", ["before_commit", "after_commit"])
def test_unacknowledged_admission_never_schedules_paid_work(pdf_pg, client, monkeypatch, commit_event):
    token = register_user(client, "commit-api@example.com")
    headers = {"Authorization": "Bearer " + token["access_token"]}
    cid = client.post("/api/v1/courses", headers=headers, json={"name": "Commit"}).json()["id"]
    task = Mock()
    monkeypatch.setattr(routes, "process_document", task)
    def fail(_):
        raise SQLAlchemyError("private commit acknowledgement")
    event.listen(Session, commit_event, fail)
    try:
        response = client.post(f"/api/v1/courses/{cid}/documents", headers=headers,
                               files={"file": ("commit.pdf", make_pdf(["Commit"]), "application/pdf")})
    finally:
        event.remove(Session, commit_event, fail)
    assert response.status_code == 503 and task.call_count == 0
    with pdf_pg[1]() as db:
        assert db.scalar(select(func.count()).select_from(Attempt)) == (1 if commit_event == "after_commit" else 0)


@pytest.mark.parametrize("field", ["document_max_pages", "document_max_text_chars", "document_max_chunks", "document_user_daily_limit", "document_global_daily_limit"])
def test_settings_do_not_allow_unlimited_zero_bounds(field):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        config.Settings(_env_file=None, database_url="postgresql+psycopg://unused/test",
                        jwt_secret="mock-only-secret-at-least-thirty-two-characters", **{field: 0})


@pytest.mark.parametrize("commit_event", ["before_commit", "after_commit"])
def test_unacknowledged_dispatch_commit_sends_zero_batches(pdf_pg, provider, monkeypatch, commit_event):
    did, rid = admit(pdf_pg)
    original = ingestion.dispatch_batch
    triggered = False
    def fail_once(_):
        nonlocal triggered
        if not triggered:
            triggered = True
            raise SQLAlchemyError("private dispatch acknowledgement")
    def dispatch(*args):
        original(*args)
        event.listen(Session, commit_event, fail_once)
    monkeypatch.setattr(ingestion, "dispatch_batch", dispatch)
    try:
        ingestion.process_document(did, rid)
    finally:
        event.remove(Session, commit_event, fail_once)
    document, attempt, chunks = saved(pdf_pg, did, rid)
    assert document.status == DocumentStatus.FAILED and not chunks and provider.call_count == 0
    assert attempt.state == ("FAILED" if commit_event == "after_commit" else "CANCELLED")
