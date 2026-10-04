import hashlib
import logging
import math
import re
from time import perf_counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from uuid import UUID
from collections.abc import Callable

from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.observability import record_document
from app.database import SessionLocal
from app.models.document import (
    EMBEDDING_DIMENSIONS,
    Document,
    DocumentChunk,
    DocumentStatus,
)
from app.models.document_processing_attempt import DocumentProcessingAttempt
from app.services.embedding import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingResponseError,
    embed_texts,
    require_embedding_configuration,
)
from app.services.ai_guard import failure_state, ensure_paid_ai_enabled
from app.services.document_protection import (
    DocumentProtectionUnavailable, ObsoleteAttempt, processing_transaction, recover_expired,
    claim_attempt, dispatch_batch, finish_attempt,
    execution_guard,
)


logger = logging.getLogger(__name__)

PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
UPLOAD_READ_SIZE = 64 * 1024
INTERRUPTED_REASON = "Document processing was interrupted. Please retry."
MISSING_FILE_REASON = "The stored PDF is missing. Please upload the document again."
EMBEDDING_FAILURE_REASON = "Embedding is temporarily unavailable. Please retry."
GENERIC_FAILURE_REASON = "Document processing failed. Please retry."


class UploadValidationError(ValueError):
    pass


class FileTooLargeError(UploadValidationError):
    pass


class DocumentProcessingError(RuntimeError):
    def __init__(self, safe_reason: str):
        super().__init__(safe_reason)
        self.safe_reason = safe_reason


@dataclass(frozen=True)
class StoredUpload:
    filename: str
    storage_path: str
    content_hash: str
    file_size: int


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class ChunkDraft:
    page_number: int
    content: str


def normalize_original_filename(filename: str | None) -> str:
    normalized = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not normalized or len(normalized) > 255:
        raise UploadValidationError("A valid PDF filename is required.")
    if Path(normalized).suffix.lower() != ".pdf":
        raise UploadValidationError("Only PDF files are supported.")
    return normalized


def validate_pdf_content_type(content_type: str | None) -> None:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized not in PDF_CONTENT_TYPES:
        raise UploadValidationError("Only PDF files are supported.")


def get_storage_root() -> Path:
    root = get_settings().document_storage_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_storage_path(storage_path: str) -> Path:
    relative = Path(storage_path)
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name != storage_path:
        raise DocumentProcessingError(GENERIC_FAILURE_REASON)
    root = get_storage_root()
    target = (root / relative).resolve()
    if target.parent != root:
        raise DocumentProcessingError(GENERIC_FAILURE_REASON)
    return target


async def store_pdf_upload(upload: UploadFile, *, max_bytes: int) -> StoredUpload:
    filename = normalize_original_filename(upload.filename)
    validate_pdf_content_type(upload.content_type)
    storage_path = f"{uuid4().hex}.pdf"
    target = resolve_storage_path(storage_path)
    temporary = target.with_suffix(".uploading")
    digest = hashlib.sha256()
    total = 0
    signature = bytearray()

    try:
        with temporary.open("xb") as destination:
            while chunk := await upload.read(UPLOAD_READ_SIZE):
                total += len(chunk)
                if total > max_bytes:
                    raise FileTooLargeError("PDF files must not exceed 20 MB.")
                if len(signature) < 5:
                    signature.extend(chunk[: 5 - len(signature)])
                digest.update(chunk)
                destination.write(chunk)
        if bytes(signature) != b"%PDF-":
            raise UploadValidationError("The uploaded file is not a valid PDF.")
        temporary.replace(target)
        return StoredUpload(
            filename=filename,
            storage_path=storage_path,
            content_hash=digest.hexdigest(),
            file_size=total,
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def remove_stored_pdf(storage_path: str) -> None:
    resolve_storage_path(storage_path).unlink(missing_ok=True)


def stored_pdf_exists(storage_path: str) -> bool:
    return resolve_storage_path(storage_path).is_file()


def normalize_page_text(text: str) -> str:
    # PDF extraction may emit NUL, which PostgreSQL text columns cannot store.
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    lines = [re.sub(r"[ \f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_pdf_pages(path: Path) -> list[PageText]:
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as exc:
                raise DocumentProcessingError(
                    "The PDF is encrypted and cannot be read."
                ) from exc
            if unlocked == 0:
                raise DocumentProcessingError("The PDF is encrypted and cannot be read.")

        pages: list[PageText] = []
        settings = get_settings()
        if len(reader.pages) > settings.document_max_pages:
            raise DocumentProcessingError("PDF page limit exceeded. Split the PDF into smaller files.")
        total_chars = 0
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = normalize_page_text(page.extract_text() or "")
            except Exception as exc:
                raise DocumentProcessingError(
                    "The PDF is damaged or cannot be read."
                ) from exc
            total_chars += len(text)
            if total_chars > settings.document_max_text_chars:
                raise DocumentProcessingError("PDF text limit exceeded. Split the PDF into smaller files.")
            pages.append(PageText(page_number=page_number, text=text))
    except DocumentProcessingError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise DocumentProcessingError("The PDF is damaged or cannot be read.") from exc

    if not any(page.text for page in pages):
        raise DocumentProcessingError(
            "No extractable text was found. Upload a text-based PDF instead of a scanned PDF."
        )
    return pages


def split_page_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk overlap must be non-negative and smaller than chunk size")
    normalized = normalize_page_text(text)
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    separators = ("\n\n", "\n", "。", ". ", " ")
    while start < len(normalized):
        hard_end = min(start + chunk_size, len(normalized))
        end = hard_end
        if hard_end < len(normalized):
            minimum_break = start + max(chunk_size // 2, overlap + 1)
            for separator in separators:
                candidate = normalized.rfind(separator, minimum_break, hard_end)
                if candidate >= minimum_break:
                    end = candidate + len(separator)
                    break
        content = normalized[start:end].strip()
        if content:
            chunks.append(content)
        if end >= len(normalized):
            break
        next_start = max(end - overlap, start + 1)
        while next_start < end and normalized[next_start].isspace():
            next_start += 1
        start = next_start
    return chunks


def build_chunk_drafts(
    pages: list[PageText], *, chunk_size: int, overlap: int
) -> list[ChunkDraft]:
    chunks = [
        ChunkDraft(page_number=page.page_number, content=content)
        for page in pages
        for content in split_page_text(
            page.text, chunk_size=chunk_size, overlap=overlap
        )
    ]
    if not chunks:
        raise DocumentProcessingError(
            "No extractable text was found. Upload a text-based PDF instead of a scanned PDF."
        )
    return chunks


def embed_chunk_drafts(
    chunks: list[ChunkDraft], *, batch_size: int, before_batch: Callable[[], None]
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        before_batch()  # Committed fencing/dispatch gate before EVERY paid batch.
        batch_vectors = embed_texts([chunk.content for chunk in batch], max_retries=0)
        if len(batch_vectors) != len(batch):
            raise EmbeddingResponseError("Embedding provider returned incomplete data")
        for vector in batch_vectors:
            if len(vector) != EMBEDDING_DIMENSIONS or not all(
                math.isfinite(value) for value in vector
            ):
                raise EmbeddingResponseError("Embedding provider returned invalid data")
        vectors.extend(batch_vectors)
    return vectors


def _persist_ready_document(
    document_id: int,
    chunks: list[ChunkDraft],
    vectors: list[list[float]],
    attempt_id: UUID,
) -> None:
    with SessionLocal() as db:
        with processing_transaction(db) as now:
            finish_attempt(db, document_id, attempt_id, now, chunks=chunks, vectors=vectors)


def _mark_document_failed(document_id: int, reason: str, attempt_id: UUID, failure: str = "FAILED") -> None:
    try:
        with SessionLocal() as db:
            with processing_transaction(db) as now:
                finish_attempt(db, document_id, attempt_id, now, reason=reason, failure=failure)
    except ObsoleteAttempt:
        pass  # In particular, never overwrite a new attempt's failure/success.
    except Exception:
        logger.error("Document failure state could not be saved", extra={"event": "document_failure_state_error"})


def process_document(document_id: int, attempt_id: UUID) -> None:
    try:
        with SessionLocal() as db:
            attempt = db.get(DocumentProcessingAttempt, attempt_id)
            if attempt is None or attempt.document_id != document_id:
                return
            user_id = attempt.user_id
            db.rollback()  # Do not retain this read transaction during execution.
            with execution_guard(db.get_bind(), document_id, user_id) as check_execution:
                _process_document(document_id, attempt_id, check_execution)
    except ObsoleteAttempt:
        try:
            recover_stale_processing_documents()
        except Exception:
            logger.error("Document recovery failed", extra={"event": "document_recovery_failed"})
        return
    except Exception:
        logger.error("Document execution guard failed", extra={"event": "document_claim_failed"})


def _process_document(document_id: int, attempt_id: UUID, check_execution: Callable[[], None]) -> None:
    try:
        with SessionLocal() as db:
            with processing_transaction(db) as now:
                storage_path = claim_attempt(db, document_id, attempt_id, now)
    except ObsoleteAttempt:
        raise
    except Exception:
        logger.error("Document claim failed", extra={"event": "document_claim_failed"})
        return  # No acknowledged claim, no provider work.

    started = perf_counter()
    outcome = "failure"
    logger.info("Document processing started", extra={"event": "document_processing_started"})
    try:
        path = resolve_storage_path(storage_path)
        if not path.is_file():
            raise DocumentProcessingError(MISSING_FILE_REASON)
        settings = get_settings()
        pages = extract_pdf_pages(path)
        chunks = build_chunk_drafts(
            pages,
            chunk_size=settings.document_chunk_size_chars,
            overlap=settings.document_chunk_overlap_chars,
        )
        if len(chunks) > settings.document_max_chunks:
            raise DocumentProcessingError("PDF chunk limit exceeded. Split the PDF into smaller files.")
        ensure_paid_ai_enabled()
        require_embedding_configuration()

        def before_batch():
            check_execution()
            with SessionLocal() as db:
                with processing_transaction(db) as now:
                    dispatch_batch(db, document_id, attempt_id, now)

        vectors = embed_chunk_drafts(chunks, batch_size=settings.embedding_batch_size, before_batch=before_batch)
        _persist_ready_document(document_id, chunks, vectors, attempt_id)
        outcome = "success"
    except ObsoleteAttempt:
        raise
    except DocumentProcessingError as exc:
        _mark_document_failed(document_id, exc.safe_reason, attempt_id)
    except (
        EmbeddingConfigurationError,
        EmbeddingProviderError,
        EmbeddingResponseError,
    ) as exc:
        logger.error("Document embedding failed", extra={"event": "document_embedding_failed"})
        _mark_document_failed(document_id, EMBEDDING_FAILURE_REASON, attempt_id, failure_state(exc).value)
    except Exception as exc:
        logger.error("Document processing failed", extra={"event": "document_processing_failed"})
        state = "FAILED" if isinstance(exc, DocumentProtectionUnavailable) else failure_state(exc).value
        _mark_document_failed(document_id, GENERIC_FAILURE_REASON, attempt_id, state)
    finally:
        record_document(outcome, perf_counter() - started)
        logger.info(
            "Document processing finished",
            extra={
                "event": "document_processing_completed" if outcome == "success" else "document_processing_failed",
                "outcome": outcome,
            },
        )


def recover_stale_processing_documents(*, now: datetime | None = None) -> int:
    settings = get_settings()
    with SessionLocal() as db:
        with processing_transaction(db) as database_now:
            timestamp = now or database_now
            cutoff = timestamp - timedelta(seconds=settings.document_processing_stale_seconds)
            recovered = recover_expired(db, timestamp)
            documents = list(db.scalars(select(Document).where(
                Document.status == DocumentStatus.PROCESSING,
                Document.current_attempt_id.is_(None),
                Document.updated_at < cutoff,
            ).with_for_update()))
            if not documents:
                return recovered
            db.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.document_id.in_([document.id for document in documents])
                )
            )
            for document in documents:
                document.status = DocumentStatus.FAILED
                document.failure_reason = INTERRUPTED_REASON
                document.updated_at = timestamp
            return recovered + len(documents)
