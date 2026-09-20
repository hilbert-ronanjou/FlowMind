"""Add the Course knowledge foundation.

Revision ID: 20260920_0002
Revises: 20260914_0001
Create Date: 2026-09-20
"""
from collections.abc import Sequence

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa

revision: str = "20260920_0002"
down_revision: str | None = "20260914_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


document_status = sa.Enum(
    "PROCESSING",
    "READY",
    "FAILED",
    name="document_status",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("status", document_status, nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(filename)) > 0", name="ck_documents_filename_not_blank"
        ),
        sa.CheckConstraint(
            "length(trim(storage_path)) > 0",
            name="ck_documents_storage_path_not_blank",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_documents_content_hash_sha256"
        ),
        sa.CheckConstraint(
            "file_size >= 0", name="ck_documents_file_size_nonnegative"
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "course_id", "content_hash", name="uq_documents_course_content_hash"
        ),
    )
    op.create_index(
        "ix_documents_course_status",
        "documents",
        ["course_id", "status"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "chunk_index >= 0", name="ck_document_chunks_index_nonnegative"
        ),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name="ck_document_chunks_page_number_positive",
        ),
        sa.CheckConstraint(
            "length(trim(content)) > 0", name="ck_document_chunks_content_not_blank"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_index"
        ),
    )


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_course_status", table_name="documents")
    op.drop_table("documents")
    # Keep the shared extension installed: dropping it could remove objects owned by
    # another application or migration. This downgrade removes every M1 table.
