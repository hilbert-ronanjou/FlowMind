# FlowMind AI Cloud

FlowMind AI Cloud is a focused learning workspace for university students. It combines authentication, courses, tasks, a real-data dashboard, user-confirmed AI extraction, and Course-scoped grounded knowledge from uploaded PDFs.

## Current stage

Sprint 0, Sprint 1, and Sprint 2 milestones M1-M4 are complete. The current authorized stage is **Sprint 2 / M5 RAG Evaluation and Final Acceptance**. M5 adds a repeatable real-provider evaluation baseline and release regressions without adding product features, retrieval algorithms, or persistent chat.

The V1.0 product boundary and current sprint restrictions remain defined in [`docs/SCOPE.md`](docs/SCOPE.md).

## Technology stack

- Frontend: Next.js 16.3.5, React, TypeScript, Tailwind CSS, shadcn-style UI components
- Backend: Python, FastAPI, SQLAlchemy, Alembic, Pydantic
- Database: PostgreSQL
- Authentication: hashed passwords and JWT bearer tokens
- AI extraction: Alibaba Cloud Model Studio / Qwen structured output, validated with Pydantic
- Course knowledge: text-extractable PDF ingestion, 1024-dimensional Model Studio embeddings, PostgreSQL pgvector retrieval, and grounded Qwen answers
- Container foundation: independent Backend and Frontend production images plus Docker Compose PostgreSQL 16 with pgvector

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

The Compose database uses the versioned `pgvector/pgvector:0.8.6-pg16-bookworm` image pinned to its multi-platform index digest, listens on `localhost:5432` with credentials supplied through `.env`, and persists data in a named volume. The image explicitly contains the pgvector server extension required by the existing Alembic migration. If using an existing PostgreSQL service, set `DATABASE_URL` to that instance instead. Runtime and acceptance testing require PostgreSQL.

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

## Production container foundation

Backend and Frontend deliberately use separate build contexts so the repository-root `.env` and unrelated files cannot enter either context:

```powershell
docker build -f backend/Dockerfile -t flowmind-backend:m1-a backend
docker build --build-arg NEXT_PUBLIC_API_URL=/api/v1 -f frontend/Dockerfile -t flowmind-frontend:m1-a frontend
```

The Backend image uses Python 3.12.14, installs the declared requirements under `backend/constraints.txt`, runs as UID/GID 10001, and starts `uvicorn app.main:app --host 0.0.0.0 --port 8000` without `--reload`. It does not run Alembic automatically. Deployment must run `alembic upgrade head` exactly once as a separate gate before starting application replicas.

Production must supply `DOCUMENT_STORAGE_ROOT=/var/lib/flowmind/documents` and mount a persistent, writable volume there. A named volume inherits the image directory ownership; a host bind mount must be writable by UID/GID 10001. PDF files are never part of the image. The database continues to store only generated relative storage keys.

The Frontend image uses Node 24.19.0, pnpm 11.25.0, the frozen pnpm lockfile, and Next.js standalone output. `NEXT_PUBLIC_API_URL` is public build-time configuration and the Docker build enforces the same-origin value `/api/v1`; it must never contain a secret or an internal Docker service name. Routing `/api/v1` to the Backend is intentionally deferred to the later Nginx/production-topology milestone.

Environment contracts are documented in `.env.example` for local development and `.env.production.example` for production. Values such as `DATABASE_URL`, `JWT_SECRET`, Model Studio credentials, `FRONTEND_URL`, and Document settings are Backend runtime configuration. No real secret belongs in either example or any image layer.

Health endpoints:

- `GET /health` remains the compatibility probe.
- `GET /health/live` checks only the FastAPI process.
- `GET /health/ready` checks `SELECT 1` against PostgreSQL and performs a temporary read/write probe in Document Storage. It does not call Qwen or Embedding and does not run migrations or extension checks.

## Implemented features

- Authentication: registration, login, current-user lookup, logout, password hashing, and JWT authentication.
- Courses: per-user create, list, detail, edit, and delete.
- Tasks: per-user create, list, detail, edit, status changes, delete, and optional course assignment.
- Dashboard: today's tasks, upcoming deadlines, course count, completed count, and recent tasks from the database.
- AI Structured Extraction: JWT-protected `POST /api/v1/ai/extract`, Qwen structured output, schema validation, and safe provider error responses.
- Draft Review: `/ai-import` with editable extraction results, loading, retry, and error states.
- Confirmed AI Import: JWT-protected `POST /api/v1/ai/import`, explicit course selection or creation, backend revalidation, and transactional persistence.
- Course Documents: PDF upload, processing status polling, safe failures, retry, delete, and authenticated source-file viewing on Course Detail.
- Grounded Course Questions: single-turn questions against READY Course material, strict structured output, backend-generated citations, normal no-answer results, and temporary page-session history.
- Existing landing, login, register, dashboard, courses, course detail, tasks, settings, and AI Import pages, with responsive navigation and form feedback.
- PostgreSQL persistence for `users`, `courses`, `tasks`, `documents`, and `document_chunks`, with pgvector enabled through Alembic.

## AI workflow

```text
User Text -> Qwen -> Structured Output -> Draft -> Human Confirmation
          -> Backend Validation -> Transaction -> Course / Task
```

AI does not directly write to the database. Extraction returns an editable draft without creating Courses or Tasks. On confirmation, the user explicitly selects an owned Course or chooses to create one; an inferred course name alone never creates a Course.

The backend validates the confirmed draft and Course ownership again. It creates exactly one Task with source `AI`; extracted steps are stored in its description and weak suggestions remain optional description content. All Course and Task writes for that confirmation share one transaction. The import endpoint does not call Qwen.

## Course knowledge architecture

```text
PDF
  -> Parse
  -> Page-aware Chunk
  -> text-embedding-v4
  -> PostgreSQL pgvector

Question
  -> text-embedding-v4
  -> User + Course + READY scoped Top-5 retrieval
  -> Qwen grounded structured output
  -> Backend-generated Citation
```

The browser never receives storage paths, embeddings, provider credentials, or model-produced document metadata. Source links use backend-generated `citation.document_id` and the authenticated PDF endpoint.

## RAG evaluation results

The fixed Sprint 2/M5 dataset contains 25 project-authored cases: 9 direct facts, 4 semantic paraphrases, 2 nearby-evidence questions, 5 unsupported questions, 3 prompt-injection cases, 1 cross-Course case, and 1 cross-User case. The explicit harness executed 24 real knowledge queries; the cross-User case consists of rejected query/list/file requests.

These measurements come from this 25-case synthetic baseline. They do not represent the overall accuracy of FlowMind on real, large-scale Course material collections.

Measured baseline on 2026-09-21 using real PostgreSQL/pgvector, `text-embedding-v4`, and Qwen:

- Retrieval Hit@5: **16/16 (100%)**
- Answer correctness: **14/16 (87.5%)**
- Groundedness: **14/16 (87.5%)**
- Answerable Citation coverage: **14/16 (87.5%)**
- Emitted Citation correctness: **14/14 (100%)**
- Abstention correctness: **8/8 (100%)**
- Prompt-injection cases: **3/3 passed**
- Course isolation: **24/24 queries passed**
- User isolation: query, document list, and PDF access all returned `404` without additional provider calls

The two recorded failures are `paraphrase_channel` and `paraphrase_headcount`. Their expected evidence was retrieved at Top-1, but Qwen conservatively returned unanswerable, so no unsupported answer or Citation was emitted. The baseline preserves these failures rather than changing Ground Truth or tuning the pipeline during M5.

Average/median latency across 24 queries was 695.05/619.00 ms for question embedding, 7.67/7.21 ms for retrieval, 8716.10/8460.29 ms for Qwen, and 9460.11/9354.57 ms total. The approximately 9.35-second median total latency is recorded as a future UX optimization candidate; Sprint 2 does not change the production pipeline in response. The run made 29 embedding requests (5 ingestion and 24 question), 24 Qwen requests, and used 15,272 prompt plus 7,075 completion tokens: 22,347 total, averaging 931.12 total tokens per evaluated query. The embedding provider does not expose token usage, so no embedding token estimate is reported.

The fixed input is [`backend/evaluation/rag_baseline.json`](backend/evaluation/rag_baseline.json), and the measured output is [`backend/evaluation/sprint2_m5_results.json`](backend/evaluation/sprint2_m5_results.json).

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

Real provider regressions are explicit scripts and are not part of ordinary pytest. Run the M5 baseline from the backend directory:

```powershell
python scripts/evaluate_rag.py --output evaluation/sprint2_m5_results.json
```

It requires working Model Studio credentials, PostgreSQL with pgvector, and network access. It uploads dedicated synthetic material, records retrieval/answers/latency/provider usage, checks evidence, citations, abstention, injection and isolation, and removes its dedicated rows and local files even when the run fails.

SQLite unit tests do not prove PostgreSQL persistence. Sprint 2 acceptance additionally runs the real browser, frontend, backend, Qwen service, and PostgreSQL through Course PDF upload, processing polling, grounded questions, Citation source viewing, refusal, refresh behavior, and cross-user authorization. Acceptance data and local PDFs must be removed afterward.

## Not implemented

- Persistent chat or multi-turn memory
- Reranking, BM25, hybrid search, GraphRAG, or similarity-threshold tuning
- OCR, non-PDF ingestion, or a PDF annotation/viewer system
- AI study plans
- Agents, subtasks, rate limiting, notifications, or multi-model routing
- OSS, SLS, ECS, Redis, or production deployment
- New database tables, new business modules, or a UI redesign
- Any business module excluded by `docs/SCOPE.md`

Deferred ideas belong in [`docs/BACKLOG.md`](docs/BACKLOG.md) and require a future scoped sprint.
