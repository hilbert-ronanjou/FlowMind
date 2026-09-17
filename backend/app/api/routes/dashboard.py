from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models.course import Course
from app.models.task import Task, TaskStatus
from app.schemas.dashboard import DashboardRead

router = APIRouter()


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@router.get("", response_model=DashboardRead)
def get_dashboard(db: DbSession, current_user: CurrentUser) -> DashboardRead:
    now = datetime.now(UTC)
    tasks = list(
        db.scalars(
            select(Task)
            .where(Task.user_id == current_user.id)
            .order_by(Task.created_at.desc())
        )
    )
    today_tasks = [
        task for task in tasks if task.deadline and as_utc(task.deadline).date() == now.date()
    ]
    upcoming_tasks = sorted(
        [
            task
            for task in tasks
            if task.deadline
            and as_utc(task.deadline) > now
            and task.status != TaskStatus.COMPLETED
        ],
        key=lambda task: as_utc(task.deadline) if task.deadline else now,
    )[:5]
    course_count = db.scalar(
        select(func.count(Course.id)).where(Course.user_id == current_user.id)
    ) or 0
    completed_count = db.scalar(
        select(func.count(Task.id)).where(
            Task.user_id == current_user.id, Task.status == TaskStatus.COMPLETED
        )
    ) or 0
    return DashboardRead(
        today_tasks=today_tasks,
        upcoming_tasks=upcoming_tasks,
        course_count=course_count,
        completed_task_count=completed_count,
        recent_tasks=tasks[:5],
    )
