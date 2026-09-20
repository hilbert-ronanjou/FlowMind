import logging

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.models.course import Course
from app.models.document import Document, DocumentStatus
from app.schemas.course import CourseCreate, CourseRead, CourseUpdate
from app.services.knowledge.ingestion import resolve_storage_path

router = APIRouter()
logger = logging.getLogger(__name__)


def owned_course_or_404(
    db: DbSession, user_id: int, course_id: int, *, for_update: bool = False
) -> Course:
    statement = select(Course).where(
        Course.id == course_id, Course.user_id == user_id
    )
    if for_update:
        statement = statement.with_for_update()
    course = db.scalar(statement)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


@router.get("", response_model=list[CourseRead])
def list_courses(db: DbSession, current_user: CurrentUser) -> list[Course]:
    return list(
        db.scalars(
            select(Course)
            .where(Course.user_id == current_user.id)
            .order_by(Course.updated_at.desc())
        )
    )


@router.post("", response_model=CourseRead, status_code=status.HTTP_201_CREATED)
def create_course(payload: CourseCreate, db: DbSession, current_user: CurrentUser) -> Course:
    course = Course(user_id=current_user.id, **payload.model_dump())
    db.add(course)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A course with this name already exists") from None
    db.refresh(course)
    return course


@router.get("/{course_id}", response_model=CourseRead)
def get_course(course_id: int, db: DbSession, current_user: CurrentUser) -> Course:
    return owned_course_or_404(db, current_user.id, course_id)


@router.patch("/{course_id}", response_model=CourseRead)
def update_course(
    course_id: int, payload: CourseUpdate, db: DbSession, current_user: CurrentUser
) -> Course:
    course = owned_course_or_404(db, current_user.id, course_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(course, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A course with this name already exists") from None
    db.refresh(course)
    return course


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: int, db: DbSession, current_user: CurrentUser
) -> Response:
    course = owned_course_or_404(db, current_user.id, course_id, for_update=True)
    documents = list(
        db.scalars(select(Document).where(Document.course_id == course_id))
    )
    if any(document.status == DocumentStatus.PROCESSING for document in documents):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "document_processing",
                "message": "A course with a processing document cannot be deleted.",
            },
        )
    try:
        stored_files = [
            resolve_storage_path(document.storage_path) for document in documents
        ]
        for stored_file in stored_files:
            stored_file.unlink(missing_ok=True)
        db.delete(course)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not delete course %s and its local documents", course_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The course could not be deleted.",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
