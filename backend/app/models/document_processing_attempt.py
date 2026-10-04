"""Non-refundable PDF admissions, separate from interactive AI quotas."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocumentProcessingAttempt(Base):
    __tablename__ = "document_processing_attempts"
    __table_args__ = (
        Index("ix_document_attempt_user_created", "user_id", "created_at"),
        Index("ix_document_attempt_created", "created_at"),
        Index("uq_document_attempt_active_user", "user_id", unique=True,
              postgresql_where=text("finished_at IS NULL"), sqlite_where=text("finished_at IS NULL")),
        Index("uq_document_attempt_active_document", "document_id", unique=True,
              postgresql_where=text("finished_at IS NULL"), sqlite_where=text("finished_at IS NULL")),
        CheckConstraint("state IN ('RESERVED', 'RUNNING', 'DISPATCHED', 'SUCCEEDED', 'FAILED', 'UNCERTAIN', 'CANCELLED')", name="ck_document_attempt_state"),
        CheckConstraint("(state IN ('RESERVED', 'RUNNING', 'DISPATCHED') AND finished_at IS NULL) OR (state IN ('SUCCEEDED', 'FAILED', 'UNCERTAIN', 'CANCELLED') AND finished_at IS NOT NULL)", name="ck_document_attempt_finished"),
        CheckConstraint("(state IN ('RESERVED', 'RUNNING', 'CANCELLED') AND dispatched_at IS NULL) OR (state IN ('DISPATCHED', 'SUCCEEDED', 'FAILED', 'UNCERTAIN') AND dispatched_at IS NOT NULL)", name="ck_document_attempt_dispatched"),
        CheckConstraint("expires_at > created_at", name="ck_document_attempt_expiry"),
        CheckConstraint("dispatched_at IS NULL OR dispatched_at >= created_at", name="ck_document_attempt_dispatch_time"),
        CheckConstraint("finished_at IS NULL OR (finished_at >= created_at AND (dispatched_at IS NULL OR finished_at >= dispatched_at))", name="ck_document_attempt_finish_time"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # Deletion must not erase daily debits. Ownership still derives via Course.
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
