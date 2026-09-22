import hashlib
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.database import SessionLocal
from app.models.document import (
    EMBEDDING_DIMENSIONS,
    Document,
    DocumentChunk,
    DocumentStatus,
)
from app.services.embedding import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingResponseError,
    embed_texts,
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
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = normalize_page_text(page.extract_text() or "")
            except Exception as exc:
                raise DocumentProcessingError(
                    "The PDF is damaged or cannot be read."
                ) from exc
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
    chunks: list[ChunkDraft], *, batch_size: int
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        batch_vectors = embed_texts([chunk.content for chunk in batch])
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
) -> None:
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if document is None or document.status != DocumentStatus.PROCESSING:
            return
        try:
            db.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            db.add_all(
                [
                    DocumentChunk(
                        document_id=document_id,
                        chunk_index=index,
                        page_number=chunk.page_number,
                        content=chunk.content,
                        embedding=vector,
                    )
                    for index, (chunk, vector) in enumerate(
                        zip(chunks, vectors, strict=True)
                    )
                ]
            )
            document.status = DocumentStatus.READY
            document.failure_reason = None
            document.updated_at = datetime.now(UTC)
            db.commit()
        except Exception:
            db.rollback()
            raise


def _mark_document_failed(document_id: int, reason: str) -> None:
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if document is None:
            return
        try:
            db.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            document.status = DocumentStatus.FAILED
            document.failure_reason = reason
            document.updated_at = datetime.now(UTC)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not mark document %s as failed", document_id)


def process_document(document_id: int) -> None:
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if document is None or document.status != DocumentStatus.PROCESSING:
            return
        storage_path = document.storage_path

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
        vectors = embed_chunk_drafts(chunks, batch_size=settings.embedding_batch_size)
        _persist_ready_document(document_id, chunks, vectors)
    except DocumentProcessingError as exc:
        _mark_document_failed(document_id, exc.safe_reason)
    except (
        EmbeddingConfigurationError,
        EmbeddingProviderError,
        EmbeddingResponseError,
    ):
        logger.exception("Embedding failed while processing document %s", document_id)
        _mark_document_failed(document_id, EMBEDDING_FAILURE_REASON)
    except Exception:
        logger.exception("Unexpected failure while processing document %s", document_id)
        _mark_document_failed(document_id, GENERIC_FAILURE_REASON)


def recover_stale_processing_documents(*, now: datetime | None = None) -> int:
    settings = get_settings()
    cutoff = (now or datetime.now(UTC)) - timedelta(
        seconds=settings.document_processing_stale_seconds
    )
    with SessionLocal() as db:
        document_ids = list(
            db.scalars(
                select(Document.id).where(
                    Document.status == DocumentStatus.PROCESSING,
                    Document.updated_at < cutoff,
                )
            )
        )
        if not document_ids:
            return 0
        try:
            db.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.document_id.in_(document_ids)
                )
            )
            documents = list(
                db.scalars(select(Document).where(Document.id.in_(document_ids)))
            )
            timestamp = now or datetime.now(UTC)
            for document in documents:
                document.status = DocumentStatus.FAILED
                document.failure_reason = INTERRUPTED_REASON
                document.updated_at = timestamp
            db.commit()
            return len(documents)
        except Exception:
            db.rollback()
            raise
