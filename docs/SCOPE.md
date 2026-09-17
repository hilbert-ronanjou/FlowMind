# FlowMind AI Cloud V1.0 Scope

## Product positioning

FlowMind AI Cloud is an AI-assisted learning workspace for university students. This document freezes the V1.0 business scope. Every implementation change must be checked against it before coding.

## V1.0 business modules

V1.0 contains exactly seven major business modules:

1. User system
2. Dashboard
3. Course management
4. Task management
5. AI content parsing
6. Course material knowledge base / RAG
7. AI study planning

No eighth major business module may be added. The presence of a module in the V1.0 scope does not authorize its implementation in the current sprint.

## Sprint 0: Foundation

Sprint 0 implements only:

- Authentication: registration, login, current-user lookup, logout, password hashing, and JWT authentication.
- Course management: create, list, view, edit, and delete the signed-in user's courses.
- Task management: create, list, view, edit, delete, and change status for the signed-in user's tasks. Tasks may optionally belong to one of that user's courses.
- Dashboard: today's tasks, upcoming deadlines, course count, completed-task count, and recent tasks, all from the real database.
- The specified frontend routes, including loading, empty, error, toast, validation, and responsive states.
- A local PostgreSQL service through Docker Compose.

All user-owned records must be isolated by authenticated user. Sprint 0 tasks are created only with the `MANUAL` source; `AI` is an enum value reserved for a later sprint.

## Explicitly out of scope for Sprint 0

- AI import, LLM integration, AI content parsing implementation
- RAG, embeddings, vector databases, document upload
- AI study planning implementation
- AI agents or multi-agent systems
- Web search or automatic crawling
- Object storage, logging services, cloud services, or Alibaba Cloud integration
- Microservices, Kubernetes, Kafka, RocketMQ, Redis, service mesh
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

## Data constraints for Sprint 0

The only core business tables are:

- `users`
- `courses`
- `tasks`

Adding any table or changing a table requires a documented, current business reason. Passwords, secrets, and API keys must never be hard-coded.

## Change control

Ideas outside this frozen scope belong in `docs/BACKLOG.md`; they must not be implemented. Prefer the smallest implementation that meets an explicit acceptance criterion, following YAGNI.
