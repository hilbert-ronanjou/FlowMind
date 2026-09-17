"""Create users, courses, and tasks.

Revision ID: 20260914_0001
Revises:
Create Date: 2026-09-14
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260914_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=True),
        sa.Column("teacher", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "name", name="uq_courses_user_name"),
    )
    op.create_index("ix_courses_user_id", "courses", ["user_id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "priority",
            sa.Enum("LOW", "MEDIUM", "HIGH", name="task_priority", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("TODO", "IN_PROGRESS", "COMPLETED", name="task_status", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum("MANUAL", "AI", name="task_source", native_enum=False),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_course_id", "tasks", ["course_id"])
    op.create_index("ix_tasks_deadline", "tasks", ["deadline"])


def downgrade() -> None:
    op.drop_table("tasks")
    op.drop_table("courses")
    op.drop_table("users")

