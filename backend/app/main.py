import logging
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import get_settings
from app.core.observability import (
    configure_logging,
    metrics_response,
    observe_request,
    safe_unhandled_error,
)
from app.database import engine
from app.services.knowledge.ingestion import recover_stale_processing_documents

settings = get_settings()
configure_logging(settings.app_environment)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        recovered = recover_stale_processing_documents()
        if recovered:
            logger.warning("Interrupted documents recovered", extra={"event": "document_recovery_completed"})
    except Exception:
        logger.error("Document recovery failed", extra={"event": "document_recovery_failed"})
    yield


app = FastAPI(title=settings.app_name, version="0.3.0", lifespan=lifespan)
app.middleware("http")(observe_request)
app.add_exception_handler(Exception, safe_unhandled_error)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return metrics_response()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "live"}


def database_is_ready() -> bool:
    try:
        with engine.connect() as connection:
            return connection.scalar(text("SELECT 1")) == 1
    except Exception:
        logger.warning("Database readiness check failed", extra={"event": "database_readiness_failed"})
        return False


def document_storage_is_ready() -> bool:
    root = settings.document_storage_root.expanduser().resolve()
    if not root.is_dir():
        return False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", prefix=".health-", suffix=".tmp", dir=root
        ) as probe:
            probe.write(b"ok")
            probe.flush()
            probe.seek(0)
            return probe.read() == b"ok"
    except OSError:
        logger.warning("Document storage readiness check failed", extra={"event": "storage_readiness_failed"})
        return False


@app.get("/health/ready", response_model=None)
def health_ready() -> JSONResponse:
    if not database_is_ready() or not document_storage_is_ready():
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(status_code=200, content={"status": "ready"})
