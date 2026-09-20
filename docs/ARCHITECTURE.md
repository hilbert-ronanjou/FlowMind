# Architecture

## Current architecture: Sprint 2 / M2 Document Ingestion Pipeline

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

The internal retrieval foundation requires both `user_id` and `course_id`, joins each Chunk to its Document and Course, filters on `Course.user_id`, `Document.course_id`, and `DocumentStatus.READY`, and only then orders by vector distance. It does not provide a global-search path. There is no Knowledge Query API, RAG answer generation, citation flow, file upload, parser, or chunking pipeline in M1.

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

M2 exposes authenticated upload, list, metadata, delete, and retry endpoints. Ownership is always checked through Course. It still exposes no knowledge-question API, grounded Qwen answer, citation generation, or frontend knowledge UI.

## Runtime and verification

Docker Compose defines a local PostgreSQL 16 service with a persistent named volume. An existing local PostgreSQL instance can also be selected through `DATABASE_URL`. The frontend origin is configured by `FRONTEND_URL`, and its API address by `NEXT_PUBLIC_API_URL`.

Backend unit tests use an isolated in-memory SQLite database. A separately enabled PostgreSQL integration test validates pgvector ordering and ownership/Course/status filters without a provider call. The explicit M1 regression invokes the actual embedding provider once in a batch, validates semantic ordering, stores dedicated rows in a real PostgreSQL transaction, performs two filtered pgvector searches, and rolls the transaction back. Ordinary pytest does not call Model Studio.

## Repository layout

- `frontend/`: Next.js, React, TypeScript, Tailwind CSS, and local shadcn-style UI components
- `backend/`: FastAPI, SQLAlchemy, Alembic, Pydantic, and pytest
- `docs/`: frozen scope, architecture, and deferred backlog

## Future / Planned

Later explicitly scoped milestones may add knowledge-query APIs, RAG answers, citations, and frontend knowledge UI. None of those capabilities is implemented or authorized by M2. OCR, non-PDF formats, OSS, SLS, ECS, Redis, durable queues, approximate vector indexes, and AI study planning also remain future-only. Inclusion here does not authorize implementation.
