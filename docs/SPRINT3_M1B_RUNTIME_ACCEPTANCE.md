# Sprint 3 / M1-B Runtime Acceptance

Date: 2026-09-22. Status: implementation and local runtime acceptance complete; awaiting Owner Review. No commit, tag, or M2 work performed by this task.

## Delivery scope

Added an independent `compose.prod.yml`, Nginx HTTP configuration, minimal GitHub Actions CI, and a deployment runbook. Updated only environment documentation, Git ignore rules, and stage/scope documentation. M1-A is retained as the approved foundation. No application source, Dockerfile, dependency/lockfile, database migration, development Compose, prompt, Top-K, chunking, embedding model, retrieval logic, or AI Import behavior was changed by M1-B.

## Actual runtime environment

- Docker Engine 28.4.0; Compose v2.39.2-desktop.1; Linux containers on Docker Desktop.
- Isolated acceptance project: `flowmind-m1b-0922`.
- New named volumes: `flowmind-m1b-0922_postgres_data`, `flowmind-m1b-0922_documents`. Their absence was checked before creation.
- Single network: `flowmind-m1b-0922_app`, a private bridge with provider egress.
- Only Nginx published a port: `127.0.0.1:18080 -> 80`. Existing unrelated services on host port 80 were untouched.
- Backend and Frontend images were built through the production Compose file from their independent `./backend` and `./frontend` contexts, using tags `flowmind-backend:m1b-acceptance` and `flowmind-frontend:m1b-acceptance`.
- Backend actual UID/GID: 10001:10001; Frontend: 1000:1000. Their default image commands remained unchanged.
- Nginx actual version: 1.28.2. Final image reference: `nginx:1.28.2-alpine@sha256:5b4900b042ccfa8b0a73df622c3a60f2322faeb2be800cbee5aa7b44d241649e`.
- PostgreSQL retained the M1-A pgvector image/digest. Actual SQL: PostgreSQL `16.15 (Debian 16.15-1.pgdg12+2)`, vector extension `0.8.6`, embedding column `vector(1024)`.

## Startup and migration gates

The actual fresh deployment followed PostgreSQL healthy -> migration exit 0 -> Backend ready; Backend and Frontend health then allowed Nginx startup. The migration container remained `Exited (0)` rather than restarting. `alembic current` returned `20260920_0002 (head)`. A later `alembic check` reported `No new upgrade operations detected.`

The final digest-pinned Compose configuration was started successfully and structurally checked: exactly nginx/frontend/backend/postgres/migrate, no published application/database ports, and Backend depending on `migrate: service_completed_successfully`.

An additional independent project, `flowmind-m1b-0922-gate`, used fresh volumes and an intentionally missing Alembic target revision. Migration exited **255**, Compose startup failed, and Backend stayed **created / not running**. No application or migration source was altered to trigger this test.

## Same-origin HTTP acceptance

All application requests below used only `http://127.0.0.1:18080` through Nginx. This was an actual HTTP-client E2E run, not a browser-UI automation run or an in-process FastAPI TestClient substitute.

| Check | Result |
| --- | --- |
| GET `/` | 200, real Next.js HTML |
| Homepage static assets | All 9 returned 200 with non-empty content |
| Unauthenticated `/api/v1/auth/me` | Backend 401, proving API routing |
| Register and login | Passed |
| Course and Task creation | Passed; task moved to COMPLETED |
| AI Import | Real Qwen extraction followed by explicit confirmed import; persisted task source AI |
| PDF upload | 202 PROCESSING -> READY with real Embedding calls |
| Document persistence contract | Actual relative key matched `[a-f0-9]{32}.pdf`; one chunk with 1024-dimensional vector |
| Authenticated PDF download | 200, byte-for-byte match with the uploaded PDF |
| Upload-size boundary | A 20 MiB + 1 byte PDF plus multipart overhead reached FastAPI and received its JSON 413, not a premature Nginx rejection |
| RAG query | Real answerable response with document/page/chunk citation |
| External Host and port | API trailing-slash redirect retained the external origin and port |
| Browser bundle addresses | Fetched bundles contained neither localhost:8000 nor backend:8000; production build fixed the API base at `/api/v1` |

Question: “What is the laboratory report deadline?”

Answer: “The laboratory report deadline is October 15, 2026.”

Citation: acceptance.pdf, document 1, page 1, chunk 1.

PDF SHA-256: `21c99e1111524aa48697f74947ddc973c11a5accb659bbf706437e052b76e983`.

## Persistence and failure behavior

Backend and Frontend were force-recreated while Nginx continued running. After startup and Docker DNS re-resolution, a new login succeeded, the Course remained available, the Task remained COMPLETED, and the PDF download retained its original hash. No Nginx restart was needed.

PostgreSQL was stopped. `/health/live` remained 200, `/health/ready` returned `503 {"status":"not_ready"}`, and Docker marked Backend unhealthy. After PostgreSQL restarted, readiness returned to `200 {"status":"ready"}` and Docker later reported healthy. No Qwen or Embedding health dependency was added.

Final database counts after the vector regression rollback: users 1, courses 1, tasks 2 (manual and confirmed AI), documents 1, document_chunks 1.

## Security checks

- Actual container port bindings confirmed only Nginx published a host port; PostgreSQL, Backend, Frontend, and migration had none.
- Read-only secret files supplied Backend runtime settings and PostgreSQL credentials; images did not contain those files. Frontend/Nginx received no Backend secrets.
- Scanned image/container inspect data and combined application/migration logs in memory for known acceptance database/JWT/provider secrets and the test login token/password; no matches. No raw secret values or expanded configuration were printed.
- Scanned application-file bytes in clean Backend (50 files) and Frontend (1365 files) image containers for those known secret values; no matches. Relevant `.env`, `.venv`, local storage/PDF, or private-key paths were absent from image application trees.
- Backend/Frontend remained non-root. Runtime secret mounts are not an encrypted vault; privileged Docker/host access can read them, as documented in the runbook.
- Environment examples and delivery configuration contain placeholders or explicitly fake CI-only credentials, not real credentials.

## Regression and configuration validation

| Check | Actual result |
| --- | --- |
| Production Compose builds | Backend and Frontend passed |
| Host Backend pytest | 92 passed, 1 existing opt-in PostgreSQL test skipped |
| Linux Backend pytest with real PostgreSQL integration enabled | **93 passed**, no skips, two existing deprecation warnings |
| Frontend frozen-lockfile install | Passed, lockfile unchanged |
| Frontend lint | Passed |
| Frontend typecheck | Passed |
| Frontend production build | Passed, Next.js 16.3.5, same-origin build variable |
| `docker compose config --quiet` | Passed |
| Nginx `nginx -t` | Passed |
| `alembic check` | Passed, no new upgrade operations |
| actionlint 1.7.7 | Passed for final GitHub Actions workflow |
| `git diff --check` | Passed; only Git LF/CRLF conversion notices |

Linux tests used the approved production dependency image with constraints-pinned pytest installed in an ephemeral test directory. Real provider settings were explicitly disabled during pytest. CI follows the same dependency versions and existing vector integration test, but GitHub-hosted execution has **not** been run: no push/commit or remote workflow dispatch was performed. Branch-protection required checks still require Owner configuration.

## Issues found and boundaries

No M1-B business-code defect was found or fixed. The temporary acceptance tooling needed two corrections: bypassing a host HTTP proxy for loopback requests, and respecting startup/configuration initialization order when polling recreated containers and assembling pytest. These did not require weakening any test or changing production behavior.

Nginx dynamic Docker DNS resolution and 21 MiB multipart allowance were verified as part of the implementation. No 25-case evaluation was rerun because no RAG algorithm changed. This smoke test is not a general accuracy or load-test claim.

Known limitations remain: HTTP only; no domain/TLS/cloud/CD/HA/backup automation; single-process non-durable ingestion; file deletion/database commit not atomic; Compose dependency gates do not continuously stop existing Backend instances when dependencies fail. Follow the maintenance deployment sequence in `DEPLOYMENT.md` rather than bypassing the gate during upgrades.

## Cleanup and review

After acceptance, the dedicated test stack containers/network were removed using `down` without `-v`. The successful deployment's `flowmind-m1b-0922_postgres_data` and `flowmind-m1b-0922_documents` volumes were retained. Only the separate intentionally failing migration project's two test volumes were deleted after checking their exact names and Compose project labels; they contained no business acceptance data. Generated temporary credentials and local test tooling were removed. Reusing the retained database for another run requires supplying/resetting its test credentials; they are not retained in the repository or this report. Existing development and M1-A resources were not changed. This task stops at M1-B awaiting Owner Review.
