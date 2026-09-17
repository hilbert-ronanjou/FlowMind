from pydantic import BaseModel

from app.schemas.task import TaskRead


class DashboardRead(BaseModel):
    today_tasks: list[TaskRead]
    upcoming_tasks: list[TaskRead]
    course_count: int
    completed_task_count: int
    recent_tasks: list[TaskRead]

