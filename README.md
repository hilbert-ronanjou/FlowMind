# FlowMind AI Cloud v0.2.0

FlowMind AI Cloud is a focused learning workspace for university students. It combines authentication, courses, tasks, a real-data dashboard, and user-confirmed AI extraction.

## Current stage

Sprint 0 and Sprint 1 milestones M1-M5 are implemented and accepted locally. Version **v0.2.0** is ready as the Sprint 1 baseline candidate; establishing its Git commit and tag still requires final Product Owner approval. No commit or tag is created by M5.

The V1.0 product boundary and current sprint restrictions remain defined in [`docs/SCOPE.md`](docs/SCOPE.md).

## Technology stack

- Frontend: Next.js 16.3.5, React, TypeScript, Tailwind CSS, shadcn-style UI components
- Backend: Python, FastAPI, SQLAlchemy, Alembic, Pydantic
- Database: PostgreSQL
- Authentication: hashed passwords and JWT bearer tokens
- AI extraction: Alibaba Cloud Model Studio / Qwen structured output, validated with Pydantic
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

- Node.js 20.9 or newer and pnpm 9 or newer
- Python 3.11 or newer
- Docker Desktop with Docker Compose, or an existing local PostgreSQL service
- Model Studio credentials and a Qwen model supporting the configured structured-output API

## Local setup

Copy the environment template at the repository root:

```powershell
Copy-Item .env.example .env
```

Replace `POSTGRES_PASSWORD` and the matching password in `DATABASE_URL`, and set `JWT_SECRET` to a random secret of at least 32 characters. Do not commit `.env` or actual credentials.

For real AI extraction, configure these server-side variables in the root `.env`:

- `DASHSCOPE_API_KEY`: your Model Studio API key.
- `DASHSCOPE_BASE_URL`: the OpenAI-compatible API base URL for your Model Studio region.
- `QWEN_MODEL`: the Qwen model available to that account and region.

Keep the API key on the backend; never use a `NEXT_PUBLIC_` variable for it. The backend and the semantic regression script load the root `.env` when run as shown below.

### 1. Start PostgreSQL

```powershell
docker compose up -d postgres
```

The Compose database listens on `localhost:5432` with credentials supplied through `.env` and persists data in a named volume. If using an existing PostgreSQL service, set `DATABASE_URL` to that instance instead. Runtime and acceptance testing require PostgreSQL.

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
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000/api/v1"
pnpm dev
```

Open `http://localhost:3000`. The browser origin must match the backend's `FRONTEND_URL`; `localhost` and `127.0.0.1` are different origins. Set `NEXT_PUBLIC_API_URL` in the frontend process environment (or its local environment file) before development or production build if the API address differs. Next.js does not automatically read the repository-root `.env` from the `frontend` directory.

## Implemented features

- Authentication: registration, login, current-user lookup, logout, password hashing, and JWT authentication.
- Courses: per-user create, list, detail, edit, and delete.
- Tasks: per-user create, list, detail, edit, status changes, delete, and optional course assignment.
- Dashboard: today's tasks, upcoming deadlines, course count, completed count, and recent tasks from the database.
- AI Structured Extraction: JWT-protected `POST /api/v1/ai/extract`, Qwen structured output, schema validation, and safe provider error responses.
- Draft Review: `/ai-import` with editable extraction results, loading, retry, and error states.
- Confirmed AI Import: JWT-protected `POST /api/v1/ai/import`, explicit course selection or creation, backend revalidation, and transactional persistence.
- Existing landing, login, register, dashboard, courses, course detail, tasks, settings, and AI Import pages, with responsive navigation and form feedback.
- Alembic migration for the three core business tables: `users`, `courses`, and `tasks`.

## AI workflow

```text
User Text -> Qwen -> Structured Output -> Draft -> Human Confirmation
          -> Backend Validation -> Transaction -> Course / Task
```

AI does not directly write to the database. Extraction returns an editable draft without creating Courses or Tasks. On confirmation, the user explicitly selects an owned Course or chooses to create one; an inferred course name alone never creates a Course.

The backend validates the confirmed draft and Course ownership again. It creates exactly one Task with source `AI`; extracted steps are stored in its description and weak suggestions remain optional description content. All Course and Task writes for that confirmation share one transaction. The import endpoint does not call Qwen.

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

Run the existing real Qwen semantic regression separately from the backend directory:

```powershell
python scripts/test_qwen_structured.py
```

It calls the configured Qwen service for six unchanged semantic cases: explicit course notice, missing date, explicit relative date, vague "next week", non-task conversation, and prompt injection. It reports structured output and semantic failures and requires working provider credentials and network access.

SQLite unit tests do not prove PostgreSQL persistence. M5 acceptance additionally runs the real browser, frontend, backend, Qwen service, and PostgreSQL through registration, manual Course/Task creation, extraction, draft editing, confirmation, refresh, and service restart. Verify `alembic current` against the existing migration, confirm the three core business tables, and remove only the dedicated acceptance-test users and their data afterward.

## Not implemented

- RAG, document upload, PDF parsing, embeddings, or vector databases
- AI study plans
- Agents, subtasks, rate limiting, notifications, or multi-model routing
- OSS, SLS, ECS, Redis, or production deployment
- New database tables, new business modules, or a UI redesign
- Any business module excluded by `docs/SCOPE.md`

Deferred ideas belong in [`docs/BACKLOG.md`](docs/BACKLOG.md) and require a future scoped sprint.
