import logging
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    APIResponseValidationError,
    LengthFinishReasonError,
    ContentFilterFinishReasonError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.api.routes.courses import owned_course_or_404
from app.core.config import get_settings
from app.core.observability import record_rag
from app.models.course import Course
from app.models.document import Document, DocumentStatus
from app.models.ai_usage_reservation import Operation
from app.services.ai_guard import (
    InteractiveQuota, PaidAIDisabled, ensure_paid_ai_enabled, interactive_operation,
)
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
    require_embedding_configuration,
)
from app.services.knowledge.grounded_answer import (
    GroundedStructuredOutputError,
    KnowledgeConfigurationError,
    generate_grounded_answer,
    require_knowledge_configuration,
    PromptOverflowError,
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
    quota: InteractiveQuota,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=128, pattern=r"^[\x00-\x7f]+$")] = None,
) -> KnowledgeQueryResponse:
    started = perf_counter()
    outcome = "failure"
    try:
        result = _execute_query_course_knowledge(
            course_id, payload, db, current_user, quota, idempotency_key
        )
        outcome = "answerable" if result.answerable else "unanswerable"
        return result
    finally:
        record_rag(outcome, perf_counter() - started)
        logger.info(
            "Knowledge query finished",
            extra={
                "event": "rag_query_completed" if outcome != "failure" else "rag_query_failed",
                "outcome": outcome,
            },
        )


def _execute_query_course_knowledge(
    course_id: int,
    payload: KnowledgeQueryRequest,
    db: DbSession,
    current_user: CurrentUser,
    quota: InteractiveQuota,
    idempotency_key: str | None,
) -> KnowledgeQueryResponse:
    user_id = current_user.id
    owned_course_or_404(db, user_id, course_id)
    ready_count = int(
        db.scalar(
            select(func.count(Document.id))
            .join(Course, Course.id == Document.course_id)
            .where(
                Course.id == course_id,
                Course.user_id == user_id,
                Document.course_id == course_id,
                Document.status == DocumentStatus.READY,
            )
        )
        or 0
    )
    if ready_count == 0:
        db.close()
        return no_answer_response()

    try:
        ensure_paid_ai_enabled()
        require_embedding_configuration()
        require_knowledge_configuration()
    except (PaidAIDisabled, EmbeddingConfigurationError, KnowledgeConfigurationError):
        raise HTTPException(503, "Knowledge service is temporarily unavailable") from None
    db.close()
    with interactive_operation(quota, user_id, Operation.RAG_QUERY,
                               {"course_id": course_id, "question": payload.question},
                               idempotency_key):
        return _execute_paid_course_query(course_id, payload, db, user_id)


def _execute_paid_course_query(
    course_id: int, payload: KnowledgeQueryRequest, db: Session, user_id: int,
) -> KnowledgeQueryResponse:
    try:
        ensure_paid_ai_enabled()
        query_embedding = embed_text(payload.question, max_retries=0)
    except PaidAIDisabled:
        raise HTTPException(503, "Paid AI operations are temporarily unavailable") from None
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
    try:
        candidates = search_ready_chunk_candidates(
            db,
            user_id=user_id,
            course_id=course_id,
            query_embedding=query_embedding,
            limit=settings.knowledge_top_k,
        )[: settings.knowledge_top_k]
    except SQLAlchemyError as error:
        raise HTTPException(500, "Knowledge retrieval failed") from error
    finally:
        db.close()  # The detached, fully loaded candidates need no open transaction.
    if not candidates:
        return no_answer_response()

    try:
        ensure_paid_ai_enabled()
        generation = generate_grounded_answer(payload.question, candidates)
        return build_grounded_response(generation.answer, candidates)
    except PaidAIDisabled:
        raise HTTPException(503, "Paid AI operations are temporarily unavailable") from None
    except PromptOverflowError:
        raise HTTPException(502, "Knowledge prompt exceeds the allowed size") from None
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
        APIResponseValidationError,
        LengthFinishReasonError,
        ContentFilterFinishReasonError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Knowledge answer provider returned an invalid grounded result",
        ) from error
