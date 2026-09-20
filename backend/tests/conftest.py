import os
import shutil
import tempfile
from pathlib import Path

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

    monkeypatch.setattr(ingestion, "SessionLocal", TestingSession)
    shutil.rmtree(TEST_STORAGE_ROOT, ignore_errors=True)
    TEST_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(test_engine)
    yield
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
