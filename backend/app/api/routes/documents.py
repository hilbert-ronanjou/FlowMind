import logging

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.api.routes.courses import owned_course_or_404
from app.core.config import get_settings
from app.models.course import Course
from app.models.document import Document, DocumentStatus
from app.schemas.document import DocumentRead
from app.services.knowledge.ingestion import (
    DocumentProcessingError,
    FileTooLargeError,
    UploadValidationError,
    process_document,
    remove_stored_pdf,
    store_pdf_upload,
    stored_pdf_exists,
)


logger = logging.getLogger(__name__)
router = APIRouter()


def error_detail(code: str, message: str, **extra: object) -> dict[str, object]:
    return {"code": code, "message": message, **extra}


def owned_document_or_404(db: DbSession, user_id: int, document_id: int) -> Document:
    document = db.scalar(
        select(Document)
        .join(Course, Course.id == Document.course_id)
        .where(Document.id == document_id, Course.user_id == user_id)
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    return document


def active_document_count(db: DbSession, course_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(Document.id)).where(
                Document.course_id == course_id,
                Document.status.in_(
                    [DocumentStatus.READY, DocumentStatus.PROCESSING]
                ),
            )
        )
        or 0
    )


@router.post(
    "/courses/{course_id}/documents",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    course_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> DocumentRead:
    settings = get_settings()
    owned_course_or_404(db, current_user.id, course_id)
    if active_document_count(db, course_id) >= settings.document_course_limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_detail(
                "document_limit_reached",
                "This course already has 20 active documents. Delete one before uploading another.",
            ),
        )

    try:
        stored = await store_pdf_upload(file, max_bytes=settings.document_max_bytes)
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=error_detail("file_too_large", str(exc)),
        ) from None
    except UploadValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("invalid_file", str(exc)),
        ) from None
    finally:
        await file.close()

    try:
        owned_course_or_404(
            db, current_user.id, course_id, for_update=True
        )
        if active_document_count(db, course_id) >= settings.document_course_limit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_detail(
                    "document_limit_reached",
                    "This course already has 20 active documents. Delete one before uploading another.",
                ),
            )

        duplicate = db.scalar(
            select(Document).where(
                Document.course_id == course_id,
                Document.content_hash == stored.content_hash,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_detail(
                    "duplicate_content",
                    "This PDF already exists in the course.",
                    existing_document_id=duplicate.id,
                    existing_status=duplicate.status.value,
                ),
            )

        same_name = db.scalar(
            select(Document).where(
                Document.course_id == course_id,
                Document.filename == stored.filename,
            )
        )
        if same_name is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_detail(
                    "same_filename_different_content",
                    "A different PDF with this filename already exists in the course. Delete it or rename the new file before uploading.",
                    existing_document_id=same_name.id,
                ),
            )

        document = Document(
            course_id=course_id,
            filename=stored.filename,
            storage_path=stored.storage_path,
            content_hash=stored.content_hash,
            file_size=stored.file_size,
            status=DocumentStatus.PROCESSING,
            failure_reason=None,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
    except HTTPException:
        db.rollback()
        remove_stored_pdf(stored.storage_path)
        raise
    except IntegrityError:
        db.rollback()
        remove_stored_pdf(stored.storage_path)
        existing = db.scalar(
            select(Document).where(
                Document.course_id == course_id,
                Document.content_hash == stored.content_hash,
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_detail(
                    "duplicate_content",
                    "This PDF already exists in the course.",
                    existing_document_id=existing.id,
                    existing_status=existing.status.value,
                ),
            ) from None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_detail("document_conflict", "The document could not be created."),
        ) from None
    except Exception:
        db.rollback()
        remove_stored_pdf(stored.storage_path)
        logger.exception("Could not create uploaded document")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail(
                "storage_error", "The PDF could not be stored. Please retry."
            ),
        ) from None

    response = DocumentRead.model_validate(document)
    background_tasks.add_task(process_document, document.id)
    return response


@router.get(
    "/courses/{course_id}/documents", response_model=list[DocumentRead]
)
def list_documents(
    course_id: int, db: DbSession, current_user: CurrentUser
) -> list[Document]:
    owned_course_or_404(db, current_user.id, course_id)
    return list(
        db.scalars(
            select(Document)
            .where(Document.course_id == course_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
        )
    )


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: int, db: DbSession, current_user: CurrentUser
) -> Document:
    return owned_document_or_404(db, current_user.id, document_id)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int, db: DbSession, current_user: CurrentUser
) -> Response:
    document = owned_document_or_404(db, current_user.id, document_id)
    owned_course_or_404(
        db, current_user.id, document.course_id, for_update=True
    )
    db.refresh(document)
    if document.status == DocumentStatus.PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_detail(
                "document_processing", "A processing document cannot be deleted."
            ),
        )
    storage_path = document.storage_path
    try:
        remove_stored_pdf(storage_path)
        db.delete(document)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not delete document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail("storage_error", "The document could not be deleted."),
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/documents/{document_id}/retry",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    current_user: CurrentUser,
) -> DocumentRead:
    document = owned_document_or_404(db, current_user.id, document_id)
    owned_course_or_404(
        db, current_user.id, document.course_id, for_update=True
    )
    db.refresh(document)
    if document.status != DocumentStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_detail(
                "retry_not_allowed", "Only failed documents can be retried."
            ),
        )
    if active_document_count(db, document.course_id) >= get_settings().document_course_limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_detail(
                "document_limit_reached",
                "This course already has 20 active documents. Delete one before retrying.",
            ),
        )
    try:
        source_exists = stored_pdf_exists(document.storage_path)
    except DocumentProcessingError:
        source_exists = False
    if not source_exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_detail(
                "source_file_missing",
                "The stored PDF is missing. Upload the document again.",
            ),
        )

    document.status = DocumentStatus.PROCESSING
    document.failure_reason = None
    try:
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        logger.exception("Could not retry document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail("retry_failed", "The document could not be retried."),
        ) from None
    response = DocumentRead.model_validate(document)
    background_tasks.add_task(process_document, document.id)
    return response
