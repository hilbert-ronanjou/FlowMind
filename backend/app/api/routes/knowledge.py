import logging

from fastapi import APIRouter, HTTPException, status
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.api.routes.courses import owned_course_or_404
from app.core.config import get_settings
from app.models.course import Course
from app.models.document import Document, DocumentStatus
from app.schemas.knowledge import (
    KnowledgeQueryRequest,
    KnowledgeQueryResponse,
    no_answer_response,
)
from app.services.embedding import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingResponseError,
    embed_text,
)
from app.services.knowledge.grounded_answer import (
    GroundedStructuredOutputError,
    KnowledgeConfigurationError,
    generate_grounded_answer,
)
from app.services.knowledge.query import (
    GroundingValidationError,
    build_grounded_response,
)
from app.services.knowledge.retrieval import search_ready_chunk_candidates


logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/courses/{course_id}/knowledge/query",
    response_model=KnowledgeQueryResponse,
)
def query_course_knowledge(
    course_id: int,
    payload: KnowledgeQueryRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> KnowledgeQueryResponse:
    owned_course_or_404(db, current_user.id, course_id)
    ready_count = int(
        db.scalar(
            select(func.count(Document.id))
            .join(Course, Course.id == Document.course_id)
            .where(
                Course.id == course_id,
                Course.user_id == current_user.id,
                Document.course_id == course_id,
                Document.status == DocumentStatus.READY,
            )
        )
        or 0
    )
    if ready_count == 0:
        return no_answer_response()

    try:
        query_embedding = embed_text(payload.question)
    except (EmbeddingConfigurationError, EmbeddingProviderError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge embedding service is temporarily unavailable",
        ) from error
    except (EmbeddingResponseError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Knowledge embedding service returned an invalid result",
        ) from error

    settings = get_settings()
    candidates = search_ready_chunk_candidates(
        db,
        user_id=current_user.id,
        course_id=course_id,
        query_embedding=query_embedding,
        limit=settings.knowledge_top_k,
    )[: settings.knowledge_top_k]
    logger.info(
        "Knowledge retrieval course_id=%s count=%s chunk_ids=%s",
        course_id,
        len(candidates),
        [candidate.chunk_id for candidate in candidates],
    )
    if not candidates:
        return no_answer_response()

    try:
        generation = generate_grounded_answer(payload.question, candidates)
        logger.info(
            "Grounded answer course_id=%s retrieval_count=%s latency_ms=%.1f total_tokens=%s",
            course_id,
            len(candidates),
            generation.latency_ms,
            generation.usage.total_tokens,
        )
        return build_grounded_response(generation.answer, candidates)
    except (KnowledgeConfigurationError, RateLimitError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge answer service is temporarily unavailable",
        ) from error
    except (APIConnectionError, APITimeoutError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge answer service is temporarily unavailable",
        ) from error
    except (AuthenticationError, PermissionDeniedError, APIStatusError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Knowledge answer provider request failed",
        ) from error
    except (
        GroundedStructuredOutputError,
        GroundingValidationError,
        ValidationError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Knowledge answer provider returned an invalid grounded result",
        ) from error
