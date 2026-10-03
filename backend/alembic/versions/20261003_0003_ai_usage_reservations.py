"""Add conservative interactive AI admission ledger only."""
from alembic import op
import sqlalchemy as sa

revision = "20261003_0003"
down_revision = "20260920_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_usage_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "operation", "idempotency_key", name="uq_ai_usage_user_operation_key"),
        sa.CheckConstraint("operation IN ('extraction', 'rag_query')", name="ck_ai_usage_operation"),
        sa.CheckConstraint("state IN ('RESERVED', 'DISPATCHED', 'SUCCEEDED', 'FAILED', 'UNCERTAIN', 'CANCELLED')", name="ck_ai_usage_state"),
        sa.CheckConstraint("length(idempotency_key) BETWEEN 1 AND 128", name="ck_ai_usage_key_length"),
        sa.CheckConstraint("length(request_hash) = 64", name="ck_ai_usage_hash_length"),
        sa.CheckConstraint("(state IN ('RESERVED', 'DISPATCHED') AND finished_at IS NULL) OR (state IN ('SUCCEEDED', 'FAILED', 'UNCERTAIN', 'CANCELLED') AND finished_at IS NOT NULL)", name="ck_ai_usage_finished"),
        sa.CheckConstraint("(state IN ('RESERVED', 'CANCELLED') AND dispatched_at IS NULL) OR (state IN ('DISPATCHED', 'SUCCEEDED', 'FAILED', 'UNCERTAIN') AND dispatched_at IS NOT NULL)", name="ck_ai_usage_dispatched"),
        sa.CheckConstraint("dispatched_at IS NULL OR dispatched_at >= created_at", name="ck_ai_usage_dispatch_time"),
        sa.CheckConstraint("finished_at IS NULL OR (finished_at >= created_at AND (dispatched_at IS NULL OR finished_at >= dispatched_at))", name="ck_ai_usage_finish_time"),
    )
    op.create_index("ix_ai_usage_user_created", "ai_usage_reservations", ["user_id", "created_at"])
    op.create_index("ix_ai_usage_created", "ai_usage_reservations", ["created_at"])


def downgrade():
    op.drop_index("ix_ai_usage_created", table_name="ai_usage_reservations")
    op.drop_index("ix_ai_usage_user_created", table_name="ai_usage_reservations")
    op.drop_table("ai_usage_reservations")
