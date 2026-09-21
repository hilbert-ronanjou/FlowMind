from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.document import (
    EMBEDDING_DIMENSIONS,
    Document,
    DocumentChunk,
    DocumentStatus,
)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: DocumentChunk
    document_id: int
    filename: str
    distance: float

    @property
    def chunk_id(self) -> int:
        return self.chunk.id

    @property
    def page_number(self) -> int | None:
        return self.chunk.page_number

    @property
    def content(self) -> str:
        return self.chunk.content


def search_ready_chunk_candidates(
    db: Session,
    *,
    user_id: int,
    course_id: int,
    query_embedding: Sequence[float],
    limit: int = 5,
) -> list[RetrievedChunk]:
    if len(query_embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"query embedding must have {EMBEDDING_DIMENSIONS} dimensions")
    if limit < 1:
        raise ValueError("limit must be positive")

    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label(
        "distance"
    )
    statement = (
        select(DocumentChunk, Document.id, Document.filename, distance)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(Course, Course.id == Document.course_id)
        .where(
            Course.id == course_id,
            Course.user_id == user_id,
            Document.course_id == course_id,
            Document.status == DocumentStatus.READY,
        )
        .order_by(distance, DocumentChunk.id)
        .limit(limit)
    )
    return [
        RetrievedChunk(
            chunk=chunk,
            document_id=document_id,
            filename=filename,
            distance=float(chunk_distance),
        )
        for chunk, document_id, filename, chunk_distance in db.execute(statement).all()
    ]


def search_ready_chunks(
    db: Session,
    *,
    user_id: int,
    course_id: int,
    query_embedding: Sequence[float],
    limit: int = 10,
) -> list[tuple[DocumentChunk, float]]:
    return [
        (candidate.chunk, candidate.distance)
        for candidate in search_ready_chunk_candidates(
            db,
            user_id=user_id,
            course_id=course_id,
            query_embedding=query_embedding,
            limit=limit,
        )
    ]
