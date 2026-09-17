# FlowMind AI Cloud

FlowMind AI Cloud is a focused learning workspace for university students. It brings authentication, courses, tasks, and a real-data dashboard into one clean web application.

## Current stage

This repository contains **Sprint 0: Foundation** only. The scope is frozen in [`docs/SCOPE.md`](docs/SCOPE.md). AI content parsing, RAG, document upload, AI study planning, and cloud integrations are intentionally not implemented.

## Technology stack

- Frontend: Next.js, React, TypeScript, Tailwind CSS, shadcn-style UI components
- Backend: Python, FastAPI, SQLAlchemy, Alembic, Pydantic
- Database: PostgreSQL
- Authentication: hashed passwords and JWT bearer tokens
- Local infrastructure: Docker Compose for PostgreSQL only

## Project structure

```text
FlowMind/
├── frontend/              # Next.js application
├── backend/               # FastAPI application, migrations, and tests
│   ├── alembic/
│   ├── app/
│   └── tests/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── BACKLOG.md
│   └── SCOPE.md
├── .env.example
├── AGENTS.md
├── docker-compose.yml
└── README.md
```

## Requirements

- Node.js 20 or newer and pnpm 9 or newer
- Python 3.11 or newer
- Docker Desktop with Docker Compose

## Local setup

Copy the environment template at the repository root:

```powershell
Copy-Item .env.example .env
```

Replace `JWT_SECRET` with a long random development secret. Do not commit `.env`.

### 1. Start PostgreSQL

```powershell
docker compose up -d postgres
```

The local database listens on `localhost:5432` with the development credentials supplied through `.env`.

### 2. Start the backend

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

The API runs at `http://localhost:8000`, its OpenAPI UI is at `http://localhost:8000/docs`, and the health check is `GET /health`.

### 3. Start the frontend

In a second terminal:

```powershell
Set-Location frontend
pnpm install
pnpm dev
```

Open `http://localhost:3000`.

## Tests and verification

Backend:

```powershell
Set-Location backend
pytest
```

Frontend:

```powershell
Set-Location frontend
pnpm lint
pnpm typecheck
pnpm build
```

Backend tests use an isolated in-memory SQLite database for speed; application runtime and Alembic target PostgreSQL.

## Implemented in Sprint 0

- Register, login, current-user lookup, logout, password hashing, and JWT authentication
- Per-user data isolation for every Course and Task endpoint
- Course create, list, detail, edit, and delete
- Task create, list, detail, edit, status update, and delete
- Optional course assignment for tasks
- Dashboard with today's tasks, upcoming deadlines, course count, completed count, and recent tasks
- Landing, login, register, dashboard, courses, course detail, tasks, and settings pages
- Responsive navigation, form validation, loading, empty, error, and toast states
- Initial Alembic migration for the three core tables

## Not implemented

- AI import or LLM integration
- RAG, embeddings, vector databases, or document upload
- AI study plans
- Cloud-service integrations or production deployment
- Any business module excluded by `docs/SCOPE.md`

Deferred ideas belong in [`docs/BACKLOG.md`](docs/BACKLOG.md) and require a future scoped sprint.
