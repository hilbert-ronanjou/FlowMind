from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.task import TaskPriority, TaskSource, TaskStatus


class TaskCreate(BaseModel):
    course_id: int | None = None
    title: str = Field(min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=4000)
    deadline: datetime | None = None
    priority: TaskPriority = TaskPriority.MEDIUM

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Task title cannot be blank")
        return value


class TaskUpdate(BaseModel):
    course_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=4000)
    deadline: datetime | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Task title cannot be blank")
        return value.strip() if value is not None else value


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class TaskRead(BaseModel):
    id: int
    user_id: int
    course_id: int | None
    title: str
    description: str | None
    deadline: datetime | None
    priority: TaskPriority
    status: TaskStatus
    source: TaskSource
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

