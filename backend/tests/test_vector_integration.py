import os
from pathlib import Path
from uuid import uuid4

import pytest
from pypdf import PageObject, PdfWriter
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.course import Course
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services.knowledge import ingestion
from app.services.knowledge.retrieval import (
    search_ready_chunk_candidates,
    search_ready_chunks,
)


POSTGRES_URL = os.getenv("TEST_POSTGRES_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="set TEST_POSTGRES_DATABASE_URL for real pgvector integration"
)


def unit_vector(index: int) -> list[float]:
    vector = [0.0] * 1024
    vector[index] = 1.0
    return vector


def test_ingestion_persists_nul_free_chunks_in_postgres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    # Exercise real inserts and the ingestion commit without retaining test rows.
    with engine.connect() as connection, connection.begin() as transaction:
        sessions = sessionmaker(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            token = uuid4().hex
            path = tmp_path / f"{token}.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with path.open("wb") as output:
                writer.write(output)
            with sessions() as db:
                user = User(
                    email=f"nul-{token}@example.invalid", username="NUL regression",
                    password_hash="test-hash",
                )
                document = Document(
                    course=Course(user=user, name=f"NUL {token}"),
                    filename="nul.pdf", storage_path=path.name,
                    content_hash=token * 2, file_size=path.stat().st_size,
                    status=DocumentStatus.PROCESSING,
                )
                db.add(document)
                db.commit()
                document_id = document.id

            expected = "辽宁省博物馆赓续家国情怀"

            def fake_embed(texts: list[str]) -> list[list[float]]:
                assert texts == [expected]
                return [unit_vector(0)]

            monkeypatch.setattr(ingestion, "SessionLocal", sessions)
            monkeypatch.setattr(ingestion, "resolve_storage_path", lambda _key: path)
            monkeypatch.setattr(
                PageObject, "extract_text", lambda _page: "辽宁省博物馆\x00赓续家国情怀"
            )
            monkeypatch.setattr(ingestion, "embed_texts", fake_embed)
            ingestion.process_document(document_id)

            with sessions() as db:
                saved = db.get(Document, document_id)
                chunks = list(db.scalars(
                    select(DocumentChunk).where(DocumentChunk.document_id == document_id)
                ))
                assert saved is not None and saved.status == DocumentStatus.READY
                assert saved.failure_reason is None
                assert len(chunks) == 1
                assert chunks[0].content == expected
                assert "\x00" not in chunks[0].content
                assert chunks[0].page_number == 1
                assert len(chunks[0].embedding) == 1024
        finally:
            transaction.rollback()
    engine.dispose()


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
                        page_number=1,
                        content="transaction",
                        embedding=unit_vector(0),
                    ),
                    DocumentChunk(
                        chunk_index=1,
                        page_number=2,
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
            Document(
                course=course,
                filename="processing.txt",
                storage_path=f"tests/{token}/processing.txt",
                content_hash="e" * 64,
                file_size=1,
                status=DocumentStatus.PROCESSING,
                chunks=[
                    DocumentChunk(
                        chunk_index=0,
                        content="processing exact match",
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

        candidates = search_ready_chunk_candidates(
            db,
            user_id=owner.id,
            course_id=course.id,
            query_embedding=unit_vector(0),
            limit=5,
        )
        assert [item.content for item in candidates] == ["transaction", "process"]
        assert [item.filename for item in candidates] == ["ready.txt", "ready.txt"]
        assert [item.page_number for item in candidates] == [1, 2]
        assert candidates[0].distance < candidates[1].distance

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
