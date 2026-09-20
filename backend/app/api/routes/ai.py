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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.deps import CurrentUser, DbSession
from app.services.ai.extractor import (
    AIConfigurationError,
    StructuredOutputError,
    extract_course_content,
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
    _current_user: CurrentUser,
) -> ExtractionResult:
    try:
        return extract_course_content(payload.text)
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
    except (StructuredOutputError, ValidationError) as error:
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
