from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.routes.courses import owned_course_or_404
from app.models.task import Task, TaskPriority, TaskSource, TaskStatus
from app.schemas.task import TaskCreate, TaskRead, TaskStatusUpdate, TaskUpdate

router = APIRouter()


def owned_task_or_404(db: DbSession, user_id: int, task_id: int) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def validate_course(db: DbSession, user_id: int, course_id: int | None) -> None:
    if course_id is not None:
        owned_course_or_404(db, user_id, course_id)


@router.get("", response_model=list[TaskRead])
def list_tasks(
    db: DbSession,
    current_user: CurrentUser,
    task_status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    course_id: int | None = None,
) -> list[Task]:
    query = select(Task).where(Task.user_id == current_user.id)
    if task_status is not None:
        query = query.where(Task.status == task_status)
    if priority is not None:
        query = query.where(Task.priority == priority)
    if course_id is not None:
        query = query.where(Task.course_id == course_id)
    return list(db.scalars(query.order_by(Task.created_at.desc())))


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, db: DbSession, current_user: CurrentUser) -> Task:
    validate_course(db, current_user.id, payload.course_id)
    task = Task(
        user_id=current_user.id,
        source=TaskSource.MANUAL,
        status=TaskStatus.TODO,
        **payload.model_dump(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: DbSession, current_user: CurrentUser) -> Task:
    return owned_task_or_404(db, current_user.id, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(
    task_id: int, payload: TaskUpdate, db: DbSession, current_user: CurrentUser
) -> Task:
    task = owned_task_or_404(db, current_user.id, task_id)
    changes = payload.model_dump(exclude_unset=True)
    if "course_id" in changes:
        validate_course(db, current_user.id, changes["course_id"])
    for field, value in changes.items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    return task


@router.patch("/{task_id}/status", response_model=TaskRead)
def update_task_status(
    task_id: int,
    payload: TaskStatusUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> Task:
    task = owned_task_or_404(db, current_user.id, task_id)
    task.status = payload.status
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: DbSession, current_user: CurrentUser) -> Response:
    task = owned_task_or_404(db, current_user.id, task_id)
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

