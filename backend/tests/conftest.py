import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-only-secret-that-is-longer-than-thirty-two-characters")
os.environ.setdefault("JWT_EXPIRE_MINUTES", "60")
TEST_STORAGE_ROOT = Path(tempfile.mkdtemp(prefix="flowmind-test-documents-"))
os.environ.setdefault("DOCUMENT_STORAGE_ROOT", str(TEST_STORAGE_ROOT))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app import models  # noqa: F401

test_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
TestingSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_database(monkeypatch: pytest.MonkeyPatch):
    from app.services.knowledge import ingestion
    from app.core import config
    from app.services.ai import extractor
    from app.services import embedding
    from app.services.knowledge import grounded_answer
    from app.services.ai_guard import get_quota_service
    from app.services.ai_quota import QuotaService

    # Explicit mocks for legacy SQLite API regressions, never a runtime fallback.
    # Dedicated PostgreSQL API tests replace this dependency with the real service.
    settings = config.Settings(
        _env_file=None, database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-only-secret-that-is-longer-than-thirty-two-characters",
        document_storage_root=TEST_STORAGE_ROOT,
        dashscope_api_key="mock-only", dashscope_base_url="https://provider.invalid/v1",
        qwen_model="mock-only", app_environment="test",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    for module in (extractor, embedding, grounded_answer):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    quota = Mock(spec=QuotaService)
    quota.reserve.side_effect = lambda *_args: uuid4()
    quota.mark_dispatched.return_value = True
    quota.cancel.return_value = True
    quota.settle_best_effort.return_value = True
    app.dependency_overrides[get_quota_service] = lambda: quota

    monkeypatch.setattr(ingestion, "SessionLocal", TestingSession)
    shutil.rmtree(TEST_STORAGE_ROOT, ignore_errors=True)
    TEST_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(test_engine)
    yield
    app.dependency_overrides.pop(get_quota_service, None)
    Base.metadata.drop_all(test_engine)
    shutil.rmtree(TEST_STORAGE_ROOT, ignore_errors=True)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db() -> Session:
    with TestingSession() as session:
        yield session


def register_user(client: TestClient, email: str, username: str = "Student") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": username, "password": "Password123!"},
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    data = register_user(client, "student@example.com")
    return {"Authorization": f"Bearer {data['access_token']}"}
