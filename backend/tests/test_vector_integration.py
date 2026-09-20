import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services.knowledge.retrieval import search_ready_chunks


POSTGRES_URL = os.getenv("TEST_POSTGRES_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="set TEST_POSTGRES_DATABASE_URL for real pgvector integration"
)


def unit_vector(index: int) -> list[float]:
    vector = [0.0] * 1024
    vector[index] = 1.0
    return vector


def test_vector_retrieval_orders_results_and_enforces_scope():
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, expire_on_commit=False)
    token = uuid4().hex
    try:
        owner = User(
            email=f"vector-owner-{token}@example.invalid",
            username="Vector Owner",
            password_hash="test-hash",
        )
        other = User(
            email=f"vector-other-{token}@example.invalid",
            username="Other Owner",
            password_hash="test-hash",
        )
        course = Course(user=owner, name=f"Database {token}")
        other_course = Course(user=owner, name=f"Operating Systems {token}")
        documents = [
            Document(
                course=course,
                filename="ready.txt",
                storage_path=f"tests/{token}/ready.txt",
                content_hash="a" * 64,
                file_size=1,
                status=DocumentStatus.READY,
                chunks=[
                    DocumentChunk(
                        chunk_index=0,
                        content="transaction",
                        embedding=unit_vector(0),
                    ),
                    DocumentChunk(
                        chunk_index=1,
                        content="process",
                        embedding=unit_vector(1),
                    ),
                ],
            ),
            Document(
                course=other_course,
                filename="other-course.txt",
                storage_path=f"tests/{token}/other-course.txt",
                content_hash="a" * 64,
                file_size=1,
                status=DocumentStatus.READY,
                chunks=[
                    DocumentChunk(
                        chunk_index=0,
                        content="wrong course exact match",
                        embedding=unit_vector(0),
                    )
                ],
            ),
            Document(
                course=course,
                filename="failed.txt",
                storage_path=f"tests/{token}/failed.txt",
                content_hash="c" * 64,
                file_size=1,
                status=DocumentStatus.FAILED,
                chunks=[
                    DocumentChunk(
                        chunk_index=0,
                        content="failed exact match",
                        embedding=unit_vector(0),
                    )
                ],
            ),
        ]
        foreign_course = Course(user=other, name=f"Foreign {token}")
        documents.append(
            Document(
                course=foreign_course,
                filename="foreign.txt",
                storage_path=f"tests/{token}/foreign.txt",
                content_hash="d" * 64,
                file_size=1,
                status=DocumentStatus.READY,
                chunks=[
                    DocumentChunk(
                        chunk_index=0,
                        content="foreign exact match",
                        embedding=unit_vector(0),
                    )
                ],
            )
        )
        db.add_all([owner, other])
        db.flush()

        matches = search_ready_chunks(
            db,
            user_id=owner.id,
            course_id=course.id,
            query_embedding=unit_vector(0),
            limit=10,
        )

        assert [chunk.content for chunk, _ in matches] == ["transaction", "process"]
        assert matches[0][1] < matches[1][1]

        assert search_ready_chunks(
            db,
            user_id=other.id,
            course_id=course.id,
            query_embedding=unit_vector(0),
        ) == []

        with pytest.raises(IntegrityError):
            with db.begin_nested():
                duplicate = Document(
                    course=course,
                    filename="ready-copy.txt",
                    storage_path=f"tests/{token}/ready-copy.txt",
                    content_hash="a" * 64,
                    file_size=1,
                    status=DocumentStatus.READY,
                )
                db.add(duplicate)
                db.flush()
    finally:
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
