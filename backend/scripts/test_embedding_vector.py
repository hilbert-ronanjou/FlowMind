"""Explicit real-provider and real-PostgreSQL Sprint 2/M1 regression."""
import hashlib
import json
import math
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.course import Course
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.user import User
from app.services.embedding import embed_texts
from app.services.knowledge.retrieval import search_ready_chunks


A = "事务具有原子性、一致性、隔离性和持久性。"
B = "数据库事务的 ACID 包括哪些性质？"
C = "操作系统使用 fork 创建子进程。"

CHUNKS = [
    "数据库事务包含原子性、一致性、隔离性和持久性，也就是 ACID。",
    "在类 Unix 操作系统中，可以调用 fork 创建子进程。",
    "B+ tree 常用于数据库索引，以支持范围查询。",
]
QUESTIONS = ["ACID 有哪些特性？", "如何创建子进程？"]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm)


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        raise RuntimeError("This explicit regression requires PostgreSQL")

    texts = [A, B, C, *CHUNKS, *QUESTIONS]
    vectors = embed_texts(texts)
    by_text = dict(zip(texts, vectors, strict=True))
    similarity_ab = cosine_similarity(by_text[A], by_text[B])
    similarity_ac = cosine_similarity(by_text[A], by_text[C])
    if similarity_ab <= similarity_ac:
        raise AssertionError("The database transaction texts did not rank as expected")

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    db = Session(engine, expire_on_commit=False)
    token = uuid4().hex
    try:
        user = User(
            email=f"m1-vector-{token}@example.invalid",
            username="M1 Vector Regression",
            password_hash="regression-only-not-a-real-password-hash",
        )
        course = Course(user=user, name=f"M1 Vector Regression {token}")
        document = Document(
            course=course,
            filename="knowledge-regression.txt",
            storage_path=f"m1-regression/{token}/fixture.txt",
            content_hash=hashlib.sha256("\n".join(CHUNKS).encode()).hexdigest(),
            file_size=len("\n".join(CHUNKS).encode()),
            status=DocumentStatus.READY,
        )
        document.chunks = [
            DocumentChunk(
                chunk_index=index,
                page_number=None,
                content=text,
                embedding=by_text[text],
            )
            for index, text in enumerate(CHUNKS)
        ]
        db.add(user)
        db.flush()

        rankings: dict[str, list[dict[str, float | str]]] = {}
        for question in QUESTIONS:
            matches = search_ready_chunks(
                db,
                user_id=user.id,
                course_id=course.id,
                query_embedding=by_text[question],
                limit=3,
            )
            rankings[question] = [
                {"content": chunk.content, "cosine_distance": round(distance, 6)}
                for chunk, distance in matches
            ]

        if not rankings[QUESTIONS[0]][0]["content"].startswith("数据库事务"):
            raise AssertionError("ACID retrieval did not rank the transaction chunk first")
        if "fork" not in str(rankings[QUESTIONS[1]][0]["content"]):
            raise AssertionError("Process retrieval did not rank the fork chunk first")

        print(
            json.dumps(
                {
                    "model": settings.embedding_model,
                    "dimensions": sorted({len(vector) for vector in vectors}),
                    "provider_api_calls": 1,
                    "semantic_similarity": {
                        "A_B": round(similarity_ab, 6),
                        "A_C": round(similarity_ac, 6),
                        "ordering_passed": similarity_ab > similarity_ac,
                    },
                    "retrieval": rankings,
                    "cleanup": "transaction rolled back",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        db.rollback()
        db.close()
        engine.dispose()


if __name__ == "__main__":
    main()
