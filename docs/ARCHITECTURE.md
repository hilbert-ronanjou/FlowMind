# Architecture

## Current Sprint 0 architecture

```text
Browser
   |
   v
Next.js Frontend
   |
   v
FastAPI REST API
   |
   v
PostgreSQL
```

The frontend calls the versioned `/api/v1` REST API. FastAPI owns authentication, authorization, validation, and persistence. SQLAlchemy models and Alembic migrations manage the three core tables: users, courses, and tasks.

JWT bearer tokens authenticate API requests. Every course and task query is constrained by the authenticated user ID.

## Repository layout

- `frontend/`: Next.js, React, TypeScript, Tailwind CSS, and local shadcn-style UI components
- `backend/`: FastAPI, SQLAlchemy, Alembic, Pydantic, and pytest
- `docs/`: frozen scope, architecture, and deferred backlog

## Future architecture

Later V1.0 sprints may introduce AI content parsing, a course-material knowledge base / RAG, and AI study planning. Their providers, storage, and boundaries will be designed only when those sprints are scoped. No implementation or infrastructure for them exists in Sprint 0.
