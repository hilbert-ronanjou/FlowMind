# Sprint 3 / M1-A Runtime Acceptance

Date: 2026-09-22. Scope: actual Docker runtime acceptance of the existing M1-A foundation; no M1-B implementation, commit, or tag.

## Environment and builds

- Docker Engine/client 28.4.0, API 1.51; Docker Compose v2.39.2-desktop.1; Docker Desktop 4.46.0. Linux amd64 containers.
- Backend built successfully with `docker build --progress=plain -t flowmind-backend:m1a-acceptance ./backend`.
- Frontend built successfully with `docker build --progress=plain --build-arg NEXT_PUBLIC_API_URL=/api/v1 -t flowmind-frontend:m1a-acceptance ./frontend`.
- Both builds were repeated successfully after the ignore-rule fix described below.

| Final image | Docker inspect Size (bytes) | Runtime identity | Runtime |
| --- | ---: | --- | --- |
| Backend | 77,870,982 | UID/GID 10001:10001 | Python 3.12.14 |
| Frontend | 92,544,055 | UID/GID 1000:1000 | Node 24.19.0; Next.js 16.3.5 |

Backend image ID: `sha256:2bb44e4ce50174698923b672f69de3279c6c2b23c77887cb0c3a91809ccdfe9c`.

Frontend image ID: `sha256:75132ea074d7761301333c44aca4a83c9208be52e326bc0ef1c4c2075ca7691c`.

Backend production dependencies installed successfully on Linux, including uvloop; `pip check` passed. No Windows/Linux constraints conflict was observed. Its default command is `uvicorn app.main:app --host 0.0.0.0 --port 8000`, without reload or migration.

Frontend pnpm 11.25.0 frozen-lockfile installation and production build passed. Standalone output contents are copied into `/app` (including `/app/server.js`), rather than retaining the build-stage `.next/standalone` directory wrapper. `.next/static` and `public` exist in the runtime image; this project has no public assets, so public is empty. The running container returned HTTP 200 for the homepage and all nine referenced static assets. Bundle inspection confirmed `/api/v1` and found neither `http://localhost:8000` nor the Backend Docker hostname.

## Fresh PostgreSQL and deployment gate

Used a newly created, isolated volume, without modifying existing development volumes:

`pgvector/pgvector:0.8.6-pg16-bookworm@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`

Actual running SQL results:

- PostgreSQL: `16.15 (Debian 16.15-1.pgdg12+2)`; container became healthy.
- `CREATE EXTENSION IF NOT EXISTS vector` succeeded.
- `pg_extension.extversion` for vector: `0.8.6`.
- Independent one-off Backend container ran `alembic upgrade head` successfully.
- `alembic current` and the database revision both confirmed `20260920_0002 (head)`.
- `document_chunks.embedding` has actual database type `vector(1024)`.

Before running Alembic, the default Backend command was started against the fresh database. Liveness worked, but a SQL check found none of users/courses/tasks/alembic_version. This confirms Backend startup did not silently run migrations.

## Health and storage

| Condition | Endpoint | Result |
| --- | --- | --- |
| Normal | `/health` | 200, `{"status":"ok"}` |
| Normal | `/health/live` | 200, `{"status":"live"}` |
| Normal | `/health/ready` | 200, `{"status":"ready"}` |
| PostgreSQL container stopped | `/health/live` | 200 |
| PostgreSQL container stopped | `/health/ready` | 503, `{"status":"not_ready"}` |
| PostgreSQL restarted | `/health/ready` | 200 |
| Read-only document mount | `/health/ready` | 503, `{"status":"not_ready"}` |

Failure bodies did not disclose database URLs, filesystem paths, exception details, or secrets. The additional read-only-mount container was removed after its check.

The named document volume was mounted at `/var/lib/flowmind/documents`. Actual UID/GID 10001:10001 successfully created, read, overwrote, and deleted a probe file. Three repeated readiness probes left zero `.health-*` files.

## Real API, persistence, and provider smoke test

1. Registered an acceptance user and created one course and one task through HTTP APIs.
2. Uploaded a real 676-byte text PDF through the authenticated document API: HTTP 202, `PROCESSING`, then `READY` using the real provider pipeline.
3. SQL confirmed a relative storage key matching `[a-f0-9]{32}.pdf`, one chunk, and vector dimensions 1024.
4. Authenticated download returned the original bytes. The volume file and HTTP download shared SHA-256 `21c99e1111524aa48697f74947ddc973c11a5accb659bbf706437e052b76e983`.
5. Stopped and removed the PostgreSQL container, then recreated it using the same named volume. User/course/task data persisted without rerunning migrations.
6. Stopped and removed the Backend container, then recreated it using the same document volume. The READY document remained downloadable with identical bytes.
7. After recreation, the real knowledge query "What is the laboratory report deadline?" returned `answerable=true`, answer "The laboratory report deadline is October 15, 2026.", and a citation to acceptance.pdf, page 1, chunk 1, document 1.

Final SQL counts: users 1, courses 1, tasks 1, documents 1, document_chunks 1. This is a minimal runtime smoke test, not a rerun of the 25-case evaluation or a general RAG accuracy claim.

## Runtime security

- PostgreSQL had no published host ports. Backend and Frontend acceptance ports were bound only to 127.0.0.1 (18000 and 13000 respectively).
- Both application containers ran as non-root users.
- Image application-file checks found no `.env`, `.venv`, local PDF storage, known real secret values, or private-key markers. Backend host Python caches were absent after the fix.
- Filtered image/container inspect checks found none of the known real secrets. Raw inspect output and secret values were not printed.
- Acceptance database and JWT credentials were generated for this isolated run, not copied from production configuration.
- For real provider calls only, the existing local provider configuration was mounted read-only at `/run/secrets/provider.env`. An ephemeral acceptance launcher loaded only the provider key/base/model into the process before exec-ing Uvicorn. Provider secrets were not baked into images or put in Docker Config.Env. This is test orchestration, not a new application secret-management implementation. The image's unmodified default command was separately validated.

## Regression and fixes

- Backend pytest: **92 passed, 1 skipped**, two deprecation warnings. The existing opt-in PostgreSQL test was skipped because `TEST_POSTGRES_DATABASE_URL` was not supplied to that pytest run; the actual PostgreSQL/vector acceptance above was executed separately.
- Frontend `pnpm lint`: passed.
- Frontend `pnpm typecheck`: passed.
- Frontend `NEXT_PUBLIC_API_URL=/api/v1 pnpm build`: passed.
- Both final Docker production builds: passed.

The initial Backend image contained 11 host `__pycache__` directories: directory allowlist rules had re-included files after broad exclusions. Added explicit sensitive/cache/storage exclusions after the allowlist in both build-context `.dockerignore` files, then rebuilt and rescanned both images. No business code, schema, migration, RAG algorithm, prompt, Top-K, or provider implementation was changed in this runtime-acceptance turn. Pre-existing uncommitted M1-A changes were preserved.

## Boundaries and retained resources

Not validated in this milestone: Nginx same-origin proxy integration, HTTPS, full production orchestration, CI/cloud deployment, load testing, and the complete 25-case RAG evaluation. They were not implemented. Frontend HTML/assets and Backend APIs were tested independently, so this report does not claim integrated production same-origin API routing.

The known filesystem-delete/database-commit non-atomic limitation remains unchanged.

Acceptance application/database containers were stopped and removed after testing. Both named volumes, the isolated network, and final images were retained for Owner Review:

- `flowmind-m1a-rt-0922-pgdata`
- `flowmind-m1a-rt-0922-documents`
- `flowmind-m1a-rt-0922-net`

No `down -v` was used. Existing unrelated containers and volumes were left unchanged. No commit or tag was created. Work stops at M1-A pending Owner Review.
