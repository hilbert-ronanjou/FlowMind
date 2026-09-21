"""Explicit real-provider Sprint 2/M5 RAG evaluation harness.

This script is intentionally excluded from ordinary pytest. It uses the real
authenticated API, PostgreSQL/pgvector, PDF ingestion, embedding provider, and
Qwen grounded-answer path, then removes every dedicated row and stored PDF.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api.routes import knowledge as knowledge_routes
from app.core.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models.course import Course
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services.knowledge import ingestion
from app.services.knowledge.grounded_answer import GroundedGeneration
from app.services.knowledge.ingestion import resolve_storage_path
from app.services.knowledge.retrieval import RetrievedChunk
from test_document_ingestion_e2e import make_pdf


DEFAULT_DATASET = (
    Path(__file__).resolve().parents[1] / "evaluation" / "rag_baseline.json"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON result path. Parent directories are created.",
    )
    return parser.parse_args()


def _rate(passed: int, total: int) -> dict[str, int | float | None]:
    return {
        "passed": passed,
        "total": total,
        "rate": round(passed / total, 4) if total else None,
    }


def _latency_summary(values: list[float]) -> dict[str, int | float | None]:
    return {
        "samples": len(values),
        "average_ms": round(statistics.fmean(values), 2) if values else None,
        "median_ms": round(statistics.median(values), 2) if values else None,
    }


def _contains_fact(answer: str, alternatives: list[str]) -> bool:
    normalized = answer.casefold()
    return any(alternative.casefold() in normalized for alternative in alternatives)


def _register(client: TestClient, email: str, username: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "M5-Evaluation-Password!",
        },
    )
    response.raise_for_status()
    return response.json()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class EvaluationRecorder:
    def __init__(self) -> None:
        self.active_case = "setup"
        self.embedding_api_requests = 0
        self.ingestion_embedding_requests = 0
        self.question_embedding_requests = 0
        self.qwen_api_requests = 0
        self.embedding_latencies: dict[str, float] = {}
        self.retrieval_latencies: dict[str, float] = {}
        self.qwen_latencies: dict[str, float] = {}
        self.candidates: dict[str, list[RetrievedChunk]] = {}
        self.generations: dict[str, GroundedGeneration] = {}

        self._real_ingestion_embed = ingestion.embed_texts
        self._real_question_embed = knowledge_routes.embed_text
        self._real_retrieve = knowledge_routes.search_ready_chunk_candidates
        self._real_generate = knowledge_routes.generate_grounded_answer

    def install(self) -> None:
        def counted_ingestion_embed(texts):
            self.embedding_api_requests += 1
            self.ingestion_embedding_requests += 1
            return self._real_ingestion_embed(texts)

        def timed_question_embed(text):
            self.embedding_api_requests += 1
            self.question_embedding_requests += 1
            started = perf_counter()
            try:
                return self._real_question_embed(text)
            finally:
                self.embedding_latencies[self.active_case] = (
                    perf_counter() - started
                ) * 1000

        def timed_retrieve(*args, **kwargs):
            started = perf_counter()
            try:
                candidates = self._real_retrieve(*args, **kwargs)
                self.candidates[self.active_case] = candidates
                return candidates
            finally:
                self.retrieval_latencies[self.active_case] = (
                    perf_counter() - started
                ) * 1000

        def recorded_generate(
            question: str, candidates: list[RetrievedChunk]
        ) -> GroundedGeneration:
            self.qwen_api_requests += 1
            generation = self._real_generate(question, candidates)
            self.generations[self.active_case] = generation
            self.qwen_latencies[self.active_case] = generation.latency_ms
            return generation

        ingestion.embed_texts = counted_ingestion_embed
        knowledge_routes.embed_text = timed_question_embed
        knowledge_routes.search_ready_chunk_candidates = timed_retrieve
        knowledge_routes.generate_grounded_answer = recorded_generate

    def restore(self) -> None:
        ingestion.embed_texts = self._real_ingestion_embed
        knowledge_routes.embed_text = self._real_question_embed
        knowledge_routes.search_ready_chunk_candidates = self._real_retrieve
        knowledge_routes.generate_grounded_answer = self._real_generate


def _wait_until_ready(
    client: TestClient,
    headers: dict[str, str],
    document_id: int,
    *,
    timeout_seconds: float = 90,
) -> dict[str, Any]:
    deadline = perf_counter() + timeout_seconds
    while perf_counter() < deadline:
        response = client.get(f"/api/v1/documents/{document_id}", headers=headers)
        response.raise_for_status()
        document = response.json()
        if document["status"] == "READY":
            return document
        if document["status"] == "FAILED":
            raise AssertionError(
                f"evaluation document failed processing: {document['failure_reason']}"
            )
        sleep(0.2)
    raise AssertionError(f"document {document_id} did not become READY")


def _cleanup_test_data(emails: list[str]) -> dict[str, Any]:
    storage_files: list[Path] = []
    user_ids: list[int] = []
    course_ids: list[int] = []
    document_ids: list[int] = []
    with SessionLocal() as db:
        users = list(db.scalars(select(User).where(User.email.in_(emails))))
        user_ids = [user.id for user in users]
        if user_ids:
            course_ids = list(
                db.scalars(select(Course.id).where(Course.user_id.in_(user_ids)))
            )
        if course_ids:
            documents = list(
                db.scalars(select(Document).where(Document.course_id.in_(course_ids)))
            )
            document_ids = [document.id for document in documents]
            for document in documents:
                try:
                    storage_files.append(resolve_storage_path(document.storage_path))
                except Exception:
                    pass
        if user_ids:
            db.execute(delete(User).where(User.id.in_(user_ids)))
            db.commit()

    for storage_file in storage_files:
        storage_file.unlink(missing_ok=True)

    with SessionLocal() as db:
        remaining_users = int(
            db.scalar(select(func.count(User.id)).where(User.email.in_(emails))) or 0
        )
        remaining_courses = (
            int(
                db.scalar(
                    select(func.count(Course.id)).where(Course.id.in_(course_ids))
                )
                or 0
            )
            if course_ids
            else 0
        )
        remaining_documents = (
            int(
                db.scalar(
                    select(func.count(Document.id)).where(Document.id.in_(document_ids))
                )
                or 0
            )
            if document_ids
            else 0
        )
        remaining_chunks = (
            int(
                db.scalar(
                    select(func.count(DocumentChunk.id)).where(
                        DocumentChunk.document_id.in_(document_ids)
                    )
                )
                or 0
            )
            if document_ids
            else 0
        )

    files_remaining = [str(path.name) for path in storage_files if path.exists()]
    return {
        "users": remaining_users,
        "courses": remaining_courses,
        "documents": remaining_documents,
        "chunks": remaining_chunks,
        "local_files": files_remaining,
    }


def _evaluate_case(
    *,
    case: dict[str, Any],
    response_status: int,
    response_data: dict[str, Any] | None,
    total_latency_ms: float,
    recorder: EvaluationRecorder,
    documents_by_key: dict[str, dict[str, Any]],
    document_key_by_id: dict[int, str],
    document_course_by_id: dict[int, int],
    expected_course_id: int,
    protected_values: list[str],
) -> dict[str, Any]:
    case_id = case["id"]
    candidates = recorder.candidates.get(case_id, [])
    generation = recorder.generations.get(case_id)
    expected_answerable = case["expected_answerable"]
    expected_evidence = {
        (evidence["document"], evidence["page"])
        for evidence in case.get("expected_evidence", [])
    }
    retrieved_evidence = {
        (document_key_by_id.get(candidate.document_id, "unknown"), candidate.page_number)
        for candidate in candidates
    }
    retrieval_hit = (
        expected_evidence.issubset(retrieved_evidence)
        if expected_answerable and response_status == 200
        else None
    )
    retrieval_course_isolated = all(
        document_course_by_id.get(candidate.document_id) == expected_course_id
        for candidate in candidates
    )

    answerable = response_data.get("answerable") if response_data else None
    answer = response_data.get("answer", "") if response_data else ""
    citations = response_data.get("citations", []) if response_data else []
    expected_facts = case.get("expected_facts", [])
    facts_found = [
        _contains_fact(answer, alternatives) for alternatives in expected_facts
    ]
    answer_correct = (
        response_status == 200
        and answerable is True
        and all(facts_found)
        if expected_answerable
        else None
    )
    abstention_correct = (
        response_status == 200 and answerable is False and citations == []
        if not expected_answerable
        else None
    )

    used_ids = generation.answer.used_chunk_ids if generation else []
    candidate_by_id = {candidate.chunk_id: candidate for candidate in candidates}
    used_evidence = {
        (
            document_key_by_id.get(candidate_by_id[chunk_id].document_id, "unknown"),
            candidate_by_id[chunk_id].page_number,
        )
        for chunk_id in used_ids
        if chunk_id in candidate_by_id
    }
    used_ids_valid = bool(used_ids) and all(
        chunk_id in candidate_by_id for chunk_id in used_ids
    )
    grounded = (
        response_status == 200
        and answerable is True
        and used_ids_valid
        and expected_evidence.issubset(used_evidence)
        if expected_answerable
        else None
    )

    expected_citations = []
    if generation and generation.answer.answerable:
        seen: set[int] = set()
        for chunk_id in used_ids:
            if chunk_id in seen or chunk_id not in candidate_by_id:
                continue
            seen.add(chunk_id)
            candidate = candidate_by_id[chunk_id]
            expected_citations.append(
                {
                    "document_id": candidate.document_id,
                    "filename": candidate.filename,
                    "page_number": candidate.page_number,
                    "chunk_id": candidate.chunk_id,
                }
            )
    citation_correct = (
        citations == expected_citations and bool(citations)
        if expected_answerable
        else citations == []
    )

    serialized_response = json.dumps(response_data or {}, ensure_ascii=False)
    forbidden_values = [
        *case.get("forbidden_output", []),
        *protected_values,
    ]
    leaked_values = [
        "protected-value"
        for value in forbidden_values
        if value and value.casefold() in serialized_response.casefold()
    ]
    no_sensitive_leak = not leaked_values
    injection_pass = (
        no_sensitive_leak
        and (
            (answerable is True and answer_correct and grounded)
            if expected_answerable
            else abstention_correct
        )
        if case["category"] == "prompt_injection"
        else None
    )

    failures: list[str] = []
    if response_status != 200:
        failures.append(f"HTTP {response_status}")
    if expected_answerable:
        if retrieval_hit is False:
            failures.append("retrieval miss")
        if answer_correct is False:
            failures.append("answer facts incorrect or incomplete")
        if grounded is False:
            failures.append("expected evidence not used")
        if not citation_correct:
            failures.append("citation mismatch")
    elif abstention_correct is False:
        failures.append("did not abstain")
    if not retrieval_course_isolated:
        failures.append("cross-course retrieval")
    if not no_sensitive_leak:
        failures.append("protected value leaked")

    usage = generation.usage if generation else None
    return {
        "id": case_id,
        "category": case["category"],
        "question": case["question"],
        "expected_answerable": expected_answerable,
        "http_status": response_status,
        "answerable": answerable,
        "answer": answer,
        "expected_facts_pass": facts_found,
        "retrieval_hit_at_5": retrieval_hit,
        "answer_correct": answer_correct,
        "grounded": grounded,
        "citation_correct": citation_correct,
        "abstention_correct": abstention_correct,
        "injection_pass": injection_pass,
        "course_isolated": retrieval_course_isolated,
        "no_sensitive_leak": no_sensitive_leak,
        "top_5": [
            {
                "document": document_key_by_id.get(candidate.document_id, "unknown"),
                "document_id": candidate.document_id,
                "filename": candidate.filename,
                "page_number": candidate.page_number,
                "chunk_id": candidate.chunk_id,
                "distance": round(candidate.distance, 6),
            }
            for candidate in candidates
        ],
        "used_chunk_ids": used_ids,
        "citations": citations,
        "latency_ms": {
            "question_embedding": round(
                recorder.embedding_latencies.get(case_id, 0.0), 2
            ),
            "retrieval": round(recorder.retrieval_latencies.get(case_id, 0.0), 2),
            "qwen": round(recorder.qwen_latencies.get(case_id, 0.0), 2),
            "total": round(total_latency_ms, 2),
        },
        "qwen_usage": {
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
            "total_tokens": usage.total_tokens if usage else None,
        },
        "failures": failures,
    }


def run_evaluation(dataset: dict[str, Any]) -> dict[str, Any]:
    token = uuid4().hex
    emails = {
        "user_a": f"m5-eval-owner-{token}@example.com",
        "user_b": f"m5-eval-intruder-{token}@example.com",
    }
    recorder = EvaluationRecorder()
    recorder.install()
    cleanup_result: dict[str, Any] | None = None
    results: list[dict[str, Any]] = []

    try:
        with TestClient(app) as client:
            registrations = {
                user_key: _register(client, email, f"M5 Evaluation {user_key}")
                for user_key, email in emails.items()
            }
            headers_by_user = {
                user_key: _headers(registration["access_token"])
                for user_key, registration in registrations.items()
            }

            courses_by_key: dict[str, dict[str, Any]] = {}
            documents_by_key: dict[str, dict[str, Any]] = {}
            document_key_by_id: dict[int, str] = {}
            document_course_by_id: dict[int, int] = {}

            for course_spec in dataset["courses"]:
                owner_key = course_spec["owner"]
                response = client.post(
                    "/api/v1/courses",
                    headers=headers_by_user[owner_key],
                    json={"name": f"{course_spec['name']} {token[:8]}"},
                )
                response.raise_for_status()
                course = response.json()
                courses_by_key[course_spec["key"]] = course

                for document_spec in course_spec["documents"]:
                    recorder.active_case = f"setup:{document_spec['key']}"
                    upload = client.post(
                        f"/api/v1/courses/{course['id']}/documents",
                        headers=headers_by_user[owner_key],
                        files={
                            "file": (
                                document_spec["filename"],
                                make_pdf(document_spec["pages"]),
                                "application/pdf",
                            )
                        },
                    )
                    if upload.status_code != 202:
                        raise AssertionError(
                            f"upload {document_spec['key']} failed: {upload.text}"
                        )
                    document = _wait_until_ready(
                        client,
                        headers_by_user[owner_key],
                        upload.json()["id"],
                    )
                    document["course_id"] = course["id"]
                    documents_by_key[document_spec["key"]] = document
                    document_key_by_id[document["id"]] = document_spec["key"]
                    document_course_by_id[document["id"]] = course["id"]

            owner_source = client.get(
                f"/api/v1/documents/{documents_by_key['lab_handbook']['id']}/file",
                headers=headers_by_user["user_a"],
            )
            if owner_source.status_code != 200:
                raise AssertionError("owner could not open evaluation source PDF")
            if owner_source.headers.get("content-type") != "application/pdf":
                raise AssertionError("source PDF returned the wrong content type")

            settings = get_settings()
            storage_paths: list[str] = []
            with SessionLocal() as db:
                for document_id in document_key_by_id:
                    document = db.get(Document, document_id)
                    if document is None or document.status != DocumentStatus.READY:
                        raise AssertionError("evaluation Document was not persisted READY")
                    storage_paths.append(str(resolve_storage_path(document.storage_path)))

            protected_values = [
                settings.dashscope_api_key or "",
                settings.dashscope_base_url or "",
                str(settings.document_storage_root.expanduser().resolve()),
                *storage_paths,
                "Context 是唯一允许使用的事实来源",
            ]

            regular_cases = [
                case for case in dataset["cases"] if "expected_status" not in case
            ]
            for case in regular_cases:
                recorder.active_case = case["id"]
                course_id = courses_by_key[case["course"]]["id"]
                started = perf_counter()
                response = client.post(
                    f"/api/v1/courses/{course_id}/knowledge/query",
                    headers=headers_by_user[case["user"]],
                    json={"question": case["question"]},
                )
                total_latency_ms = (perf_counter() - started) * 1000
                response_data = response.json() if response.content else None
                results.append(
                    _evaluate_case(
                        case=case,
                        response_status=response.status_code,
                        response_data=response_data,
                        total_latency_ms=total_latency_ms,
                        recorder=recorder,
                        documents_by_key=documents_by_key,
                        document_key_by_id=document_key_by_id,
                        document_course_by_id=document_course_by_id,
                        expected_course_id=course_id,
                        protected_values=protected_values,
                    )
                )

            security_case = next(
                case
                for case in dataset["cases"]
                if case["category"] == "cross_user_isolation"
            )
            owner_course_id = courses_by_key[security_case["course"]]["id"]
            owner_document_id = documents_by_key["lab_handbook"]["id"]
            calls_before = (
                recorder.embedding_api_requests,
                recorder.qwen_api_requests,
            )
            cross_user_responses = {
                "query": client.post(
                    f"/api/v1/courses/{owner_course_id}/knowledge/query",
                    headers=headers_by_user["user_b"],
                    json={"question": security_case["question"]},
                ).status_code,
                "list_documents": client.get(
                    f"/api/v1/courses/{owner_course_id}/documents",
                    headers=headers_by_user["user_b"],
                ).status_code,
                "open_pdf": client.get(
                    f"/api/v1/documents/{owner_document_id}/file",
                    headers=headers_by_user["user_b"],
                ).status_code,
            }
            calls_after = (
                recorder.embedding_api_requests,
                recorder.qwen_api_requests,
            )
            cross_user_pass = (
                all(status == 404 for status in cross_user_responses.values())
                and calls_before == calls_after
            )

            answerable_results = [
                result for result in results if result["expected_answerable"]
            ]
            unanswerable_results = [
                result for result in results if not result["expected_answerable"]
            ]
            injection_results = [
                result
                for result in results
                if result["category"] == "prompt_injection"
            ]
            emitted_citation_results = [
                result for result in answerable_results if result["citations"]
            ]

            prompt_tokens = [
                result["qwen_usage"]["prompt_tokens"] for result in results
            ]
            completion_tokens = [
                result["qwen_usage"]["completion_tokens"] for result in results
            ]
            total_tokens = [
                result["qwen_usage"]["total_tokens"] for result in results
            ]
            complete_usage = all(value is not None for value in total_tokens)

            summary = {
                "dataset_cases": len(dataset["cases"]),
                "executed_queries": len(results),
                "composition": dict(
                    sorted(Counter(case["category"] for case in dataset["cases"]).items())
                ),
                "retrieval_hit_at_5": _rate(
                    sum(result["retrieval_hit_at_5"] is True for result in answerable_results),
                    len(answerable_results),
                ),
                "answer_correctness": _rate(
                    sum(result["answer_correct"] is True for result in answerable_results),
                    len(answerable_results),
                ),
                "groundedness": _rate(
                    sum(result["grounded"] is True for result in answerable_results),
                    len(answerable_results),
                ),
                "answerable_citation_coverage": _rate(
                    sum(bool(result["citations"]) for result in answerable_results),
                    len(answerable_results),
                ),
                "emitted_citation_correctness": _rate(
                    sum(
                        result["citation_correct"] is True
                        for result in emitted_citation_results
                    ),
                    len(emitted_citation_results),
                ),
                "abstention_correctness": _rate(
                    sum(result["abstention_correct"] is True for result in unanswerable_results),
                    len(unanswerable_results),
                ),
                "prompt_injection": _rate(
                    sum(result["injection_pass"] is True for result in injection_results),
                    len(injection_results),
                ),
                "cross_course_isolation": _rate(
                    sum(result["course_isolated"] is True for result in results),
                    len(results),
                ),
                "cross_user_isolation": {
                    "passed": cross_user_pass,
                    "statuses": cross_user_responses,
                    "provider_calls_before": list(calls_before),
                    "provider_calls_after": list(calls_after),
                },
                "latency": {
                    "question_embedding": _latency_summary(
                        [result["latency_ms"]["question_embedding"] for result in results]
                    ),
                    "retrieval": _latency_summary(
                        [result["latency_ms"]["retrieval"] for result in results]
                    ),
                    "qwen": _latency_summary(
                        [result["latency_ms"]["qwen"] for result in results]
                    ),
                    "total": _latency_summary(
                        [result["latency_ms"]["total"] for result in results]
                    ),
                },
                "provider_requests": {
                    "embedding_total": recorder.embedding_api_requests,
                    "embedding_ingestion": recorder.ingestion_embedding_requests,
                    "embedding_questions": recorder.question_embedding_requests,
                    "qwen": recorder.qwen_api_requests,
                },
                "qwen_tokens": {
                    "usage_available_for_all_queries": complete_usage,
                    "prompt": sum(value for value in prompt_tokens if value is not None),
                    "completion": sum(
                        value for value in completion_tokens if value is not None
                    ),
                    "total": sum(value for value in total_tokens if value is not None),
                    "average_total_per_evaluated_query": round(
                        sum(value for value in total_tokens if value is not None)
                        / len(results),
                        2,
                    )
                    if complete_usage and results
                    else None,
                },
                "retrieval_failures": [
                    result["id"]
                    for result in answerable_results
                    if result["retrieval_hit_at_5"] is False
                ],
                "answer_failures": [
                    result["id"]
                    for result in answerable_results
                    if result["answer_correct"] is False
                ],
                "grounding_failures": [
                    result["id"]
                    for result in answerable_results
                    if result["grounded"] is False
                ],
                "answerable_citation_coverage_failures": [
                    result["id"]
                    for result in answerable_results
                    if not result["citations"]
                ],
                "emitted_citation_failures": [
                    result["id"]
                    for result in emitted_citation_results
                    if not result["citation_correct"]
                ],
                "abstention_failures": [
                    result["id"]
                    for result in unanswerable_results
                    if result["abstention_correct"] is False
                ],
            }

            evaluation = {
                "schema_version": 1,
                "generated_at": datetime.now(UTC).isoformat(),
                "dataset_version": dataset["version"],
                "summary": summary,
                "cases": results,
            }
    finally:
        recorder.restore()
        cleanup_result = _cleanup_test_data(list(emails.values()))

    evaluation["cleanup"] = cleanup_result
    if any(
        [
            cleanup_result["users"],
            cleanup_result["courses"],
            cleanup_result["documents"],
            cleanup_result["chunks"],
            cleanup_result["local_files"],
        ]
    ):
        raise AssertionError(f"evaluation cleanup was incomplete: {cleanup_result}")
    return evaluation


def main() -> None:
    args = _arguments()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    evaluation = run_evaluation(dataset)
    serialized = json.dumps(evaluation, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
