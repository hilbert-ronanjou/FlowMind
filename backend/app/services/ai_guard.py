"""Small HTTP integration policy for the existing PostgreSQL quota protocol."""
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends, HTTPException
from openai import (
    APIConnectionError, APIStatusError, APIResponseValidationError,
    LengthFinishReasonError, ContentFilterFinishReasonError,
)

from app.core import config
from app.models.ai_usage_reservation import Operation, ReservationState as State
from app.services.ai_quota import (
    DuplicateReservation, QuotaExceeded, QuotaLimits, QuotaService,
    QuotaUnavailable, canonical_fingerprint, validate_key,
)


class PaidAIDisabled(RuntimeError):
    pass


def ensure_paid_ai_enabled() -> None:
    if not config.get_settings().ai_paid_operations_enabled:
        raise PaidAIDisabled("Paid AI operations are temporarily disabled")


def get_quota_service() -> QuotaService:
    from app.database import engine

    settings = config.get_settings()
    return QuotaService(engine, QuotaLimits(
        user_window=settings.ai_interactive_user_window_limit,
        window_seconds=settings.ai_interactive_window_seconds,
        user_daily=settings.ai_interactive_user_daily_limit,
        global_daily=settings.ai_interactive_global_daily_limit,
    ), enabled=lambda: config.get_settings().ai_paid_operations_enabled)


InteractiveQuota = Annotated[QuotaService, Depends(get_quota_service)]


def failure_state(error: BaseException) -> State:
    """Preserve typed causes through HTTP/embedding wrappers, never public strings."""
    from app.services.embedding import EmbeddingProviderError

    cause = error
    seen = set()
    causes = []
    while id(cause) not in seen:
        seen.add(id(cause))
        causes.append(cause)
        if cause.__cause__ is None:
            break
        cause = cause.__cause__
    if any(isinstance(item, APIConnectionError) for item in causes):  # Includes timeout.
        return State.UNCERTAIN
    if any(isinstance(item, APIStatusError) for item in causes):
        return State.FAILED
    if any(isinstance(item, EmbeddingProviderError) for item in causes):
        return State.UNCERTAIN  # A wrapped failure without a classified SDK cause.
    from pydantic import ValidationError
    from sqlalchemy.exc import SQLAlchemyError
    from app.services.ai.extractor import AIConfigurationError, StructuredOutputError
    from app.services.embedding import EmbeddingConfigurationError, EmbeddingResponseError
    from app.services.knowledge.grounded_answer import (
        GroundedStructuredOutputError, KnowledgeConfigurationError, PromptOverflowError,
    )
    from app.services.knowledge.query import GroundingValidationError

    known_local = (PaidAIDisabled, ValueError, ValidationError,
                   SQLAlchemyError, AIConfigurationError, StructuredOutputError,
                   EmbeddingConfigurationError, EmbeddingResponseError,
                   GroundedStructuredOutputError, KnowledgeConfigurationError,
                   PromptOverflowError, GroundingValidationError,
                   APIResponseValidationError, LengthFinishReasonError,
                   ContentFilterFinishReasonError)
    if any(isinstance(item, known_local) for item in causes):
        return State.FAILED
    # A plain local HTTP error is known; an HTTP wrapper alone must not turn
    # an unknown underlying provider failure into a known billing outcome.
    return State.FAILED if len(causes) == 1 and isinstance(error, HTTPException) else State.UNCERTAIN


@contextmanager
def interactive_operation(quota: QuotaService, user_id: int, operation: Operation,
                          inputs: Mapping, key: str | None) -> Iterator[None]:
    try:
        ensure_paid_ai_enabled()
        key = validate_key(key)
        rid = quota.reserve(user_id, operation, canonical_fingerprint(operation, inputs), key)
    except ValueError:
        raise HTTPException(422, "Invalid AI operation key") from None
    except QuotaExceeded as error:
        raise HTTPException(429, str(error), headers={"Retry-After": str(error.retry_after)}) from None
    except DuplicateReservation as error:
        raise HTTPException(409, str(error)) from None
    except (QuotaUnavailable, PaidAIDisabled):
        raise HTTPException(503, "Paid AI operations are temporarily unavailable") from None

    try:
        ensure_paid_ai_enabled()
        if not quota.mark_dispatched(rid):
            raise QuotaUnavailable()
    except (QuotaUnavailable, PaidAIDisabled):
        # Conditional cancellation cannot overwrite an uncertain committed dispatch.
        try:
            quota.cancel(rid)
        except QuotaUnavailable:
            pass
        raise HTTPException(503, "Paid AI operations are temporarily unavailable") from None

    try:
        yield
    except BaseException as error:
        quota.settle_best_effort(rid, failure_state(error))
        raise
    else:
        quota.settle_best_effort(rid, State.SUCCEEDED)
