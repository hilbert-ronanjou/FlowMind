"""JWT -> real disposable PostgreSQL admission -> mock SDK dispatch regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, InternalServerError
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import config
from app.main import app
from app.models.ai_usage_reservation import Operation, ReservationState as State
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services import embedding
from app.services.ai import extractor
from app.services.ai_guard import get_quota_service
from app.services.ai_quota import QuotaLimits, QuotaService, QuotaUnavailable
from app.services.knowledge import grounded_answer
from app.api.routes import knowledge
from app.services.knowledge.retrieval import RetrievedChunk
from conftest import TestingSession, register_user
from test_ai_quota_postgres import pg, T, rows  # Reuse unchanged safety guard/fixture.


@pytest.fixture
def real_quota(pg):
    service = QuotaService(pg[0], clock=lambda _: T,
                           enabled=lambda: config.get_settings().ai_paid_operations_enabled)
    app.dependency_overrides[get_quota_service] = lambda: service
    return service


@pytest.fixture
def sdk(monkeypatch):
    clients = {name: Mock() for name in ("extract", "embed", "answer")}
    clients["extract"].chat.completions.parse.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, parsed={
            "course_name": None, "title": "Task", "deadline": None,
            "priority": "MEDIUM", "tasks": [],
        }))]
    )
    clients["embed"].embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.1] * 1024)]
    )
    clients["answer"].chat.completions.parse.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, parsed={
            "answerable": True, "answer": "Evidence", "used_chunk_ids": [1],
        }))], usage=None,
    )
    for module, name in ((extractor, "extract"), (embedding, "embed"),
                         (grounded_answer, "answer")):
        monkeypatch.setattr(module, "OpenAI", Mock(return_value=clients[name]))
    return clients


@pytest.fixture
def ready_course(client, auth_headers, monkeypatch):
    cid = client.post("/api/v1/courses", headers=auth_headers,
                      json={"name": "Quota course"}).json()["id"]
    with TestingSession() as db:
        document = Document(course_id=cid, filename="test.pdf", storage_path="test.pdf",
                            content_hash="a" * 64, file_size=1, status=DocumentStatus.READY)
        db.add(document)
        db.commit()
        did = document.id
    candidate = RetrievedChunk(
        chunk=DocumentChunk(id=1, document_id=did, chunk_index=0, page_number=1,
                            content="Evidence", embedding=[0.1] * 1024),
        document_id=did, filename="test.pdf", distance=0.0,
    )
    monkeypatch.setattr(knowledge, "search_ready_chunk_candidates", lambda *_a, **_k: [candidate])
    return cid


def post(client, headers, course=None, key=None, text="notice"):
    headers = dict(headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        "/api/v1/ai/extract" if course is None else f"/api/v1/courses/{course}/knowledge/query",
        headers=headers, json={"text": text} if course is None else {"question": text},
    )


def calls(sdk):
    return (sdk["extract"].chat.completions.parse.call_count,
            sdk["embed"].embeddings.create.call_count,
            sdk["answer"].chat.completions.parse.call_count)


def test_committed_admission_precedes_each_sdk_and_shared_five_limit(
    client, auth_headers, real_quota, pg, sdk, ready_course,
):
    def assert_dispatched(*_args, **_kwargs):
        # Independent connection sees committed state; try-lock proves no held lock.
        from sqlalchemy import text
        with pg[0].begin() as connection:
            assert connection.scalar(text("SELECT pg_try_advisory_xact_lock(117947,4)"))
        assert any(row.state == State.DISPATCHED for row in rows(pg))
    for endpoint in (sdk["extract"].chat.completions.parse,
                     sdk["embed"].embeddings.create,
                     sdk["answer"].chat.completions.parse):
        endpoint.side_effect = assert_dispatched
    # Preserve actual parsed mock returns while checking committed state.
    for endpoint in (sdk["extract"].chat.completions.parse,
                     sdk["embed"].embeddings.create,
                     sdk["answer"].chat.completions.parse):
        result = endpoint.return_value
        endpoint.side_effect = lambda *a, _result=result, **k: (assert_dispatched(*a, **k), _result)[1]
    for n in range(5):
        assert post(client, auth_headers, ready_course if n % 2 else None).status_code == 200
    before = calls(sdk)
    rejected = post(client, auth_headers, ready_course)
    assert rejected.status_code == 429 and rejected.headers["Retry-After"] == "60"
    assert calls(sdk) == before == (3, 2, 2)
    assert len(rows(pg)) == 5 and all(row.state == State.SUCCEEDED for row in rows(pg))
    assert extractor.OpenAI.call_args.kwargs["max_retries"] == 0
    assert embedding.OpenAI.call_args.kwargs["max_retries"] == 0
    assert grounded_answer.OpenAI.call_args.kwargs["max_retries"] == 0
    for name in ("extract", "answer"):
        assert sdk[name].chat.completions.parse.call_args.kwargs["max_tokens"] == 4096


@pytest.mark.parametrize("limit", ["user_daily", "global_daily"])
def test_actual_daily_defaults_reject_without_calls(client, auth_headers, real_quota, pg, sdk, limit):
    real_quota.limits = QuotaLimits(user_window=200, user_daily=200 if limit == "global_daily" else 20)
    count = 100 if limit == "global_daily" else 20
    for _ in range(count):
        assert post(client, auth_headers).status_code == 200
    assert post(client, auth_headers).status_code == 429
    assert calls(sdk) == (count, 0, 0) and len(rows(pg)) == count


@pytest.mark.parametrize("course_query", [False, True])
def test_duplicate_key_no_second_call_or_refund(
    client, auth_headers, real_quota, pg, sdk, ready_course, course_query,
):
    course = ready_course if course_query else None
    assert post(client, auth_headers, course, key="same").status_code == 200
    for text in ("notice", "changed"):
        assert post(client, auth_headers, course, key="same", text=text).status_code == 409
    real_quota.clock = lambda _: T + timedelta(days=1)
    assert post(client, auth_headers, course, key="same").status_code == 409
    assert len(rows(pg)) == 1
    assert calls(sdk) == ((0, 1, 1) if course_query else (1, 0, 0))


@pytest.mark.parametrize("key", ["", "x" * 129, "非ASCII"])
def test_invalid_key_no_admission(client, auth_headers, real_quota, pg, sdk, key):
    # ASGI clients require HTTP header encodability; invoke the dependency policy
    # directly for a non-ASCII header that the transport would already reject.
    if not key.isascii():
        from fastapi import HTTPException
        from app.services.ai_guard import interactive_operation
        with pytest.raises(HTTPException) as error:
            with interactive_operation(real_quota, 1, Operation.EXTRACTION, {"text": "notice"}, key):
                pytest.fail("invalid key dispatched")
        assert error.value.status_code == 422
    else:
        assert post(client, auth_headers, key=key).status_code == 422
    assert not rows(pg) and calls(sdk) == (0, 0, 0)


def test_auth_ownership_bounds_and_no_ready_are_free(
    client, auth_headers, real_quota, pg, sdk, ready_course,
):
    assert post(client, {}).status_code == 401
    assert post(client, {"Authorization": "Bearer invalid"}).status_code == 401
    assert post(client, auth_headers, text="x" * 10001).status_code == 422
    other = register_user(client, "other@example.com")
    other_headers = {"Authorization": "Bearer " + other["access_token"]}
    assert post(client, other_headers, ready_course).status_code == 404
    empty = client.post("/api/v1/courses", headers=auth_headers,
                        json={"name": "empty"}).json()["id"]
    assert post(client, auth_headers, empty).status_code == 200
    assert not rows(pg) and calls(sdk) == (0, 0, 0)


@pytest.mark.parametrize("stage", ["extract", "embed", "answer"])
@pytest.mark.parametrize("failure", ["timeout", "connection", "known", "invalid"])
def test_provider_failures_charge_and_preserve_wrapped_uncertainty(
    client, auth_headers, real_quota, pg, sdk, ready_course, stage, failure,
):
    request = httpx.Request("POST", "https://provider.invalid/v1")
    endpoint = sdk[stage].embeddings.create if stage == "embed" else sdk[stage].chat.completions.parse
    if failure == "invalid":
        if stage == "embed":
            endpoint.return_value = SimpleNamespace(data=[])
        else:
            endpoint.return_value = SimpleNamespace(choices=[], usage=None)
    else:
        endpoint.side_effect = {
            "timeout": APITimeoutError(request=request),
            "connection": APIConnectionError(request=request, message="private diagnostic"),
            "known": InternalServerError("private diagnostic", response=httpx.Response(500, request=request), body=None),
        }[failure]
    response = post(client, auth_headers, None if stage == "extract" else ready_course)
    assert response.status_code in (502, 503) and "private" not in response.text
    assert len(rows(pg)) == 1
    assert rows(pg)[0].state == (State.UNCERTAIN if failure in ("timeout", "connection") else State.FAILED)
    assert endpoint.call_count == 1
    if stage == "embed":
        assert sdk["answer"].chat.completions.parse.call_count == 0


@pytest.mark.parametrize("failure", ["overflow", "retrieval", "shutdown"])
def test_post_embedding_failures_never_cancel_or_send_qwen(
    client, auth_headers, real_quota, pg, sdk, ready_course, monkeypatch, failure,
):
    if failure == "overflow":
        config.get_settings().ai_rag_max_prompt_chars = 1
    elif failure == "retrieval":
        database_error = SQLAlchemyError("private SQL")
        database_error.__cause__ = RuntimeError("private DBAPI detail")
        monkeypatch.setattr(knowledge, "search_ready_chunk_candidates", Mock(side_effect=database_error))
    else:
        def disable(*_args, **_kwargs):
            config.get_settings().ai_paid_operations_enabled = False
            return []
        original = knowledge.search_ready_chunk_candidates
        monkeypatch.setattr(knowledge, "search_ready_chunk_candidates",
                            lambda *a, **k: (disable(), original(*a, **k))[1])
    response = post(client, auth_headers, ready_course)
    assert response.status_code == {"overflow": 502, "retrieval": 500, "shutdown": 503}[failure]
    assert calls(sdk) == (0, 1, 0)
    assert rows(pg)[0].state == State.FAILED and rows(pg)[0].dispatched_at is not None


@pytest.mark.parametrize("course_query", [False, True])
@pytest.mark.parametrize("commit_event", ["before_commit", "after_commit"])
def test_success_survives_failed_settlement_without_second_call(
    client, auth_headers, real_quota, pg, sdk, ready_course, course_query, commit_event, caplog,
):
    def fail(_):
        raise SQLAlchemyError("private secret SQL diagnostic")
    last = sdk["answer" if course_query else "extract"].chat.completions.parse
    result = last.return_value
    def succeed_and_break(*_a, **_k):
        event.listen(Session, commit_event, fail)
        return result
    last.side_effect = succeed_and_break
    try:
        response = post(client, auth_headers, ready_course if course_query else None)
    finally:
        event.remove(Session, commit_event, fail)
    assert response.status_code == 200
    assert (response.json()["answer"] == "Evidence" if course_query else response.json()["title"] == "Task")
    assert rows(pg)[0].state in (State.DISPATCHED, State.SUCCEEDED)
    assert "ai_quota_settlement_failed" in [getattr(r, "event", None) for r in caplog.records]
    assert "private secret" not in caplog.text
    assert calls(sdk) == ((0, 1, 1) if course_query else (1, 0, 0))


@pytest.mark.parametrize("stage", ["extract", "embed", "answer"])
@pytest.mark.parametrize("failure", ["known", "timeout"])
def test_original_failure_survives_failed_settlement_without_second_call(
    client, auth_headers, sdk, ready_course, stage, failure, caplog,
):
    # Explicitly mocked admission, but run the real best-effort settlement handler.
    # PostgreSQL transaction/commit regressions remain in the dedicated PG tests.
    quota = app.dependency_overrides[get_quota_service]()
    quota.settle.side_effect = QuotaUnavailable()
    quota.settle_best_effort.side_effect = lambda rid, state: QuotaService.settle_best_effort(quota, rid, state)
    request = httpx.Request("POST", "https://provider.invalid/v1")
    provider_error = (
        APITimeoutError(request=request) if failure == "timeout" else
        InternalServerError("private provider diagnostic",
                            response=httpx.Response(500, request=request), body=None)
    )
    endpoint = sdk[stage].embeddings.create if stage == "embed" else sdk[stage].chat.completions.parse
    endpoint.side_effect = provider_error
    response = post(client, auth_headers, None if stage == "extract" else ready_course)

    temporary = failure == "timeout" or stage == "embed"
    expected_detail = {
        "extract": "AI extraction service is temporarily unavailable" if temporary else "AI provider request failed",
        "embed": "Knowledge embedding service is temporarily unavailable",
        "answer": "Knowledge answer service is temporarily unavailable" if temporary else "Knowledge answer provider request failed",
    }[stage]
    assert response.status_code == (503 if temporary else 502)
    assert response.json() == {"detail": expected_detail}
    expected_state = State.UNCERTAIN if failure == "timeout" else State.FAILED
    quota.reserve.assert_called_once()
    quota.mark_dispatched.assert_called_once()
    rid = quota.mark_dispatched.call_args.args[0]
    quota.settle.assert_called_once_with(rid, expected_state)
    quota.cancel.assert_not_called()
    settlement_logs = [r for r in caplog.records if getattr(r, "event", None) == "ai_quota_settlement_failed"]
    assert len(settlement_logs) == 1 and "private" not in caplog.text + response.text
    expected_calls = {"extract": (1, 0, 0), "embed": (0, 1, 0), "answer": (0, 1, 1)}[stage]
    assert calls(sdk) == expected_calls


@pytest.mark.parametrize("stage", ["reserve", "dispatch"])
@pytest.mark.parametrize("commit_event", ["before_commit", "after_commit"])
def test_uncertain_quota_commit_never_calls_provider(
    client, auth_headers, real_quota, pg, sdk, monkeypatch, stage, commit_event,
):
    def fail(_):
        raise SQLAlchemyError("private commit")
    if stage == "dispatch":
        original = real_quota.mark_dispatched
        def mark(rid):
            event.listen(Session, commit_event, fail)
            return original(rid)
        monkeypatch.setattr(real_quota, "mark_dispatched", mark)
    else:
        event.listen(Session, commit_event, fail)
    try:
        assert post(client, auth_headers).status_code == 503
    finally:
        event.remove(Session, commit_event, fail)
    assert calls(sdk) == (0, 0, 0)


def test_cancel_before_dispatch_and_global_shutdown(
    client, auth_headers, real_quota, pg, sdk, monkeypatch,
):
    original = real_quota.reserve
    def reserve(*a, **k):
        rid = original(*a, **k)
        config.get_settings().ai_paid_operations_enabled = False
        return rid
    monkeypatch.setattr(real_quota, "reserve", reserve)
    assert post(client, auth_headers).status_code == 503
    assert rows(pg)[0].state == State.CANCELLED
    assert post(client, auth_headers).status_code == 503
    from app.services.ai_guard import PaidAIDisabled
    with pytest.raises(PaidAIDisabled):
        embedding.embed_texts(["PDF content"])
    assert calls(sdk) == (0, 0, 0) and len(rows(pg)) == 1


@pytest.mark.parametrize("duplicate", [False, True])
def test_concurrent_api_requests_only_one_paid_call(
    client, auth_headers, real_quota, pg, sdk, duplicate,
):
    real_quota.limits = QuotaLimits(user_window=1)
    barrier = Barrier(2)
    def request():
        barrier.wait(timeout=5)
        return post(client, auth_headers, key="same" if duplicate else None).status_code
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(request) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [200, 409 if duplicate else 429]
    assert calls(sdk) == (1, 0, 0) and len(rows(pg)) == 1


def test_pdf_embedding_retry_default_is_unchanged(sdk):
    assert embedding.embed_texts(["PDF content"])
    assert embedding.OpenAI.call_args.kwargs["max_retries"] == 1


def test_configuration_failure_precedes_reservation(client, auth_headers, real_quota, pg, sdk):
    config.get_settings().dashscope_api_key = None
    assert post(client, auth_headers).status_code == 503
    assert not rows(pg) and calls(sdk) == (0, 0, 0)


@pytest.mark.parametrize("no_answer", ["empty_retrieval", "unanswerable"])
def test_no_answer_after_embedding_still_counts_one(
    client, auth_headers, real_quota, pg, sdk, ready_course, monkeypatch, no_answer,
):
    if no_answer == "empty_retrieval":
        monkeypatch.setattr(knowledge, "search_ready_chunk_candidates", lambda *_a, **_k: [])
    else:
        sdk["answer"].chat.completions.parse.return_value.choices[0].message.parsed = {
            "answerable": False, "answer": "", "used_chunk_ids": [],
        }
    response = post(client, auth_headers, ready_course)
    assert response.status_code == 200 and response.json()["answerable"] is False
    assert len(rows(pg)) == 1 and rows(pg)[0].state == State.SUCCEEDED
    assert calls(sdk) == (0, 1, 0 if no_answer == "empty_retrieval" else 1)


@pytest.mark.parametrize("stage", ["extract", "answer"])
def test_generation_length_limit_failure_is_charged_safe_502(
    client, auth_headers, real_quota, pg, sdk, ready_course, stage,
):
    from openai import LengthFinishReasonError
    sdk[stage].chat.completions.parse.side_effect = LengthFinishReasonError(completion=Mock())
    response = post(client, auth_headers, None if stage == "extract" else ready_course)
    assert response.status_code == 502 and rows(pg)[0].state == State.FAILED
    assert sdk[stage].chat.completions.parse.call_count == 1


def test_global_limit_and_user_identity_across_tokens_and_users(
    client, auth_headers, real_quota, pg, sdk,
):
    second = register_user(client, "quota-second@example.com")
    second_headers = {"Authorization": "Bearer " + second["access_token"]}
    real_quota.limits = QuotaLimits(global_daily=1)
    assert post(client, auth_headers).status_code == 200
    assert post(client, second_headers).status_code == 429
    login = client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "Password123!"})
    assert login.status_code == 200
    assert post(client, {"Authorization": "Bearer " + login.json()["access_token"]}).status_code == 429
    assert calls(sdk) == (1, 0, 0) and rows(pg)[0].user_id == 1


def test_full_postgres_jwt_ownership_retrieval_and_provider_flow(client, pg, sdk, monkeypatch):
    """No SQLite business data or mocked retrieval in this end-to-end case."""
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from app.database import Base, get_db

    pg[1]("downgrade")
    with pg[0].begin() as conn:
        conn.execute(text("DROP TABLE users"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        schema = conn.scalar(text("SELECT current_schema()"))
    @event.listens_for(pg[0], "checkout")
    def configure(dbapi, *_):
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}", public')
        dbapi.commit()
    # Do not let checkfirst find similarly named tables in public via search_path.
    Base.metadata.create_all(pg[0], checkfirst=False)
    with pg[0].connect() as conn:
        assert conn.scalar(text("SELECT current_schema()")) == schema
        actual_tables = set(conn.scalars(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = :schema"
        ), {"schema": schema}))
        assert set(Base.metadata.tables).issubset(actual_tables)
    sessions = sessionmaker(bind=pg[0], expire_on_commit=False)
    request_sessions = []
    def real_db():
        with sessions() as session:
            request_sessions.append(session)
            yield session
    service = QuotaService(pg[0], clock=lambda _: T)
    monkeypatch.setitem(app.dependency_overrides, get_db, real_db)
    monkeypatch.setitem(app.dependency_overrides, get_quota_service, lambda: service)
    registered = register_user(client, "full-postgres@example.com")
    headers = {"Authorization": "Bearer " + registered["access_token"]}
    cid = client.post("/api/v1/courses", headers=headers, json={"name": "Full PostgreSQL"}).json()["id"]
    with sessions() as db:
        document = Document(course_id=cid, filename="pg.pdf", storage_path="pg.pdf",
                            content_hash="b" * 64, file_size=1, status=DocumentStatus.READY)
        document.chunks = [DocumentChunk(chunk_index=0, page_number=1, content="Evidence",
                                        embedding=[0.1] * 1024)]
        db.add(document)
        db.commit()
        did = document.id
    for endpoint in (sdk["extract"].chat.completions.parse,
                     sdk["embed"].embeddings.create, sdk["answer"].chat.completions.parse):
        result = endpoint.return_value
        def checked(*_a, _result=result, **_k):
            assert all(not session.in_transaction() for session in request_sessions)
            with pg[0].begin() as connection:
                assert connection.scalar(text("SELECT pg_try_advisory_xact_lock(117947,4)"))
            return _result
        endpoint.side_effect = checked
    assert post(client, headers).status_code == 200
    response = post(client, headers, cid)
    assert response.status_code == 200 and response.json()["citations"][0]["document_id"] == did
    assert len(rows(pg)) == 2 and calls(sdk) == (1, 1, 1)
    foreign = register_user(client, "full-pg-foreign@example.com")
    foreign_headers = {"Authorization": "Bearer " + foreign["access_token"]}
    assert post(client, foreign_headers, cid).status_code == 404
    assert len(rows(pg)) == 2 and calls(sdk) == (1, 1, 1)


def test_exact_extraction_boundary_and_runtime_output_setting(
    client, auth_headers, real_quota, pg, sdk,
):
    config.get_settings().ai_qwen_max_output_tokens = 2048
    assert post(client, auth_headers, text="x" * 10000).status_code == 200
    assert sdk["extract"].chat.completions.parse.call_args.kwargs["max_tokens"] == 2048
    assert len(rows(pg)) == 1


@pytest.mark.parametrize("field", [
    "ai_interactive_user_window_limit", "ai_interactive_window_seconds",
    "ai_interactive_user_daily_limit", "ai_interactive_global_daily_limit",
    "ai_extraction_max_input_chars", "ai_rag_max_prompt_chars", "ai_qwen_max_output_tokens",
])
def test_runtime_limits_validate_positive(field):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        config.Settings(_env_file=None, database_url="sqlite://",
                        jwt_secret="test-only-secret-at-least-32-characters", **{field: 0})


@pytest.mark.parametrize("stage", ["extract", "embed", "answer"])
def test_real_sdk_transport_has_zero_automatic_retries(
    client, auth_headers, real_quota, pg, sdk, ready_course, monkeypatch, stage,
):
    from openai import OpenAI
    attempts = []
    clients = []
    def fail(request):
        attempts.append(request)
        return httpx.Response(500, json={"error": {"message": "mock provider failure"}}, request=request)
    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        provider = OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(fail)))
        clients.append(provider)
        return provider
    module = {"extract": extractor, "embed": embedding, "answer": grounded_answer}[stage]
    monkeypatch.setattr(module, "OpenAI", factory)
    try:
        response = post(client, auth_headers, None if stage == "extract" else ready_course)
    finally:
        for provider in clients:
            provider.close()
    assert response.status_code in (502, 503)
    assert len(attempts) == 1 and rows(pg)[0].state == State.FAILED


def test_unknown_wrapped_provider_error_remains_uncertain():
    from fastapi import HTTPException
    from app.services.ai_guard import failure_state
    error = HTTPException(503, "safe response")
    error.__cause__ = RuntimeError("unknown provider failure")
    assert failure_state(error) == State.UNCERTAIN


def test_exact_assembled_prompt_boundary_and_output_limit(sdk):
    settings = config.get_settings()
    question = "question"
    size = len(grounded_answer.build_grounded_system_prompt()) + len(
        grounded_answer.build_grounded_user_prompt(question, [])
    )
    settings.ai_rag_max_prompt_chars = size
    settings.ai_qwen_max_output_tokens = 2048
    grounded_answer.grounded_answer_with_client(sdk["answer"], "mock-model", question, [])
    assert sdk["answer"].chat.completions.parse.call_args.kwargs["max_tokens"] == 2048
    settings.ai_rag_max_prompt_chars = size - 1
    with pytest.raises(grounded_answer.PromptOverflowError):
        grounded_answer.grounded_answer_with_client(sdk["answer"], "mock-model", question, [])
    assert calls(sdk) == (0, 0, 1)
