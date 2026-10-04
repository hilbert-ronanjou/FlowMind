import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import knowledge as knowledge_routes
from app.core.observability import HTTP_REQUESTS, REGISTRY, SafeJSONFormatter
from app.main import app
from app.schemas.knowledge import no_answer_response
from app.services.embedding import embed_texts_with_client
from app.services.knowledge import ingestion


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_request_ids_propagate_or_fall_back(client: TestClient):
    edge_id = "edge_" + uuid4().hex
    propagated = client.get("/health/live", headers={"X-Request-ID": edge_id})
    generated = client.get("/health/live")
    rejected = client.get("/health/live", headers={"X-Request-ID": "unsafe value\n"})

    assert propagated.headers["X-Request-ID"] == edge_id
    assert len(generated.headers["X-Request-ID"]) == 32
    assert generated.headers["X-Request-ID"] != edge_id
    assert rejected.headers["X-Request-ID"] != "unsafe value\n"


def test_concurrent_requests_keep_their_own_ids(client: TestClient):
    ids = ["edge_" + uuid4().hex for _ in range(12)]
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeJSONFormatter("test"))
    logger = logging.getLogger("app.core.observability")
    logger.addHandler(handler)

    def call(edge_id: str) -> str:
        return client.get("/api/v1/courses", headers={"X-Request-ID": edge_id}).headers[
            "X-Request-ID"
        ]

    try:
        with ThreadPoolExecutor(max_workers=6) as workers:
            assert list(workers.map(call, ids)) == ids
    finally:
        logger.removeHandler(handler)
    logged_ids = {
        json.loads(line)["request_id"]
        for line in output.getvalue().splitlines()
        if json.loads(line)["event"] == "http_request_completed"
    }
    assert logged_ids == set(ids)


def test_http_log_is_structured_and_excludes_body_and_secrets(client: TestClient):
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeJSONFormatter("test"))
    logger = logging.getLogger("app.core.observability")
    logger.addHandler(handler)
    try:
        edge_id = "edge_" + uuid4().hex
        response = client.post(
            "/api/v1/ai/extract",
            headers={"X-Request-ID": edge_id, "Authorization": "Bearer private-token"},
            json={"text": "private-prompt-sentinel", "password": "private-password"},
        )
    finally:
        logger.removeHandler(handler)

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    completed = next(item for item in events if item["event"] == "http_request_completed")
    assert completed.keys() >= {
        "timestamp", "level", "service", "environment", "event", "request_id",
        "method", "route", "status_code", "duration_ms",
    }
    assert completed["request_id"] == edge_id
    assert completed["route"] == "/api/v1/ai/extract"
    assert completed["status_code"] == response.status_code
    assert "private-token" not in output.getvalue()
    assert "private-prompt-sentinel" not in output.getvalue()
    assert "private-password" not in output.getvalue()


def test_request_id_is_available_to_sync_application_logging(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeJSONFormatter("test"))
    logger = logging.getLogger("app.main")
    logger.addHandler(handler)

    def fail_connect():
        raise RuntimeError("private-database-diagnostic")

    monkeypatch.setattr("app.main.engine.connect", fail_connect)
    try:
        response = client.get("/health/ready")
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 503
    event = next(json.loads(line) for line in output.getvalue().splitlines())
    assert event["event"] == "database_readiness_failed"
    assert event["request_id"] == response.headers["X-Request-ID"]
    assert "private-database-diagnostic" not in output.getvalue()


def test_formatter_discards_arbitrary_log_message_and_exception_text():
    secret = "database-password-private-sentinel"
    record = logging.LogRecord(
        "app.test", logging.ERROR, __file__, 1, secret, (), RuntimeError(secret)
    )
    record.event = "document_processing_failed"
    rendered = SafeJSONFormatter("test").format(record)

    assert json.loads(rendered)["event"] == "document_processing_failed"
    assert secret not in rendered


def test_unhandled_failure_keeps_request_id_and_hides_details(
    monkeypatch: pytest.MonkeyPatch,
):
    def fail():
        raise RuntimeError("internal-private-diagnostic")

    monkeypatch.setattr("app.main.database_is_ready", fail)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeJSONFormatter("test"))
    logger = logging.getLogger("app.core.observability")
    logger.addHandler(handler)
    try:
        with TestClient(app, raise_server_exceptions=False) as direct_client:
            response = direct_client.get("/health/ready")
    finally:
        logger.removeHandler(handler)
    assert response.status_code == 500
    assert len(response.headers["X-Request-ID"]) == 32
    assert "internal-private-diagnostic" not in response.text
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert any(event["event"] == "http_request_failed" for event in events)
    assert not any(event["event"] == "http_request_completed" for event in events)


def test_metrics_failure_cannot_break_normal_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(HTTP_REQUESTS, "labels", lambda *_: (_ for _ in ()).throw(RuntimeError("metrics unavailable")))
    response = client.get("/api/v1/courses")
    assert response.status_code == 401


def test_health_routes_exclude_generic_http_logs_and_metrics(client: TestClient):
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeJSONFormatter("test"))
    logger = logging.getLogger("app.core.observability")
    logger.addHandler(handler)
    paths = ("/health", "/health/live", "/health/ready")
    before = {
        path: (
            sample("http_requests_total", {"method": "GET", "route": path, "status_class": "2xx"}),
            sample("http_request_duration_seconds_count", {"method": "GET", "route": path}),
        )
        for path in paths
    }
    try:
        responses = {path: client.get(path) for path in paths}
    finally:
        logger.removeHandler(handler)

    assert responses["/health"].json() == {"status": "ok"}
    assert responses["/health/live"].json() == {"status": "live"}
    assert responses["/health/ready"].json() == {"status": "ready"}
    assert all(response.status_code == 200 for response in responses.values())
    for path in paths:
        assert len(responses[path].headers["X-Request-ID"]) == 32
        assert before[path] == (
            sample("http_requests_total", {"method": "GET", "route": path, "status_class": "2xx"}),
            sample("http_request_duration_seconds_count", {"method": "GET", "route": path}),
        )
    assert not any(
        json.loads(line)["event"] == "http_request_completed"
        for line in output.getvalue().splitlines()
    )


def test_failing_readiness_excludes_generic_http_noise(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("app.main.database_is_ready", lambda: False)
    labels = {"method": "GET", "route": "/health/ready"}
    before_requests = sample("http_requests_total", {**labels, "status_class": "5xx"})
    before_duration = sample("http_request_duration_seconds_count", labels)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeJSONFormatter("test"))
    logger = logging.getLogger("app.core.observability")
    logger.addHandler(handler)
    try:
        response = client.get("/health/ready")
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert sample("http_requests_total", {**labels, "status_class": "5xx"}) == before_requests
    assert sample("http_request_duration_seconds_count", labels) == before_duration
    assert not any(
        json.loads(line)["event"] == "http_request_completed"
        for line in output.getvalue().splitlines()
    )


@pytest.mark.parametrize(
    ("path", "route", "status_code"),
    [
        ("/api/v1/courses", "/api/v1/courses", 401),
        ("/health/ready/extra", "unmatched", 404),
    ],
)
def test_non_health_requests_remain_observable(
    client: TestClient, path: str, route: str, status_code: int
):
    labels = {"method": "GET", "route": route}
    counter_labels = {**labels, "status_class": "4xx"}
    before_requests = sample("http_requests_total", counter_labels)
    before_duration = sample("http_request_duration_seconds_count", labels)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeJSONFormatter("test"))
    logger = logging.getLogger("app.core.observability")
    logger.addHandler(handler)
    try:
        response = client.get(path)
    finally:
        logger.removeHandler(handler)

    assert response.status_code == status_code
    assert sample("http_requests_total", counter_labels) == before_requests + 1
    assert sample("http_request_duration_seconds_count", labels) == before_duration + 1
    completed = [
        json.loads(line) for line in output.getvalue().splitlines()
        if json.loads(line)["event"] == "http_request_completed"
    ]
    assert len(completed) == 1
    assert completed[0]["route"] == route
    assert completed[0]["status_code"] == status_code


def test_metrics_are_prometheus_text_with_normalized_labels(client: TestClient):
    route = "/api/v1/courses/{course_id}"
    first = client.get("/api/v1/courses/91")
    second = client.get("/api/v1/courses/92")
    labels = {"method": "GET", "route": route, "status_class": f"{first.status_code // 100}xx"}
    assert second.status_code == first.status_code
    assert sample("http_requests_total", labels) >= 2

    response = client.get("/metrics")
    body = response.text
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "http_requests_total" in body
    assert "http_request_duration_seconds_bucket" in body
    assert 'route="/api/v1/courses/{course_id}"' in body
    assert 'route="/api/v1/courses/91"' not in body
    for forbidden in ("request_id=", "user_id=", "course_id=", "document_id=", "filename="):
        assert forbidden not in body


def test_missing_provider_usage_does_not_create_token_series():
    from app.core.observability import record_ai_tokens

    before = sample("ai_tokens_total", {"operation": "grounded_answer", "direction": "input"})
    record_ai_tokens("grounded_answer", None, None)
    assert sample("ai_tokens_total", {"operation": "grounded_answer", "direction": "input"}) == before

    record_ai_tokens("grounded_answer", 7, 3)
    assert sample("ai_tokens_total", {"operation": "grounded_answer", "direction": "input"}) == before + 7


def test_provider_calls_are_counted_once_for_success_and_failure():
    provider = Mock()
    provider.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.25] * 1024)]
    )
    success = {"operation": "embedding", "outcome": "success"}
    failure = {"operation": "embedding", "outcome": "failure"}
    before_success = sample("ai_requests_total", success)
    before_failure = sample("ai_requests_total", failure)

    embed_texts_with_client(provider, "text-embedding-v4", 1024, ["safe text"])
    provider.embeddings.create.side_effect = RuntimeError("provider-private-message")
    with pytest.raises(RuntimeError):
        embed_texts_with_client(provider, "text-embedding-v4", 1024, ["safe text"])

    assert sample("ai_requests_total", success) == before_success + 1
    assert sample("ai_requests_total", failure) == before_failure + 1


def test_rag_outcomes_are_bounded(monkeypatch: pytest.MonkeyPatch):
    counts = {
        outcome: sample("rag_queries_total", {"outcome": outcome})
        for outcome in ("answerable", "unanswerable", "failure")
    }
    monkeypatch.setattr(
        knowledge_routes, "_execute_query_course_knowledge", lambda *_: no_answer_response()
    )
    result = knowledge_routes.query_course_knowledge(1, None, None, None, None)
    assert result.answerable is False

    monkeypatch.setattr(
        knowledge_routes, "_execute_query_course_knowledge", lambda *_: SimpleNamespace(answerable=True)
    )
    assert knowledge_routes.query_course_knowledge(1, None, None, None, None).answerable is True

    def fail(*_args):
        raise RuntimeError("private-question")

    monkeypatch.setattr(knowledge_routes, "_execute_query_course_knowledge", fail)
    with pytest.raises(RuntimeError):
        knowledge_routes.query_course_knowledge(1, None, None, None, None)

    assert sample("rag_queries_total", {"outcome": "unanswerable"}) == counts["unanswerable"] + 1
    assert sample("rag_queries_total", {"outcome": "answerable"}) == counts["answerable"] + 1
    assert sample("rag_queries_total", {"outcome": "failure"}) == counts["failure"] + 1


def test_document_processing_outcomes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    document = SimpleNamespace(status=ingestion.DocumentStatus.PROCESSING, storage_path="safe.pdf")
    session = Mock()
    session.get.return_value = document
    context = Mock()
    context.__enter__ = Mock(return_value=session)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(ingestion, "SessionLocal", lambda: context)
    source = tmp_path / "safe.pdf"
    source.write_bytes(b"%PDF")
    monkeypatch.setattr(ingestion, "resolve_storage_path", lambda _: source)
    monkeypatch.setattr(
        ingestion, "get_settings", lambda: SimpleNamespace(
            document_chunk_size_chars=1200, document_chunk_overlap_chars=200,
            embedding_batch_size=16,
        ),
    )
    monkeypatch.setattr(ingestion, "extract_pdf_pages", lambda _: [ingestion.PageText(1, "safe")])
    monkeypatch.setattr(ingestion, "build_chunk_drafts", lambda *_, **__: [ingestion.ChunkDraft(1, "safe")])
    monkeypatch.setattr(ingestion, "embed_chunk_drafts", lambda *_, **__: [[0.0] * 1024])
    monkeypatch.setattr(ingestion, "_persist_ready_document", lambda *args: None)
    mark_failed = Mock()
    monkeypatch.setattr(ingestion, "_mark_document_failed", mark_failed)
    success = {"outcome": "success"}
    failure = {"outcome": "failure"}
    before_success = sample("document_processing_total", success)
    before_failure = sample("document_processing_total", failure)

    ingestion.process_document(1)
    monkeypatch.setattr(
        ingestion, "extract_pdf_pages", lambda _: (_ for _ in ()).throw(
            ingestion.DocumentProcessingError("safe failure")
        ),
    )
    ingestion.process_document(1)

    assert sample("document_processing_total", success) == before_success + 1
    assert sample("document_processing_total", failure) == before_failure + 1
    mark_failed.assert_called_once_with(1, "safe failure")
