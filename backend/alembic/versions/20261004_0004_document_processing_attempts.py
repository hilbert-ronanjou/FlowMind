"""Separate PDF admission ledger and current-attempt fencing token."""
from alembic import op
import sqlalchemy as sa

revision = "20261004_0004"
down_revision = "20261003_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("current_attempt_id", sa.Uuid(), nullable=True))
    op.create_table(
        "document_processing_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('RESERVED', 'RUNNING', 'DISPATCHED', 'SUCCEEDED', 'FAILED', 'UNCERTAIN', 'CANCELLED')", name="ck_document_attempt_state"),
        sa.CheckConstraint("(state IN ('RESERVED', 'RUNNING', 'DISPATCHED') AND finished_at IS NULL) OR (state IN ('SUCCEEDED', 'FAILED', 'UNCERTAIN', 'CANCELLED') AND finished_at IS NOT NULL)", name="ck_document_attempt_finished"),
        sa.CheckConstraint("(state IN ('RESERVED', 'RUNNING', 'CANCELLED') AND dispatched_at IS NULL) OR (state IN ('DISPATCHED', 'SUCCEEDED', 'FAILED', 'UNCERTAIN') AND dispatched_at IS NOT NULL)", name="ck_document_attempt_dispatched"),
        sa.CheckConstraint("expires_at > created_at", name="ck_document_attempt_expiry"),
        sa.CheckConstraint("dispatched_at IS NULL OR dispatched_at >= created_at", name="ck_document_attempt_dispatch_time"),
        sa.CheckConstraint("finished_at IS NULL OR (finished_at >= created_at AND (dispatched_at IS NULL OR finished_at >= dispatched_at))", name="ck_document_attempt_finish_time"),
    )
    op.create_index("ix_document_attempt_user_created", "document_processing_attempts", ["user_id", "created_at"])
    op.create_index("ix_document_attempt_created", "document_processing_attempts", ["created_at"])
    op.create_index("uq_document_attempt_active_user", "document_processing_attempts", ["user_id"], unique=True, postgresql_where=sa.text("finished_at IS NULL"))
    op.create_index("uq_document_attempt_active_document", "document_processing_attempts", ["document_id"], unique=True, postgresql_where=sa.text("finished_at IS NULL"))
    # Legacy in-process work has no admission token and must not resume for free.
    op.execute("UPDATE documents SET status = 'FAILED', failure_reason = 'Document processing was interrupted. Please retry.' WHERE status = 'PROCESSING'")


def downgrade():
    op.drop_table("document_processing_attempts")
    op.drop_column("documents", "current_attempt_id")
