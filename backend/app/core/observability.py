"""Bounded request diagnostics and single-worker Prometheus measurements."""

import json
import logging
import re
import sys
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import lru_cache
from time import perf_counter
from typing import TypeVar
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.routing import compile_path


request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
EVENT_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")

REGISTRY = CollectorRegistry()
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Completed HTTP requests",
    ("method", "route", "status_class"),
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ("method", "route"),
    registry=REGISTRY,
)
AI_REQUESTS = Counter(
    "ai_requests_total",
    "AI provider calls",
    ("operation", "outcome"),
    registry=REGISTRY,
)
AI_DURATION = Histogram(
    "ai_request_duration_seconds",
    "AI provider call duration",
    ("operation",),
    registry=REGISTRY,
)
AI_TOKENS = Counter(
    "ai_tokens_total",
    "Provider reported token usage",
    ("operation", "direction"),
    registry=REGISTRY,
)
RAG_QUERIES = Counter(
    "rag_queries_total",
    "Course knowledge queries",
    ("outcome",),
    registry=REGISTRY,
)
RAG_DURATION = Histogram(
    "rag_query_duration_seconds", "Course knowledge query duration", registry=REGISTRY
)
DOCUMENT_PROCESSING = Counter(
    "document_processing_total",
    "Document processing attempts",
    ("outcome",),
    registry=REGISTRY,
)
DOCUMENT_DURATION = Histogram(
    "document_processing_duration_seconds", "Document processing duration", registry=REGISTRY
)
T = TypeVar("T")


class SafeJSONFormatter(logging.Formatter):
    """Emit known fields only; exception messages and arbitrary log arguments are excluded."""

    def __init__(self, environment: str):
        super().__init__()
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        if not isinstance(event, str) or not EVENT_PATTERN.fullmatch(event):
            event = "runtime_log"
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": "flowmind-backend",
            "environment": self.environment,
            "event": event,
            "request_id": request_id_context.get(),
        }
        if event == "http_request_completed":
            for name in ("method", "route", "status_code", "duration_ms"):
                payload[name] = getattr(record, name)
        if event in {"ai_request_completed", "ai_request_failed"}:
            operation = getattr(record, "operation", None)
            if operation in {"embedding", "structured_extraction", "grounded_answer"}:
                payload["operation"] = operation
        if event in {
            "rag_query_completed",
            "rag_query_failed",
            "document_processing_completed",
            "document_processing_failed",
        }:
            outcome = getattr(record, "outcome", None)
            if outcome in {"answerable", "unanswerable", "success", "failure"}:
                payload["outcome"] = outcome
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(environment: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeJSONFormatter(environment))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = name != "uvicorn.access"
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _measure(action) -> None:
    """Instrumentation is best-effort and cannot fail an application operation."""
    try:
        action()
    except Exception:
        pass


def record_http(method: str, route: str, status_code: int, seconds: float) -> None:
    method = method if method in METHODS else "OTHER"
    status_class = f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"

    def update() -> None:
        HTTP_REQUESTS.labels(method, route, status_class).inc()
        HTTP_DURATION.labels(method, route).observe(seconds)

    _measure(update)


def record_ai(operation: str, outcome: str, seconds: float) -> None:
    def update() -> None:
        AI_REQUESTS.labels(operation, outcome).inc()
        AI_DURATION.labels(operation).observe(seconds)

    _measure(update)


def observe_ai_call(operation: str, call: Callable[[], T]) -> T:
    """Count one outbound provider request, including transport failures."""
    started = perf_counter()
    outcome = "failure"
    try:
        result = call()
        outcome = "success"
        return result
    finally:
        record_ai(operation, outcome, perf_counter() - started)
        logging.getLogger(__name__).info(
            "AI provider call finished",
            extra={
                "event": f"ai_request_{'completed' if outcome == 'success' else 'failed'}",
                "operation": operation,
            },
        )


def record_ai_tokens(
    operation: str, input_tokens: int | None, output_tokens: int | None
) -> None:
    def update() -> None:
        for direction, count in (("input", input_tokens), ("output", output_tokens)):
            if type(count) is int and count > 0:
                AI_TOKENS.labels(operation, direction).inc(count)

    _measure(update)


def record_rag(outcome: str, seconds: float) -> None:
    def update() -> None:
        RAG_QUERIES.labels(outcome).inc()
        RAG_DURATION.observe(seconds)

    _measure(update)


def record_document(outcome: str, seconds: float) -> None:
    def update() -> None:
        DOCUMENT_PROCESSING.labels(outcome).inc()
        DOCUMENT_DURATION.observe(seconds)

    _measure(update)


def metrics_response() -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@lru_cache(maxsize=4)
def _known_routes(app) -> tuple:
    # FastAPI's included routers expose leaf routes in request.scope; OpenAPI
    # retains their complete static templates. Never derive labels from raw URLs.
    return tuple(
        (compile_path(template)[0], method.upper(), template)
        for template, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in METHODS
    )


def _route_template(request: Request) -> str:
    try:
        for pattern, method, template in _known_routes(request.app):
            if method == request.method and pattern.fullmatch(request.url.path):
                return template
    except Exception:
        pass
    route = request.scope.get("route")
    # Direct non-API routes (e.g. health) use their already normalized path.
    if route is not None and not request.url.path.startswith("/api/v1/"):
        return route.path
    return "unmatched"


async def observe_request(request: Request, call_next) -> Response:
    supplied_id = request.headers.get("x-request-id", "")
    request_id = supplied_id if REQUEST_ID_PATTERN.fullmatch(supplied_id) else uuid4().hex
    request.state.request_id = request_id
    context_token = request_id_context.set(request_id)
    started = perf_counter()
    status_code = 500
    try:
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            logging.getLogger(__name__).error(
                "Unhandled request failure", extra={"event": "http_request_failed"}
            )
            raise
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        template = _route_template(request)
        seconds = perf_counter() - started
        if request.url.path != "/metrics":
            record_http(request.method, template, status_code, seconds)
            logging.getLogger(__name__).info(
                "HTTP request complete",
                extra={
                    "event": "http_request_completed",
                    "method": request.method if request.method in METHODS else "OTHER",
                    "route": template,
                    "status_code": status_code,
                    "duration_ms": round(seconds * 1000, 2),
                },
            )
        request_id_context.reset(context_token)


def safe_unhandled_error(request: Request, _error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers={"X-Request-ID": getattr(request.state, "request_id", uuid4().hex)},
    )
