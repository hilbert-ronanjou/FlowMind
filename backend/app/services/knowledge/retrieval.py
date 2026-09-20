from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.document import (
    EMBEDDING_DIMENSIONS,
    Document,
    DocumentChunk,
    DocumentStatus,
)


def search_ready_chunks(
    db: Session,
    *,
    user_id: int,
    course_id: int,
    query_embedding: Sequence[float],
    limit: int = 10,
) -> list[tuple[DocumentChunk, float]]:
    if len(query_embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"query embedding must have {EMBEDDING_DIMENSIONS} dimensions")
    if limit < 1:
        raise ValueError("limit must be positive")

    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label(
        "distance"
    )
    statement = (
        select(DocumentChunk, distance)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(Course, Course.id == Document.course_id)
        .where(
            Course.user_id == user_id,
            Document.course_id == course_id,
            Document.status == DocumentStatus.READY,
        )
        .order_by(distance, DocumentChunk.id)
        .limit(limit)
    )
    return [
        (chunk, float(chunk_distance))
        for chunk, chunk_distance in db.execute(statement).all()
    ]
