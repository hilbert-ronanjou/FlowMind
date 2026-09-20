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

### Sprint 1 milestones M1-M4

The following stages are completed and remain part of the current authorized implementation:

- M1: Qwen structured-output connectivity and schema-constrained extraction.
- M2: JWT-protected `POST /api/v1/ai/extract`.
- M3: `/ai-import` Draft Review, including user review and editing before any database write.
- M4: JWT-protected `POST /api/v1/ai/import`, explicit Course selection or creation, backend revalidation, a single transaction, and confirmed Course / Task persistence.

These stages do not authorize automatic import or any capability outside the Sprint 1 boundary.

## Current Sprint scope: Sprint 1 / M5 Final Acceptance & Baseline

M5 final acceptance is complete locally. It was limited to regression testing, real Qwen semantic regression, real PostgreSQL browser acceptance, error and security checks, documentation, and fixes to confirmed defects in the existing implementation. It added no business features and performed no UI redesign. Version `v0.2.0` is ready as the Sprint 1 baseline candidate; Git commit and tag creation must wait for final Product Owner approval.

The implemented Sprint 1 capability being accepted is limited to:

- AI Structured Extraction.
- `POST /api/v1/ai/extract` for authenticated extraction requests.
- `/ai-import` Draft Review with user editing.
- An explicit Confirm Import action.
- Backend revalidation of the confirmed draft.
- Importing the user-confirmed AI Draft into the existing `courses` and `tasks` data model.
- Requiring the user to choose an owned existing Course or explicitly create a new Course.
- Creating exactly one top-level Task per confirmed draft. Extracted steps are stored in the Task description; no subtask model is introduced.
- Performing all Course and Task writes for one confirmation in a single database transaction.

Sprint 1 does not authorize automatic writes from extraction results. `POST /api/v1/ai/extract` remains read-only with respect to business data, and the confirm-import endpoint must not call Qwen. Successful acceptance does not authorize a subsequent sprint.

## Features still prohibited in the current Sprint

The following remain prohibited during Sprint 1 / M5 even when they are mentioned elsewhere in the V1.0 product scope:

- RAG
- Document Upload
- PDF Parsing
- Embedding
- pgvector or any other vector database
- Agent or multi-agent systems
- Study Plan
- OSS
- SLS
- ECS or other cloud infrastructure work
- Redis
- Rate Limit
- Notifications
- New database tables
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

The only core business tables remain:

- `users`
- `courses`
- `tasks`

Sprint 1 confirmed import may write only to the existing `courses` and `tasks` tables. M5 must not add migrations, tables (including `ai_drafts`, `ai_runs`, `subtasks`, or `logs`), or a subtask entity. Alembic's revision metadata is not a business table. Any future schema change requires an explicitly authorized milestone and a documented current business reason. Passwords, secrets, and API keys must never be hard-coded.

## Change control

Ideas outside the V1.0 global scope or the current Sprint scope belong in `docs/BACKLOG.md`; they must not be implemented. Existing correct work from completed milestones must not be reverted or overwritten. Prefer the smallest implementation that meets an explicit acceptance criterion, following YAGNI.
