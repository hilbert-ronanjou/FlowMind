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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.deps import CurrentUser, DbSession
from app.services.ai.extractor import (
    AIConfigurationError,
    StructuredOutputError,
    extract_course_content,
    require_extraction_configuration,
)
from app.models.ai_usage_reservation import Operation
from app.services.ai_guard import (
    InteractiveQuota, PaidAIDisabled, ensure_paid_ai_enabled, interactive_operation,
)
from app.services.ai.importer import import_confirmed_draft
from app.services.ai.schemas import (
    ExtractionRequest,
    ExtractionResult,
    ImportRequest,
    ImportResult,
)

router = APIRouter()


@router.post("/extract", response_model=ExtractionResult)
def extract_content(
    payload: ExtractionRequest,
    current_user: CurrentUser,
    db: DbSession,
    quota: InteractiveQuota,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=128, pattern=r"^[\x00-\x7f]+$")] = None,
) -> ExtractionResult:
    user_id = current_user.id
    try:
        ensure_paid_ai_enabled()
        require_extraction_configuration()
        db.close()  # End authentication reads before quota/provider work.
        with interactive_operation(quota, user_id, Operation.EXTRACTION,
                                   {"text": payload.text}, idempotency_key):
            return extract_course_content(payload.text)
    except PaidAIDisabled:
        raise HTTPException(503, "Paid AI operations are temporarily unavailable") from None
    except AIConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI extraction service is not configured",
        ) from error
    except RateLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI extraction service is temporarily unavailable",
        ) from error
    except (APIConnectionError, APITimeoutError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI extraction service is temporarily unavailable",
        ) from error
    except (AuthenticationError, PermissionDeniedError, APIStatusError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI provider request failed",
        ) from error
    except (StructuredOutputError, ValidationError, APIResponseValidationError,
            LengthFinishReasonError, ContentFilterFinishReasonError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI provider returned an invalid structured result",
        ) from error


@router.post("/import", response_model=ImportResult, status_code=status.HTTP_201_CREATED)
def confirm_import(
    payload: ImportRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> ImportResult:
    try:
        return import_confirmed_draft(db, current_user.id, payload)
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A course with this name already exists",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Import failed; no data was saved",
        ) from error
