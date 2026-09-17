from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TaskStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class TaskPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TaskSource(str, Enum):
    MANUAL = "MANUAL"
    AI = "AI"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    priority: Mapped[TaskPriority] = mapped_column(
        SqlEnum(TaskPriority, name="task_priority", native_enum=False),
        default=TaskPriority.MEDIUM,
    )
    status: Mapped[TaskStatus] = mapped_column(
        SqlEnum(TaskStatus, name="task_status", native_enum=False),
        default=TaskStatus.TODO,
    )
    source: Mapped[TaskSource] = mapped_column(
        SqlEnum(TaskSource, name="task_source", native_enum=False),
        default=TaskSource.MANUAL,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user: Mapped["User"] = relationship(back_populates="tasks")  # noqa: F821
    course: Mapped["Course | None"] = relationship(back_populates="tasks")  # noqa: F821

