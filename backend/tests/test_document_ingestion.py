from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PageObject, PdfWriter
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes import documents as document_routes
from app.core.config import get_settings
from app.models.course import Course
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services.knowledge import ingestion
from app.services.knowledge.ingestion import (
    ChunkDraft,
    PageText,
    build_chunk_drafts,
    extract_pdf_pages,
    process_document,
    recover_stale_processing_documents,
    resolve_storage_path,
    split_page_text,
    normalize_original_filename,
    normalize_page_text,
)
from conftest import TEST_STORAGE_ROOT, TestingSession, register_user


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
        escaped = page_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
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


def make_encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def create_course(client: TestClient, headers: dict[str, str], name: str) -> int:
    response = client.post("/api/v1/courses", headers=headers, json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def add_document(
    db: Session,
    *,
    course: Course,
    filename: str,
    content_hash: str,
    status: DocumentStatus,
    content: bytes | None = None,
    updated_at: datetime | None = None,
) -> Document:
    storage_path = f"{content_hash[:16]}-{filename}"
    document = Document(
        course=course,
        filename=filename,
        storage_path=storage_path,
        content_hash=content_hash,
        file_size=len(content or b"pdf"),
        status=status,
        failure_reason="Previous processing failure" if status == DocumentStatus.FAILED else None,
        updated_at=updated_at or datetime.now(UTC),
    )
    if content is not None:
        path = resolve_storage_path(storage_path)
        path.write_bytes(content)
    db.add(document)
    db.flush()
    return document


def test_pdf_parser_preserves_one_based_page_numbers(tmp_path: Path):
    path = tmp_path / "two-pages.pdf"
    path.write_bytes(make_pdf(["Database transactions and ACID", "Operating system fork process"]))

    pages = extract_pdf_pages(path)

    assert [(page.page_number, page.text) for page in pages] == [
        (1, "Database transactions and ACID"),
        (2, "Operating system fork process"),
    ]


def test_page_aware_chunking_and_overlap():
    text_value = "A" * 90
    chunks = split_page_text(text_value, chunk_size=40, overlap=10)
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert chunks[0][-10:] == chunks[1][:10]

    drafts = build_chunk_drafts(
        [PageText(1, "A" * 70), PageText(2, "B" * 70)],
        chunk_size=40,
        overlap=10,
    )
    assert {draft.page_number for draft in drafts} == {1, 2}
    assert all(not ("A" in draft.content and "B" in draft.content) for draft in drafts)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("辽宁省博物馆\x00赓续家国情怀", "辽宁省博物馆赓续家国情怀"),
        ("\x00First\x00\x00 paragraph\x00", "First paragraph"),
        ("Normal text\nSecond line\n\nNext paragraph", "Normal text\nSecond line\n\nNext paragraph"),
        # Keep the existing whitespace policy, not a new control-character filter.
        ("First\x00\tline\r\nSecond line", "First line\nSecond line"),
        ("First\tline\r\nSecond line", "First line\nSecond line"),
        ("A\x01B\x00C", "A\x01BC"),
        ("\x00\x00", ""),
    ],
)
def test_page_text_normalization_removes_only_nul(raw: str, expected: str):
    assert normalize_page_text(raw) == expected


def test_ingestion_normalizes_extracted_nul_before_chunking_and_embedding(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    user = User(email="nul-pipeline@example.com", username="NUL", password_hash="hash")
    course = Course(user=user, name="NUL regression")
    document = add_document(
        db,
        course=course,
        filename="nul.pdf",
        content_hash="f" * 64,
        status=DocumentStatus.PROCESSING,
        content=make_pdf(["First page", "Second page"]),
    )
    db.commit()

    extracted = iter(["辽宁省博物馆\x00赓续家国情怀", "Normal text\nSecond line"])
    monkeypatch.setattr(PageObject, "extract_text", lambda _page: next(extracted))
    expected = ["辽宁省博物馆赓续家国情怀", "Normal text\nSecond line"]
    real_build_chunk_drafts = ingestion.build_chunk_drafts
    embedded: list[str] = []

    def checked_build(pages, **kwargs):
        assert [page.text for page in pages] == expected
        assert [page.page_number for page in pages] == [1, 2]
        return real_build_chunk_drafts(pages, **kwargs)

    def fake_embed(texts: list[str]) -> list[list[float]]:
        assert all("\x00" not in value for value in texts)
        embedded.extend(texts)
        return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(ingestion, "build_chunk_drafts", checked_build)
    monkeypatch.setattr(ingestion, "embed_texts", fake_embed)
    process_document(document.id)

    db.expire_all()
    saved = db.get(Document, document.id)
    chunks = list(db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index)
    ))
    assert saved is not None and saved.status == DocumentStatus.READY
    assert saved.failure_reason is None
    assert embedded == expected
    assert [chunk.content for chunk in chunks] == expected
    assert [chunk.page_number for chunk in chunks] == [1, 2]
    assert all("\x00" not in chunk.content for chunk in chunks)


def test_user_filename_never_controls_storage_path():
    assert normalize_original_filename("../../private/notes.pdf") == "notes.pdf"
    assert normalize_original_filename(r"C:\\fakepath\\notes.pdf") == "notes.pdf"


def test_processing_pipeline_batches_embeddings_and_becomes_ready(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    user = User(email="pipeline@example.com", username="Pipeline", password_hash="hash")
    course = Course(user=user, name="Pipeline Course")
    document = add_document(
        db,
        course=course,
        filename="two-pages.pdf",
        content_hash="1" * 64,
        status=DocumentStatus.PROCESSING,
        content=make_pdf(["Database transactions and ACID", "Operating system fork process"]),
    )
    db.commit()

    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [[float(index == 0)] + [0.0] * 1023 for index, _ in enumerate(texts)]

    monkeypatch.setattr(ingestion, "SessionLocal", TestingSession)
    monkeypatch.setattr(ingestion, "embed_texts", fake_embed)
    monkeypatch.setattr(get_settings(), "embedding_batch_size", 16)

    process_document(document.id)

    db.expire_all()
    saved = db.get(Document, document.id)
    chunks = list(
        db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        )
    )
    assert saved is not None and saved.status == DocumentStatus.READY
    assert [chunk.page_number for chunk in chunks] == [1, 2]
    assert all(len(chunk.embedding) == 1024 for chunk in chunks)
    assert len(calls) == 1
    assert len(calls[0]) == 2


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        (b"%PDF-1.4\nnot-a-real-pdf", "damaged"),
        (make_pdf([""]), "No extractable text"),
        (make_encrypted_pdf(), "encrypted"),
    ],
)
def test_corrupted_or_empty_pdf_becomes_failed_without_chunks(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    expected_reason: str,
):
    user = User(email=f"failure-{len(payload)}@example.com", username="Failure", password_hash="hash")
    course = Course(user=user, name="Failure Course")
    document = add_document(
        db,
        course=course,
        filename="bad.pdf",
        content_hash=f"{len(payload):064x}",
        status=DocumentStatus.PROCESSING,
        content=payload,
    )
    db.commit()
    monkeypatch.setattr(ingestion, "SessionLocal", TestingSession)

    process_document(document.id)

    db.expire_all()
    saved = db.get(Document, document.id)
    assert saved is not None and saved.status == DocumentStatus.FAILED
    assert expected_reason.lower() in (saved.failure_reason or "").lower()
    assert db.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id)
    ) is None


def test_invalid_embedding_dimension_marks_failed_and_removes_chunks(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    user = User(email="dimension@example.com", username="Dimension", password_hash="hash")
    course = Course(user=user, name="Dimension Course")
    document = add_document(
        db,
        course=course,
        filename="dimension.pdf",
        content_hash="2" * 64,
        status=DocumentStatus.PROCESSING,
        content=make_pdf(["Text for embedding"]),
    )
    db.commit()
    monkeypatch.setattr(ingestion, "SessionLocal", TestingSession)
    monkeypatch.setattr(ingestion, "embed_texts", lambda _texts: [[0.0] * 8])

    process_document(document.id)

    db.expire_all()
    saved = db.get(Document, document.id)
    assert saved is not None and saved.status == DocumentStatus.FAILED
    assert saved.failure_reason == ingestion.EMBEDDING_FAILURE_REASON
    assert db.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id)
    ) is None


def test_ready_persistence_rolls_back_as_one_transaction(db: Session, monkeypatch: pytest.MonkeyPatch):
    user = User(email="rollback@example.com", username="Rollback", password_hash="hash")
    course = Course(user=user, name="Rollback Course")
    document = add_document(
        db,
        course=course,
        filename="rollback.pdf",
        content_hash="3" * 64,
        status=DocumentStatus.PROCESSING,
        content=make_pdf(["Rollback text"]),
    )
    document.chunks = [
        DocumentChunk(
            chunk_index=0,
            page_number=1,
            content="existing retry chunk",
            embedding=[0.0] * 1024,
        )
    ]
    db.commit()
    db.execute(
        text(
            "CREATE TRIGGER reject_new_chunks BEFORE INSERT ON document_chunks "
            "BEGIN SELECT RAISE(ABORT, 'forced chunk failure'); END"
        )
    )
    db.commit()
    monkeypatch.setattr(ingestion, "SessionLocal", TestingSession)

    with pytest.raises(IntegrityError):
        ingestion._persist_ready_document(
            document.id,
            [ChunkDraft(page_number=1, content="replacement")],
            [[1.0] + [0.0] * 1023],
        )

    db.expire_all()
    saved = db.get(Document, document.id)
    chunks = list(
        db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id))
    )
    assert saved is not None and saved.status == DocumentStatus.PROCESSING
    assert [chunk.content for chunk in chunks] == ["existing retry chunk"]


def test_upload_contract_duplicate_and_same_name_rules(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    token = register_user(client, "upload@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    first_course = create_course(client, headers, "First Course")
    second_course = create_course(client, headers, "Second Course")
    monkeypatch.setattr(document_routes, "process_document", lambda _document_id: None)
    pdf = make_pdf(["Database transactions"])

    created = client.post(
        f"/api/v1/courses/{first_course}/documents",
        headers=headers,
        files={"file": ("notes.pdf", pdf, "application/pdf")},
    )
    assert created.status_code == 202
    assert created.json()["status"] == "PROCESSING"
    assert "storage_path" not in created.json()
    assert "storage_path" not in client.get(
        f"/api/v1/documents/{created.json()['id']}", headers=headers
    ).json()
    assert all(
        "storage_path" not in item
        for item in client.get(
            f"/api/v1/courses/{first_course}/documents", headers=headers
        ).json()
    )

    duplicate = client.post(
        f"/api/v1/courses/{first_course}/documents",
        headers=headers,
        files={"file": ("other-name.pdf", pdf, "application/pdf")},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_content"
    assert duplicate.json()["detail"]["existing_document_id"] == created.json()["id"]

    same_name = client.post(
        f"/api/v1/courses/{first_course}/documents",
        headers=headers,
        files={"file": ("notes.pdf", make_pdf(["Different content"]), "application/pdf")},
    )
    assert same_name.status_code == 409
    assert same_name.json()["detail"]["code"] == "same_filename_different_content"

    other_course = client.post(
        f"/api/v1/courses/{second_course}/documents",
        headers=headers,
        files={"file": ("notes.pdf", pdf, "application/pdf")},
    )
    assert other_course.status_code == 202
    assert len(list(TEST_STORAGE_ROOT.glob("*.pdf"))) == 2


def test_upload_rejects_non_pdf_signature_and_measured_size(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    token = register_user(client, "validation@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    course_id = create_course(client, headers, "Validation Course")
    monkeypatch.setattr(document_routes, "process_document", lambda _document_id: None)

    invalid = client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=headers,
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_file"

    wrong_extension = client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=headers,
        files={"file": ("fake.txt", make_pdf(["Text"]), "application/pdf")},
    )
    assert wrong_extension.status_code == 400

    wrong_type = client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=headers,
        files={"file": ("fake.pdf", make_pdf(["Text"]), "text/plain")},
    )
    assert wrong_type.status_code == 400

    oversized = b"%PDF-" + b"x" * (20 * 1024 * 1024)
    too_large = client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=headers,
        files={"file": ("large.pdf", oversized, "application/pdf")},
    )
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "file_too_large"
    assert not list(TEST_STORAGE_ROOT.iterdir())


def test_document_quota_counts_only_ready_and_processing(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    token = register_user(client, "quota@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    course_id = create_course(client, headers, "Quota Course")
    course = db.get(Course, course_id)
    assert course is not None
    for index in range(20):
        add_document(
            db,
            course=course,
            filename=f"active-{index}.pdf",
            content_hash=f"{index:064x}",
            status=DocumentStatus.READY if index % 2 else DocumentStatus.PROCESSING,
        )
    db.commit()
    monkeypatch.setattr(document_routes, "process_document", lambda _document_id: None)

    rejected = client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=headers,
        files={"file": ("extra.pdf", make_pdf(["Extra"]), "application/pdf")},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "document_limit_reached"

    db.query(Document).update({Document.status: DocumentStatus.FAILED})
    db.commit()
    allowed = client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=headers,
        files={"file": ("extra.pdf", make_pdf(["Extra"]), "application/pdf")},
    )
    assert allowed.status_code == 202


def test_document_apis_enforce_cross_user_404(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    first = register_user(client, "document-owner@example.com")
    second = register_user(client, "document-intruder@example.com")
    owner_headers = {"Authorization": f"Bearer {first['access_token']}"}
    intruder_headers = {"Authorization": f"Bearer {second['access_token']}"}
    course_id = create_course(client, owner_headers, "Private Documents")
    course = db.get(Course, course_id)
    assert course is not None
    document = add_document(
        db,
        course=course,
        filename="private.pdf",
        content_hash="a" * 64,
        status=DocumentStatus.FAILED,
        content=make_pdf(["Private text"]),
    )
    db.commit()
    monkeypatch.setattr(document_routes, "process_document", lambda _document_id: None)

    assert client.get(
        f"/api/v1/courses/{course_id}/documents", headers=intruder_headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/documents/{document.id}", headers=intruder_headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/documents/{document.id}/file", headers=intruder_headers
    ).status_code == 404
    assert client.delete(
        f"/api/v1/documents/{document.id}", headers=intruder_headers
    ).status_code == 404
    assert client.post(
        f"/api/v1/documents/{document.id}/retry", headers=intruder_headers
    ).status_code == 404
    assert client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=intruder_headers,
        files={"file": ("intrusion.pdf", make_pdf(["Intrusion"]), "application/pdf")},
    ).status_code == 404


def test_owner_can_open_pdf_without_storage_path_disclosure(
    client: TestClient, db: Session
):
    token = register_user(client, "file-owner@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    course_id = create_course(client, headers, "File Course")
    course = db.get(Course, course_id)
    assert course is not None
    pdf = make_pdf(["Secure source PDF"])
    document = add_document(
        db,
        course=course,
        filename="course source.pdf",
        content_hash="f" * 64,
        status=DocumentStatus.READY,
        content=pdf,
    )
    storage_path = document.storage_path
    db.commit()

    response = client.get(
        f"/api/v1/documents/{document.id}/file", headers=headers
    )

    assert response.status_code == 200
    assert response.content == pdf
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert "course%20source.pdf" in response.headers["content-disposition"]
    assert storage_path not in str(response.headers)


def test_document_file_missing_and_unsafe_paths_are_safe(
    client: TestClient, db: Session, tmp_path: Path
):
    token = register_user(client, "file-safety@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    course_id = create_course(client, headers, "File Safety")
    course = db.get(Course, course_id)
    assert course is not None
    missing = add_document(
        db,
        course=course,
        filename="missing.pdf",
        content_hash="1" * 64,
        status=DocumentStatus.READY,
    )
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"private outside data")
    unsafe = add_document(
        db,
        course=course,
        filename="unsafe.pdf",
        content_hash="2" * 64,
        status=DocumentStatus.READY,
    )
    unsafe.storage_path = str(outside.resolve())
    db.commit()

    missing_response = client.get(
        f"/api/v1/documents/{missing.id}/file", headers=headers
    )
    unsafe_response = client.get(
        f"/api/v1/documents/{unsafe.id}/file", headers=headers
    )

    assert missing_response.status_code == 404
    assert missing_response.json() == {
        "detail": {
            "code": "source_file_missing",
            "message": "The stored PDF is unavailable.",
        }
    }
    assert unsafe_response.status_code == 404
    assert b"private outside data" not in unsafe_response.content
    assert str(outside).encode() not in unsafe_response.content


def test_delete_removes_chunks_and_local_file(
    client: TestClient, db: Session
):
    token = register_user(client, "delete@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    course_id = create_course(client, headers, "Delete Course")
    course = db.get(Course, course_id)
    assert course is not None
    document = add_document(
        db,
        course=course,
        filename="delete.pdf",
        content_hash="b" * 64,
        status=DocumentStatus.READY,
        content=make_pdf(["Delete text"]),
    )
    document.chunks = [
        DocumentChunk(
            chunk_index=0,
            page_number=1,
            content="Delete text",
            embedding=[0.0] * 1024,
        )
    ]
    document_id = document.id
    storage_path = resolve_storage_path(document.storage_path)
    db.commit()

    response = client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    assert response.status_code == 204
    db.expire_all()
    assert db.get(Document, document_id) is None
    assert db.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == document_id)
    ) is None
    assert not storage_path.exists()


def test_retry_rules_and_missing_source(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    token = register_user(client, "retry@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    course_id = create_course(client, headers, "Retry Course")
    course = db.get(Course, course_id)
    assert course is not None
    failed = add_document(
        db,
        course=course,
        filename="failed.pdf",
        content_hash="c" * 64,
        status=DocumentStatus.FAILED,
        content=make_pdf(["Retry text"]),
    )
    ready = add_document(
        db,
        course=course,
        filename="ready.pdf",
        content_hash="d" * 64,
        status=DocumentStatus.READY,
    )
    processing = add_document(
        db,
        course=course,
        filename="processing.pdf",
        content_hash="e" * 64,
        status=DocumentStatus.PROCESSING,
    )
    missing = add_document(
        db,
        course=course,
        filename="missing.pdf",
        content_hash="f" * 64,
        status=DocumentStatus.FAILED,
    )
    db.commit()
    monkeypatch.setattr(document_routes, "process_document", lambda _document_id: None)

    retried = client.post(f"/api/v1/documents/{failed.id}/retry", headers=headers)
    assert retried.status_code == 202
    assert retried.json()["status"] == "PROCESSING"
    assert retried.json()["failure_reason"] is None
    assert client.post(
        f"/api/v1/documents/{ready.id}/retry", headers=headers
    ).status_code == 409
    assert client.post(
        f"/api/v1/documents/{processing.id}/retry", headers=headers
    ).status_code == 409
    assert client.delete(
        f"/api/v1/documents/{processing.id}", headers=headers
    ).status_code == 409
    missing_response = client.post(
        f"/api/v1/documents/{missing.id}/retry", headers=headers
    )
    assert missing_response.status_code == 409
    assert missing_response.json()["detail"]["code"] == "source_file_missing"
    db.expire_all()
    assert db.get(Document, missing.id).status == DocumentStatus.FAILED


def test_retry_respects_course_document_quota(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    token = register_user(client, "retry-quota@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    course_id = create_course(client, headers, "Retry Quota Course")
    course = db.get(Course, course_id)
    assert course is not None
    failed = add_document(
        db,
        course=course,
        filename="retry-after-space.pdf",
        content_hash="9" * 64,
        status=DocumentStatus.FAILED,
        content=make_pdf(["Retry after deleting an active document"]),
    )
    for index in range(20):
        add_document(
            db,
            course=course,
            filename=f"quota-{index}.pdf",
            content_hash=f"{100 + index:064x}",
            status=DocumentStatus.READY,
        )
    db.commit()
    monkeypatch.setattr(document_routes, "process_document", lambda _document_id: None)

    response = client.post(
        f"/api/v1/documents/{failed.id}/retry", headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "document_limit_reached"
    db.expire_all()
    assert db.get(Document, failed.id).status == DocumentStatus.FAILED


def test_course_delete_cleans_local_documents_and_blocks_processing_race(
    client: TestClient, db: Session
):
    token = register_user(client, "course-cleanup@example.com")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    course_id = create_course(client, headers, "Course Cleanup")
    course = db.get(Course, course_id)
    assert course is not None
    document = add_document(
        db,
        course=course,
        filename="course-delete.pdf",
        content_hash="6" * 64,
        status=DocumentStatus.PROCESSING,
        content=make_pdf(["Course delete cleanup"]),
    )
    document.chunks = [
        DocumentChunk(
            chunk_index=0,
            page_number=1,
            content="Course delete cleanup",
            embedding=[0.0] * 1024,
        )
    ]
    document_id = document.id
    storage_file = resolve_storage_path(document.storage_path)
    db.commit()

    blocked = client.delete(f"/api/v1/courses/{course_id}", headers=headers)
    assert blocked.status_code == 409
    assert storage_file.exists()

    document.status = DocumentStatus.READY
    db.commit()
    deleted = client.delete(f"/api/v1/courses/{course_id}", headers=headers)

    assert deleted.status_code == 204
    db.expire_all()
    assert db.get(Course, course_id) is None
    assert db.get(Document, document_id) is None
    assert db.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == document_id)
    ) is None
    assert not storage_file.exists()


def test_stale_processing_recovery_marks_failed_and_removes_chunks(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    now = datetime.now(UTC)
    user = User(email="stale@example.com", username="Stale", password_hash="hash")
    course = Course(user=user, name="Stale Course")
    stale = add_document(
        db,
        course=course,
        filename="stale.pdf",
        content_hash="7" * 64,
        status=DocumentStatus.PROCESSING,
        updated_at=now - timedelta(hours=2),
    )
    stale.chunks = [
        DocumentChunk(
            chunk_index=0,
            page_number=1,
            content="partial",
            embedding=[0.0] * 1024,
        )
    ]
    fresh = add_document(
        db,
        course=course,
        filename="fresh.pdf",
        content_hash="8" * 64,
        status=DocumentStatus.PROCESSING,
        updated_at=now,
    )
    db.commit()
    monkeypatch.setattr(ingestion, "SessionLocal", TestingSession)

    recovered = recover_stale_processing_documents(now=now)

    db.expire_all()
    assert recovered == 1
    assert db.get(Document, stale.id).status == DocumentStatus.FAILED
    assert db.get(Document, stale.id).failure_reason == ingestion.INTERRUPTED_REASON
    assert db.get(Document, fresh.id).status == DocumentStatus.PROCESSING
    assert db.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == stale.id)
    ) is None
