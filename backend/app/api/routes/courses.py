from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.models.course import Course
from app.schemas.course import CourseCreate, CourseRead, CourseUpdate

router = APIRouter()


def owned_course_or_404(db: DbSession, user_id: int, course_id: int) -> Course:
    course = db.scalar(
        select(Course).where(Course.id == course_id, Course.user_id == user_id)
    )
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
    course = owned_course_or_404(db, current_user.id, course_id)
    db.delete(course)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

