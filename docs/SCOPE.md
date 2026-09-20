# FlowMind AI Cloud V1.0 Scope

## Product positioning

FlowMind AI Cloud is an AI-assisted learning workspace for university students. This document freezes the V1.0 business scope and distinguishes the global product boundary from the work authorized for the current sprint. Every implementation change must be checked against both boundaries before coding.

## V1.0 global scope

V1.0 contains exactly seven major business modules:

1. User system
2. Dashboard
3. Course management
4. Task management
5. AI content parsing
6. Course material knowledge base / RAG
7. AI study planning

No eighth major business module may be added. The presence of a module in the V1.0 scope does not authorize its implementation in the current sprint.

## Completed stages

### Sprint 0: Foundation

Sprint 0 completed the foundation for:

- Authentication: registration, login, current-user lookup, logout, password hashing, and JWT authentication.
- Course management: create, list, view, edit, and delete the signed-in user's courses.
- Task management: create, list, view, edit, delete, and change status for the signed-in user's tasks. Tasks may optionally belong to one of that user's courses.
- Dashboard: today's tasks, upcoming deadlines, course count, completed-task count, and recent tasks, all from the real database.
- The specified frontend routes, including loading, empty, error, toast, validation, and responsive states.
- PostgreSQL persistence and user-owned data isolation.

The former Sprint 0 prohibition on AI implementation was a temporary stage boundary. It remains historically applicable to Sprint 0, but it does not prohibit the explicitly authorized Sprint 1 work below.

### Sprint 1: AI Structured Extraction and Confirmed Import

Sprint 1 milestones M1-M5 are complete and frozen at the `v0.2.0-ai-import` baseline:

- M1: Qwen structured-output connectivity and schema-constrained extraction.
- M2: JWT-protected `POST /api/v1/ai/extract`.
- M3: `/ai-import` Draft Review, including user review and editing before any database write.
- M4: JWT-protected `POST /api/v1/ai/import`, explicit Course selection or creation, backend revalidation, a single transaction, and confirmed Course / Task persistence.
- M5: real PostgreSQL and browser acceptance, real Qwen semantic regression, security review, documentation, and the v0.2.0 baseline.

These completed stages do not authorize automatic import or any capability outside their documented boundary.

## Current Sprint scope: Sprint 2 / M1 Knowledge Foundation

Sprint 2 / M1 establishes only the server-side foundation for the existing V1.0 Course material knowledge base / RAG module. The authorized proof is:

`Text -> Alibaba Cloud text-embedding-v4 -> 1024-dimensional vector -> PostgreSQL pgvector -> semantic similarity retrieval`

M1 is limited to:

- enabling the PostgreSQL `vector` extension;
- adding exactly two business tables: `documents` and `document_chunks`;
- associating each Document with exactly one Course, with User ownership derived exclusively from that Course;
- storing `Vector(1024)` embeddings for Document Chunks;
- a server-side embedding service using Alibaba Cloud Model Studio's OpenAI-compatible API, Beijing-region `text-embedding-v4`, and exactly 1024 dimensions;
- deterministic unit/integration tests plus an explicit real-provider embedding and real-PostgreSQL retrieval regression;
- retrieval foundations that join through Course and always filter by `Course.user_id`, `Document.course_id`, and `DocumentStatus.READY` before similarity ordering;
- a database-level `UNIQUE(course_id, content_hash)` invariant that rejects identical material within one Course while allowing the same content in different Courses.

M1 exposes no new frontend or knowledge-query API. It does not upload, parse, or chunk files. `filename` is metadata only and must never be treated as a storage path. Provider credentials remain server-side environment variables.

## Features still prohibited in the current Sprint

The following remain prohibited during Sprint 2 / M1 even when they are mentioned elsewhere in the V1.0 product scope:

- PDF upload or any other document-upload API/UI
- PDF parsing, OCR, DOCX, or PPTX processing
- a chunking pipeline
- Knowledge Query API
- Qwen RAG answers, citations, or frontend RAG UI
- Agent or multi-agent systems
- Study Plan
- OSS
- SLS
- ECS or other cloud infrastructure work
- Redis
- Celery or another background-job system
- Reranking, hybrid search, BM25, GraphRAG, HNSW, or IVFFlat
- Chat memory or document versioning
- Rate Limit
- Notifications
- Any database table other than `documents` and `document_chunks`
- A subtask model
- Multi-model routing
- An eighth business module
- Any new business module or major UI redesign
- Web search or automatic crawling
- Microservices, Kubernetes, Kafka, RocketMQ, or service mesh
- Complex DDD, CQRS, event sourcing, or speculative abstraction layers
- Complex permissions, multi-tenancy, roles, permissions, audit logs, sessions, teams, or organizations
- WebSockets

## Explicitly excluded from V1.0

- Social features, friends, chat communities, or multiplayer collaboration
- Rankings, points, or check-ins
- Teacher or school administration portals
- Grade or attendance management
- Payments or marketplace features
- Message centers or complex notification systems
- Two-way calendar synchronization
- Unlimited subtasks, Gantt charts, or project management
- AI image generation, AI voice, or AI essay writing
- Web search, automatic crawlers, or multi-agent systems

## Data constraints

The business tables authorized through Sprint 2 / M1 are exactly:

- `users`
- `courses`
- `tasks`
- `documents`
- `document_chunks`

The current business reason for `documents` is to record Course-owned material metadata, processing state, and future duplicate-detection input without implementing upload. `Course.user_id` is the single authoritative ownership path; `documents` deliberately has no duplicated `user_id`. The current business reason for `document_chunks` is to prove 1024-dimensional embedding persistence and isolated semantic retrieval. No other schema is authorized. Alembic's revision metadata and the PostgreSQL `vector` extension are not business tables. Passwords, secrets, and API keys must never be hard-coded.

## Change control

Ideas outside the V1.0 global scope or the current Sprint scope belong in `docs/BACKLOG.md`; they must not be implemented. Existing correct work from completed milestones must not be reverted or overwritten. Prefer the smallest implementation that meets an explicit acceptance criterion, following YAGNI.
