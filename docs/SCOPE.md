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

### Sprint 2 / M1: Knowledge Foundation

Sprint 2 / M1 is complete at commit `336c605`. It established the server-side embedding and pgvector foundation for the existing V1.0 Course material knowledge base / RAG module:

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

M1 exposed no new frontend or knowledge-query API. Provider credentials remain server-side environment variables.

### Sprint 2 / M2: Document Ingestion Pipeline

Sprint 2 / M2 is complete at commit `fa51b50`. It implemented the authenticated backend ingestion lifecycle for text-extractable PDF files:

`PDF -> validation -> controlled local storage -> PROCESSING -> page-aware text extraction and chunking -> batched 1024-dimensional embeddings -> document_chunks -> READY / FAILED`

The authorized M2 capability includes:

- `POST /api/v1/courses/{course_id}/documents`, returning `202 Accepted` after validation, controlled storage, and creation of a `PROCESSING` Document;
- a 20 MB measured upload limit and a maximum of 20 `READY + PROCESSING` Documents per Course;
- SHA-256 duplicate detection through the existing `UNIQUE(course_id, content_hash)` invariant;
- explicit same-filename/different-content conflict handling without document versioning;
- `pypdf` text extraction for readable, non-OCR PDFs, preserving 1-based page numbers;
- deterministic page-local, paragraph-aware chunks with centralized size and overlap configuration;
- batched reuse of M1 `text-embedding-v4` embeddings with exactly 1024 dimensions;
- transactionally replacing one Document's chunks and setting it to `READY`, or rolling back partial chunks and safely setting it to `FAILED`;
- authenticated list, metadata, delete, and failed-processing retry APIs;
- startup recovery that marks stale `PROCESSING` Documents as `FAILED` for explicit user retry;
- generated local object names independent of the user-provided filename, with storage paths excluded from API responses.

M2 added no database table and did not change the M1 ownership rule: Document ownership is derived only through `documents.course_id -> courses.user_id`. M2 did not modify the frontend.

## Current Sprint scope: Sprint 2 / M3 Grounded RAG Query

M3 is limited to authenticated, single-turn, Course-scoped grounded questions:

`Question -> one 1024-dimensional embedding -> Course/User/READY-filtered pgvector retrieval -> strict Qwen structured answer -> backend-generated citations`

The authorized M3 capability includes:

- JWT-protected `POST /api/v1/courses/{course_id}/knowledge/query`;
- trimmed, length-bounded questions and 404-style Course ownership checks before provider calls;
- cosine-distance retrieval of at most five chunks from `READY` Documents in the requested owned Course;
- one `text-embedding-v4` request per question;
- a grounded system prompt that treats the question and retrieved context as untrusted data and permits only the supplied context as a factual source;
- a strict structured Qwen result containing only `answerable`, `answer`, and `used_chunk_ids`;
- backend validation that every used chunk ID belongs to the actual Top-K candidates;
- deterministic backend construction of Document, filename, page, and chunk citations;
- an explicit no-answer response with no citations when the Course has no READY material, retrieval is empty, or Qwen declares the context insufficient;
- stable, safe errors for embedding, Qwen, and invalid structured/grounding results.

M3 adds no database table, conversation persistence, threshold, reranker, alternate retrieval strategy, or frontend change.

## Features still prohibited in the current Sprint

The following remain prohibited during Sprint 2 / M3 even when they are mentioned elsewhere in the V1.0 product scope:

- OCR or scanned-PDF recognition
- DOCX, PPTX, image, web-page, or other non-PDF ingestion
- Frontend knowledge or RAG UI
- Conversation persistence, chat memory, or multi-turn context
- Agent or multi-agent systems
- Study Plan
- OSS
- SLS
- ECS or other cloud infrastructure work
- Redis
- Celery or another background-job system
- RabbitMQ or another durable task queue
- Reranking, hybrid search, BM25, GraphRAG, HNSW, or IVFFlat
- Similarity thresholds or document versioning
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

The business tables authorized through Sprint 2 / M3 are exactly:

- `users`
- `courses`
- `tasks`
- `documents`
- `document_chunks`

The current business reason for `documents` is to record Course-owned PDF metadata, controlled storage identity, processing status, safe failure details, and duplicate-detection input. `Course.user_id` is the single authoritative ownership path; `documents` deliberately has no duplicated `user_id`. The current business reason for `document_chunks` is to store page-aware extracted text and 1024-dimensional embeddings after successful processing, and to provide the only factual context candidates for M3. No new M3 table is authorized. Alembic's revision metadata and the PostgreSQL `vector` extension are not business tables. Passwords, secrets, API keys, absolute storage paths, raw prompts, embeddings, and internal provider details must never be exposed through knowledge APIs.

## Change control

Ideas outside the V1.0 global scope or the current Sprint scope belong in `docs/BACKLOG.md`; they must not be implemented. Existing correct work from completed milestones must not be reverted or overwritten. Prefer the smallest implementation that meets an explicit acceptance criterion, following YAGNI.
