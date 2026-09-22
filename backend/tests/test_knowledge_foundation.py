from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import APITimeoutError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.course import Course
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services import embedding
from app.services.embedding import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingResponseError,
    embed_texts,
    embed_texts_with_client,
)


@pytest.mark.parametrize("env_template", [None, ".env.example", ".env.production.example"])
def test_settings_use_fixed_embedding_dimensions_without_env_variable(
    monkeypatch, env_template
):
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.delenv("embedding_dimensions", raising=False)
    env_file = (
        Path(__file__).resolve().parents[2] / env_template
        if env_template is not None
        else None
    )

    settings = Settings(
        _env_file=env_file,
        database_url="postgresql+psycopg://test:test@localhost/flowmind_test",
        jwt_secret="test-only-secret-for-settings-validation",
    )

    assert settings.embedding_dimensions == 1024
    assert isinstance(settings.embedding_dimensions, int)


def document_fixture() -> tuple[User, Course, Document]:
    user = User(
        email="knowledge@example.com",
        username="Knowledge Student",
        password_hash="test-hash",
    )
    course = Course(user=user, name="Database Systems")
    document = Document(
        course=course,
        filename="database-notes.txt",
        storage_path="users/knowledge/database-notes.txt",
        content_hash="a" * 64,
        file_size=128,
        status=DocumentStatus.READY,
    )
    return user, course, document


def test_document_and_chunk_relationships(db: Session):
    user, course, document = document_fixture()
    chunk = DocumentChunk(
        document=document,
        chunk_index=0,
        page_number=1,
        content="Database transactions provide ACID guarantees.",
        embedding=[0.0] * 1024,
    )
    db.add(user)
    db.commit()

    assert document.course is course
    assert document.course.user is user
    assert course.documents == [document]
    assert document.chunks == [chunk]
    assert chunk.document is document


def test_duplicate_document_chunk_index_is_rejected(db: Session):
    user, _, document = document_fixture()
    document.chunks = [
        DocumentChunk(chunk_index=0, content="First", embedding=[0.0] * 1024),
        DocumentChunk(chunk_index=0, content="Duplicate", embedding=[0.0] * 1024),
    ]
    db.add(user)

    with pytest.raises(IntegrityError):
        db.commit()


def test_duplicate_document_content_hash_in_same_course_is_rejected(db: Session):
    user, course, document = document_fixture()
    duplicate = Document(
        course=course,
        filename="database-notes-copy.txt",
        storage_path="users/knowledge/database-notes-copy.txt",
        content_hash=document.content_hash,
        file_size=128,
        status=DocumentStatus.PROCESSING,
    )
    db.add(user)
    db.add(duplicate)

    with pytest.raises(IntegrityError):
        db.commit()


def test_same_document_content_hash_in_different_courses_is_allowed(db: Session):
    user, _, document = document_fixture()
    other_course = Course(user=user, name="Distributed Systems")
    other_document = Document(
        course=other_course,
        filename="database-notes.txt",
        storage_path="users/knowledge/distributed-database-notes.txt",
        content_hash=document.content_hash,
        file_size=128,
        status=DocumentStatus.PROCESSING,
    )
    db.add(user)
    db.commit()

    assert document.id is not None
    assert other_document.id is not None


def test_document_constraints_reject_invalid_file_size(db: Session):
    user, _, document = document_fixture()
    document.file_size = -1
    db.add(user)

    with pytest.raises(IntegrityError):
        db.commit()


def test_chunk_vector_dimension_is_exactly_1024(db: Session):
    with pytest.raises(ValueError, match="exactly 1024 dimensions"):
        DocumentChunk(
            chunk_index=0,
            content="Wrong dimension",
            embedding=[0.0] * 1023,
        )


def test_embedding_client_validates_dimension_and_request_parameters():
    provider = Mock()
    provider.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.25] * 1024)]
    )

    result = embed_texts_with_client(
        provider, "text-embedding-v4", 1024, ["  database transaction  "]
    )

    assert len(result[0]) == 1024
    provider.embeddings.create.assert_called_once_with(
        model="text-embedding-v4",
        input=["database transaction"],
        dimensions=1024,
    )


def test_embedding_client_rejects_wrong_dimension():
    provider = Mock()
    provider.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.25] * 8)]
    )

    with pytest.raises(EmbeddingResponseError, match="expected 1024"):
        embed_texts_with_client(provider, "text-embedding-v4", 1024, ["text"])


def test_embedding_provider_error_is_safe(monkeypatch):
    private_detail = "provider-private-diagnostic"
    provider = Mock()
    provider.embeddings.create.side_effect = APITimeoutError(
        request=httpx.Request("POST", "https://provider.invalid/v1/embeddings")
    )
    monkeypatch.setattr(embedding, "OpenAI", Mock(return_value=provider))
    monkeypatch.setattr(
        embedding,
        "get_settings",
        Mock(
            return_value=SimpleNamespace(
                dashscope_api_key="test-secret",
                dashscope_base_url="https://provider.invalid/v1",
                embedding_model="text-embedding-v4",
                embedding_dimensions=1024,
            )
        ),
    )

    with pytest.raises(EmbeddingProviderError) as error:
        embed_texts(["database transaction"])

    assert str(error.value) == "Embedding service is temporarily unavailable"
    assert private_detail not in str(error.value)
    assert "provider.invalid" not in str(error.value)


def test_embedding_requires_server_configuration(monkeypatch):
    monkeypatch.setattr(
        embedding,
        "get_settings",
        Mock(
            return_value=SimpleNamespace(
                dashscope_api_key=None,
                dashscope_base_url=None,
                embedding_model="text-embedding-v4",
                embedding_dimensions=1024,
            )
        ),
    )

    with pytest.raises(EmbeddingConfigurationError):
        embed_texts(["database transaction"])
