"""One conservative admission debit; never stores provider content."""
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Operation(StrEnum):
    EXTRACTION = "extraction"
    RAG_QUERY = "rag_query"


class ReservationState(StrEnum):
    RESERVED = "RESERVED"
    DISPATCHED = "DISPATCHED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    CANCELLED = "CANCELLED"


class AIUsageReservation(Base):
    __tablename__ = "ai_usage_reservations"
    __table_args__ = (
        UniqueConstraint("user_id", "operation", "idempotency_key", name="uq_ai_usage_user_operation_key"),
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
        Index("ix_ai_usage_created", "created_at"),
        CheckConstraint("operation IN ('extraction', 'rag_query')", name="ck_ai_usage_operation"),
        CheckConstraint("state IN ('RESERVED', 'DISPATCHED', 'SUCCEEDED', 'FAILED', 'UNCERTAIN', 'CANCELLED')", name="ck_ai_usage_state"),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 128", name="ck_ai_usage_key_length"),
        CheckConstraint("length(request_hash) = 64", name="ck_ai_usage_hash_length"),
        CheckConstraint("(state IN ('RESERVED', 'DISPATCHED') AND finished_at IS NULL) OR (state IN ('SUCCEEDED', 'FAILED', 'UNCERTAIN', 'CANCELLED') AND finished_at IS NOT NULL)", name="ck_ai_usage_finished"),
        CheckConstraint("(state IN ('RESERVED', 'CANCELLED') AND dispatched_at IS NULL) OR (state IN ('DISPATCHED', 'SUCCEEDED', 'FAILED', 'UNCERTAIN') AND dispatched_at IS NOT NULL)", name="ck_ai_usage_dispatched"),
        CheckConstraint("dispatched_at IS NULL OR dispatched_at >= created_at", name="ck_ai_usage_dispatch_time"),
        CheckConstraint("finished_at IS NULL OR (finished_at >= created_at AND (dispatched_at IS NULL OR finished_at >= dispatched_at))", name="ck_ai_usage_finish_time"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    operation: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
