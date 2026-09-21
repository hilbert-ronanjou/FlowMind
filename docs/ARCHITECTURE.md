# Architecture

## Current architecture: Sprint 2 / M5 RAG Evaluation and Final Acceptance

```text
Browser
   |
   v
Next.js Frontend
   |
   v
FastAPI REST API
   |-- PostgreSQL 16 + pgvector
   |-- Alibaba Cloud Model Studio / Qwen structured extraction
   `-- Alibaba Cloud Model Studio / text-embedding-v4
```

The browser loads the Next.js frontend, whose client-side API layer calls FastAPI's versioned `/api/v1` REST API. FastAPI owns authentication, authorization, validation, persistence, and the server-side Model Studio integrations. PostgreSQL stores application data. SQLAlchemy models and Alembic manage the five business tables: `users`, `courses`, `tasks`, `documents`, and `document_chunks`; Alembic maintains its own revision metadata table.

JWT bearer tokens authenticate API requests. Every course and task query is constrained by the authenticated user ID.

## Extraction and confirmed import

```text
User Text
   -> POST /api/v1/ai/extract (JWT)
   -> Qwen structured output
   -> Pydantic validation
   -> Editable Draft Review at /ai-import
   -> Human Confirmation and explicit Course choice
   -> POST /api/v1/ai/import (JWT)
   -> Draft revalidation and Course ownership check
   -> One database transaction
   -> Course / Task
```

Extraction is read-only with respect to business data. Drafts remain in the frontend's page state until the user confirms; there is no `ai_drafts`, `ai_runs`, or subtask table. The frontend supports editing, loading, retry, and error feedback. An import failure preserves the current draft and source text for correction or retry.

Confirmation requires an owned existing Course or explicit new-Course creation. The backend validates the submitted data, writes exactly one Task with source `AI`, and stores extracted steps and optional suggestions in its description. Course creation and Task insertion share one SQLAlchemy Session and one commit, with rollback on failure. The import endpoint does not call Qwen, and AI never directly writes database records.

Qwen credentials, region-specific API base URL, and model selection are backend environment variables. Provider errors are mapped to safe API responses without exposing provider credentials. The browser receives only the structured result or the application's error response.

## Course knowledge foundation

```text
Text
   -> server-side Embedding service
   -> Alibaba Cloud text-embedding-v4 (1024 dimensions)
   -> DocumentChunk.embedding Vector(1024)
   -> PostgreSQL pgvector cosine-distance ordering
```

`Document` stores metadata and processing status for material belonging to exactly one Course. Course is the sole Document ownership source: User ownership is derived through `documents.course_id -> courses.user_id`, so `documents` does not duplicate `user_id`. The schema rejects duplicate `(course_id, content_hash)` values, prevents duplicate `(document_id, chunk_index)` values, and stores strict 1024-dimensional vectors. Course deletion cascades to Documents, User deletion cascades through Courses, and Document deletion cascades to Chunks.

The internal retrieval foundation requires both `user_id` and `course_id`, joins each Chunk to its Document and Course, filters on `Course.user_id`, `Document.course_id`, and `DocumentStatus.READY`, and only then orders by vector distance. It does not provide a global-search path.

The embedding integration is server-side only. It reads the existing Model Studio API key and Beijing OpenAI-compatible base URL from environment configuration, uses `text-embedding-v4`, requests 1024 dimensions, validates every returned vector, applies a timeout, and maps provider failures to safe application errors.

## Document ingestion

```text
Authenticated PDF upload
   -> measured 20 MB validation and SHA-256
   -> generated local storage key
   -> Document(PROCESSING)
   -> in-process BackgroundTask with a new DB Session
   -> pypdf page extraction
   -> page-local paragraph-aware chunks
   -> batched text-embedding-v4 calls
   -> one chunk-replacement / READY transaction
```

M2 accepts only text-extractable PDFs. User filenames are retained as metadata but never control disk paths; generated PDF object names are resolved beneath the configured storage root, and `storage_path` is excluded from API responses. The API enforces a measured 20 MB limit, a 20-active-document Course quota, content duplication rules, and explicit same-name conflicts.

Processing never reuses the request SQLAlchemy Session. Parser, embedding, or persistence failures remove usable chunks and set a safe `FAILED` reason. Failed Documents can be retried from the stored PDF; startup recovery marks stale `PROCESSING` rows as interrupted rather than attempting automatic work. Processing Documents cannot be deleted, avoiding an in-process task/file race. Deleting a completed or failed Document removes its chunks through the existing cascade and deletes the controlled local PDF.

M2 exposes authenticated upload, list, metadata, delete, and retry endpoints. Ownership is always checked through Course.

## Grounded knowledge query

```text
Authenticated question
   -> Course ownership check
   -> READY Document existence check
   -> one text-embedding-v4 request (1024 dimensions)
   -> Course + User + READY scoped pgvector cosine-distance search (Top 5)
   -> Qwen strict GroundedAnswer structured output
   -> candidate chunk ID validation
   -> backend-only citation metadata construction
   -> stable answer / no-answer response
```

`POST /api/v1/courses/{course_id}/knowledge/query` is JWT protected. The request question is trimmed, must be non-empty, and is limited to 2,000 characters. A Course that does not exist or is not owned by the authenticated user has the same `404` behavior. The retrieval query independently enforces the user, Course, and `READY` Document boundaries before ordering candidates by pgvector cosine distance. `KNOWLEDGE_TOP_K`, currently 5, is centrally configured.

If there is no READY Document or retrieval returns no chunk, the endpoint returns the fixed domain no-answer result without calling Qwen; the no-READY path also avoids the embedding request. Otherwise Qwen receives only the untrusted question and the retrieved chunk IDs/content. The system instruction treats context as the only factual source, rejects prompt-injection instructions, forbids unsupported facts and chain-of-thought, and requires refusal when context is insufficient.

Qwen's strict structured result contains only `answerable`, `answer`, and `used_chunk_ids`; extra properties are rejected. The backend verifies every used ID against the actual Top-5 candidate set, removes duplicate IDs, and deterministically resolves `document_id`, `filename`, `page_number`, and `chunk_id` from database-backed retrieval results. The model cannot supply or override citation metadata. A non-answer is normalized to the fixed public message and empty citations even if a provider result includes text or chunk IDs.

Provider configuration, timeout, rate-limit, authentication, invalid embedding, invalid structured output, and grounding failures are mapped to stable responses without exposing provider URLs, credentials, prompts, document content, or SDK details. Diagnostics contain only Course ID, retrieval count/chunk IDs, latency, and token usage when supplied by the provider.

## Course knowledge interface and secure source access

```text
Course Detail
   |-- Course Documents
   |     |-- upload -> PROCESSING -> two-second polling -> READY / FAILED
   |     |-- FAILED -> retry
   |     `-- READY / FAILED -> confirmed delete
   `-- Course Knowledge Questions
         |-- one independent question -> grounded query API
         |-- answer / normal no-answer result
         `-- backend citation document_id -> authenticated PDF file endpoint
```

The existing Course Detail page owns the M4 interface; there is no global Knowledge Base route. Document polling exists only while the current Course contains a `PROCESSING` Document, uses one timer, and is cancelled with the component. Upload responses are inserted immediately as `PROCESSING`; retry updates the same Document; delete removes it from the current list after explicit confirmation. Temporary question history lives only in React state and intentionally disappears on refresh. It is not sent as multi-turn context.

`GET /api/v1/documents/{document_id}/file` authenticates the caller, joins Document ownership through Course, resolves only the generated single-component storage key beneath the configured storage root, and returns `application/pdf` with a sanitized inline filename. Missing files, unsafe stored paths, and cross-user access produce stable non-disclosing `404` responses. The frontend fetches the PDF with JWT and creates a temporary browser object URL; it never receives or constructs a filesystem path. Citation links use only backend-generated `citation.document_id` and never parse metadata from answer text.

## Runtime and verification

Docker Compose defines a local PostgreSQL 16 service with a persistent named volume. An existing local PostgreSQL instance can also be selected through `DATABASE_URL`. The frontend origin is configured by `FRONTEND_URL`, and its API address by `NEXT_PUBLIC_API_URL`.

Backend unit tests use an isolated in-memory SQLite database. A separately enabled PostgreSQL integration test validates pgvector ordering, citation metadata inputs, and ownership/Course/status filters without a provider call. File endpoint tests cover owner access, cross-user denial, missing files, path safety, PDF headers, and storage-path non-disclosure. The explicit browser acceptance uses real PostgreSQL, embedding, and Qwen through the Course UI and removes its dedicated rows and local file. Ordinary pytest does not call Model Studio.

## RAG evaluation and release acceptance

```text
Fixed synthetic dataset
   -> authenticated API setup
   -> real PDF ingestion and READY persistence
   -> fixed Course-scoped questions
   -> Top-5 retrieval recording
   -> real grounded Qwen response
   -> deterministic evidence/fact/Citation checks
   -> latency and provider-usage aggregation
   -> dedicated DB and local-file cleanup
```

`backend/scripts/evaluate_rag.py` is an explicitly invoked release tool, not a business API and not part of ordinary pytest. It creates isolated test Users and Courses, uploads the project-authored PDFs represented by `backend/evaluation/rag_baseline.json`, and exercises the same API and service path used by the browser. It records actual Top-5 candidate metadata, provider-selected chunk IDs, backend Citations, reliably measurable stage latency, embedding/Qwen request counts, and provider-reported Qwen usage. It never estimates embedding tokens.

Expected facts and evidence pages are authored in the dataset rather than generated by Qwen. Answer checks allow declared equivalent factual forms without requiring an exact sentence. Grounding passes only when expected evidence is present in the provider-selected chunk IDs, and Citation checks deterministically compare API Citations with those real selected candidates. Unsupported and injection questions must return the fixed no-answer result with no Citations.

Course isolation is checked for every recorded retrieval, while the cross-User case verifies query, document-list, and source-PDF `404` behavior and confirms that rejection makes no embedding or Qwen call. Cleanup is scoped to unique evaluation identities and verifies zero remaining Users, Courses, Documents, Chunks, and local PDFs.

## Repository layout

- `frontend/`: Next.js, React, TypeScript, Tailwind CSS, and local shadcn-style UI components
- `backend/`: FastAPI, SQLAlchemy, Alembic, Pydantic, and pytest
- `docs/`: frozen scope, architecture, and deferred backlog

## Future / Planned

Chat persistence, multi-turn context, reranking, similarity thresholds, BM25/hybrid search, OCR, non-PDF formats, PDF annotation/viewer systems, OSS, SLS, ECS, Redis, Celery, agents, MCP, approximate vector indexes, and AI study planning remain future-only. Inclusion here does not authorize implementation.
