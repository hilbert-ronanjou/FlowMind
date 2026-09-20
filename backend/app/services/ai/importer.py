from datetime import UTC, datetime, time

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.task import Task, TaskPriority, TaskSource, TaskStatus
from app.services.ai.schemas import (
    ExistingCourseSelection,
    ImportRequest,
    ImportResult,
)


def _owned_course(db: Session, user_id: int, course_id: int) -> Course:
    course = db.scalar(
        select(Course).where(Course.id == course_id, Course.user_id == user_id)
    )
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found",
        )
    return course


def _optional_item(description: str | None) -> str | None:
    if not description:
        return None
    value = description.strip()
    prefix = "可选事项"
    if not value.startswith(prefix):
        return None
    return value[len(prefix) :].lstrip("：: -\n") or None


def build_task_description(payload: ImportRequest) -> str | None:
    steps = payload.draft.tasks
    sections: list[str] = []

    if len(steps) > 1:
        required_lines = []
        for step in steps:
            optional_item = _optional_item(step.description)
            if optional_item == step.title:
                continue
            line = f"- {step.title}"
            if step.description and optional_item is None:
                line = f"{line}：{step.description.strip()}"
            required_lines.append(line)
        if required_lines:
            sections.append("完成要求：\n" + "\n".join(required_lines))
    elif len(steps) == 1 and steps[0].description:
        description = steps[0].description.strip()
        if _optional_item(description) is None:
            sections.append(description)

    optional_items = [
        item
        for step in steps
        if (item := _optional_item(step.description)) is not None
    ]
    if optional_items:
        sections.append("可选事项：\n" + "\n".join(f"- {item}" for item in optional_items))

    description = "\n\n".join(sections) or None
    if description is not None and len(description) > 4000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Combined task description exceeds 4000 characters",
        )
    return description


def import_confirmed_draft(
    db: Session,
    user_id: int,
    payload: ImportRequest,
) -> ImportResult:
    course_created = False
    try:
        if isinstance(payload.course, ExistingCourseSelection):
            course = _owned_course(db, user_id, payload.course.course_id)
        else:
            course = Course(
                user_id=user_id,
                name=payload.course.name,
                code=payload.course.code,
                teacher=payload.course.teacher,
                description=payload.course.description,
                color=payload.course.color,
            )
            db.add(course)
            db.flush()
            course_created = True

        deadline = (
            datetime.combine(payload.draft.deadline, time.min, tzinfo=UTC)
            if payload.draft.deadline is not None
            else None
        )
        task = Task(
            user_id=user_id,
            course_id=course.id,
            title=payload.draft.title,
            description=build_task_description(payload),
            deadline=deadline,
            priority=TaskPriority(payload.draft.priority.value),
            status=TaskStatus.TODO,
            source=TaskSource.AI,
        )
        db.add(task)
        db.flush()
        result = ImportResult(
            course_created=course_created,
            course=course,
            task=task,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return result
