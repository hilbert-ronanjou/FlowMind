from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_courses_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str | None] = mapped_column(String(40))
    teacher: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user: Mapped["User"] = relationship(back_populates="courses")  # noqa: F821
    tasks: Mapped[list["Task"]] = relationship(back_populates="course")  # noqa: F821

