# Architecture

## Current architecture: v0.2.0 Sprint 1 baseline candidate

```text
Browser
   |
   v
Next.js Frontend
   |
   v
FastAPI REST API
   |-- PostgreSQL
   `-- Alibaba Cloud Model Studio / Qwen
```

The browser loads the Next.js frontend, whose client-side API layer calls FastAPI's versioned `/api/v1` REST API. FastAPI owns authentication, authorization, validation, persistence, and the server-side Qwen integration. PostgreSQL stores application data. SQLAlchemy models and Alembic manage the three core business tables: `users`, `courses`, and `tasks`; Alembic maintains its own revision metadata table.

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

## Runtime and verification

Docker Compose defines a local PostgreSQL 16 service with a persistent named volume. An existing local PostgreSQL instance can also be selected through `DATABASE_URL`. The frontend origin is configured by `FRONTEND_URL`, and its API address by `NEXT_PUBLIC_API_URL`.

Backend unit tests use an isolated in-memory SQLite database. Real PostgreSQL browser acceptance and restart-persistence checks are separate from those tests. The six-case Qwen semantic regression script invokes the actual configured provider; deterministic API tests cover error handling and import validation without depending on provider availability.

## Repository layout

- `frontend/`: Next.js, React, TypeScript, Tailwind CSS, and local shadcn-style UI components
- `backend/`: FastAPI, SQLAlchemy, Alembic, Pydantic, and pytest
- `docs/`: frozen scope, architecture, and deferred backlog

## Future / Planned

The V1.0 global product scope includes a course-material knowledge base / RAG and AI study planning, but those modules are not implemented or authorized by Sprint 1. Document upload, embeddings, vector storage, OSS, SLS, and ECS are not current components. Any future design or infrastructure work requires its own explicitly scoped milestone; inclusion here does not authorize implementation.
