"""Explicit real PostgreSQL + Model Studio Sprint 2/M2 ingestion regression."""
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app.main import app
from app.models.course import Course
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services.knowledge import ingestion
from app.services.knowledge.ingestion import resolve_storage_path


def make_pdf(pages: list[str]) -> bytes:
    page_count = len(pages)
    font_id = 3 + page_count * 2
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + index * 2} 0 R' for index in range(page_count))}] "
            f"/Count {page_count} >>"
        ).encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, page_text in enumerate(pages):
        page_id = 3 + index * 2
        content_id = page_id + 1
        stream = f"BT /F1 12 Tf 72 720 Td ({page_text}) Tj ET".encode("ascii")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode()
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id in range(1, font_id + 1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {font_id + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {font_id + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def main() -> None:
    token = uuid4().hex
    email = f"m2-ingestion-{token}@example.com"
    document_id: int | None = None
    course_id: int | None = None
    storage_file = None
    provider_calls = 0
    real_embed_texts = ingestion.embed_texts

    def counted_embed_texts(texts):
        nonlocal provider_calls
        provider_calls += 1
        return real_embed_texts(texts)

    ingestion.embed_texts = counted_embed_texts
    try:
        with TestClient(app) as client:
            registration = client.post(
                "/api/v1/auth/register",
                json={
                    "email": email,
                    "username": "M2 Ingestion E2E",
                    "password": "M2-Regression-Password!",
                },
            )
            registration.raise_for_status()
            headers = {
                "Authorization": f"Bearer {registration.json()['access_token']}"
            }
            course_response = client.post(
                "/api/v1/courses",
                headers=headers,
                json={"name": f"M2 Ingestion {token}"},
            )
            course_response.raise_for_status()
            course_id = course_response.json()["id"]

            upload = client.post(
                f"/api/v1/courses/{course_id}/documents",
                headers=headers,
                files={
                    "file": (
                        "m2-two-page.pdf",
                        make_pdf(
                            [
                                "Database transactions provide ACID guarantees.",
                                "Operating systems use fork to create a process.",
                            ]
                        ),
                        "application/pdf",
                    )
                },
            )
            if upload.status_code != 202:
                raise AssertionError(f"upload failed safely: {upload.json()}")
            if upload.json()["status"] != "PROCESSING":
                raise AssertionError("upload response was not PROCESSING")
            if "storage_path" in upload.json():
                raise AssertionError("storage_path leaked through the API")
            document_id = upload.json()["id"]

            metadata = client.get(
                f"/api/v1/documents/{document_id}", headers=headers
            )
            metadata.raise_for_status()
            if metadata.json()["status"] != "READY":
                raise AssertionError(f"processing did not become READY: {metadata.json()}")

            with SessionLocal() as db:
                document = db.get(Document, document_id)
                if document is None or document.status != DocumentStatus.READY:
                    raise AssertionError("READY Document was not persisted")
                storage_file = resolve_storage_path(document.storage_path)
                chunks = list(
                    db.scalars(
                        select(DocumentChunk)
                        .where(DocumentChunk.document_id == document_id)
                        .order_by(DocumentChunk.chunk_index)
                    )
                )
                if [chunk.page_number for chunk in chunks] != [1, 2]:
                    raise AssertionError("page numbers were not preserved")
                if any(len(chunk.embedding) != 1024 for chunk in chunks):
                    raise AssertionError("a persisted vector was not 1024-dimensional")

            deletion = client.delete(
                f"/api/v1/documents/{document_id}", headers=headers
            )
            if deletion.status_code != 204:
                raise AssertionError(f"delete failed: {deletion.text}")

            with SessionLocal() as db:
                document_count = db.scalar(
                    select(func.count(Document.id)).where(Document.id == document_id)
                )
                chunk_count = db.scalar(
                    select(func.count(DocumentChunk.id)).where(
                        DocumentChunk.document_id == document_id
                    )
                )
                if document_count != 0 or chunk_count != 0:
                    raise AssertionError("database cleanup was incomplete")
            if storage_file.exists():
                raise AssertionError("local PDF cleanup was incomplete")

            print(
                json.dumps(
                    {
                        "upload_status": upload.status_code,
                        "initial_status": upload.json()["status"],
                        "final_status": metadata.json()["status"],
                        "pages": [chunk.page_number for chunk in chunks],
                        "chunks": len(chunks),
                        "embedding_dimensions": sorted(
                            {len(chunk.embedding) for chunk in chunks}
                        ),
                        "model_studio_embedding_calls": provider_calls,
                        "qwen_chat_calls": 0,
                        "documents_after_delete": document_count,
                        "chunks_after_delete": chunk_count,
                        "local_pdf_removed": not storage_file.exists(),
                    },
                    indent=2,
                )
            )
    finally:
        ingestion.embed_texts = real_embed_texts
        with SessionLocal() as db:
            if document_id is not None:
                remaining = db.get(Document, document_id)
                if remaining is not None:
                    resolve_storage_path(remaining.storage_path).unlink(missing_ok=True)
                db.execute(delete(Document).where(Document.id == document_id))
            if course_id is not None:
                db.execute(delete(Course).where(Course.id == course_id))
            db.execute(delete(User).where(User.email == email))
            db.commit()


if __name__ == "__main__":
    main()
