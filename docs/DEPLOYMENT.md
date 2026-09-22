# Production delivery baseline (Sprint 3 / M1-B)

This is a single-host, single-Backend-worker HTTP baseline, not an Internet-ready HTTPS deployment. M1-A images and all business behavior remain unchanged. HTTPS/domain configuration is M2; no cloud deployment, CD, or automated backup is included.

## Prerequisites and configuration

- Docker Engine with Linux containers and Docker Compose v2 supporting `service_completed_successfully` (validated with Engine 28.4.0 / Compose 2.39.2).
- Run all commands at the repository root. Use this **separate** production file and a stable project name; do not combine it with the development Compose file.
- An available HTTP port; disk space for images, PostgreSQL, and PDF volumes; outbound access to the configured Model Studio endpoint.
- Copy `.env.production.example` to `.env.production`. It is ignored by Git. Protect it with owner-only host permissions / Windows ACLs. Never paste its contents, expanded Compose configuration, or credentials into logs or tickets.
- Create `secrets/postgres_password.txt` containing a newly generated random password, without shell quotes. Use a high-entropy hexadecimal password (e.g. 32 random bytes encoded as hex) to avoid URL/INI escaping pitfalls. Protect this file too; `secrets/` is ignored by Git.
- Set `POSTGRES_PASSWORD_FILE` to this file and put the **same** password in `DATABASE_URL`, whose hostname must be `postgres`, not localhost. Keep database/user names consistent. Set a separate random `JWT_SECRET` of at least 32 characters.
- Set `FRONTEND_URL` to the exact browser HTTP origin, including its port. The default is `http://localhost:8080`. Configure existing `DASHSCOPE_API_KEY`, `DASHSCOPE_BASE_URL`, and `QWEN_MODEL` for real AI calls. No AI key is required for container readiness or CI.
- Keep the approved embedding, chunking, retrieval, and document-limit settings unchanged. `DOCUMENT_STORAGE_ROOT=/var/lib/flowmind/documents` is enforced by Compose.
- `BACKEND_ENV_FILE` selects the runtime configuration file; default `./.env.production`. If using another `--env-file`, explicitly set this path as well. The file is mounted read-only at `/app/.env` for Backend and migration, using their existing settings loader. It is **not** a Docker build input or container Config.Env secret. PostgreSQL reads its separate password file through `POSTGRES_PASSWORD_FILE`.
- `NEXT_PUBLIC_API_URL=/api/v1` is public build-time configuration, fixed in the production build. Frontend and Nginx receive no Backend secret file. Compose secrets on a local host are file mounts, not an encrypted secret vault; Docker administrators can still access them. On Linux ensure mounted Backend files are readable by UID 10001 (for example owner UID 10001 and mode 0400), while host-directory permissions prevent other host users from accessing them.

`HTTP_BIND_ADDRESS=127.0.0.1` is intentionally safe by default. `HTTP_PORT=8080` is configurable. Only Nginx publishes a port; Frontend 3000, Backend 8000, and PostgreSQL 5432 remain within the Docker bridge. An explicitly reviewed non-loopback bind can expose HTTP, but credentials and PDFs would then travel without TLS; do not use real users over an untrusted network before M2.

## Build and fresh startup

```sh
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml config --quiet
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml build backend frontend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml up -d
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml ps -a
```

Backend and migration reuse one image. Frontend uses its frozen lockfile and standalone build. PostgreSQL uses the M1-A pinned PostgreSQL 16 / pgvector digest. Nginx 1.28.2-alpine is also pinned by digest. Each independent acceptance deployment must use a new `-p` project name; project-scoped volumes avoid touching development or other acceptance data.

## Migration lifecycle

```text
PostgreSQL healthy -> migrate: alembic upgrade head -> exit 0
                                                    -> Backend ready
Frontend healthy + Backend ready                    -> Nginx starts
```

The migration service has no restart policy and no replicas; Backend keeps the unmodified image command and never migrates. A failed migration blocks a fresh Backend startup. Verify the gate explicitly:

```sh
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml logs migrate
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml run --rm --no-deps migrate alembic current
```

Expected current head: `20260920_0002`. Do not run concurrent deployment/migration commands or scale Backend; in-process PDF tasks and stale recovery assume one worker/replica. Compose dependency gates apply to startup, not continuous dependency supervision. Already-running old application containers are not stopped by a newly failed migration.

For an image/configuration update, use a maintenance window: build first, stop Nginx and Backend, and remove the completed migration container so the next deployment cannot rely on stale success:

```sh
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml build backend frontend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml stop nginx backend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml rm -f migrate
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml up -d
```

Do not resume service after a migration failure until the cause is corrected and the one-shot succeeds. Changing the password file does not rotate an existing PostgreSQL user's password; coordinate an explicit database credential rotation separately.

## Routing, health, and logs

Nginx preserves `/api/v1` paths and proxies them to Backend; other requests go to Frontend. It forwards Host (including the external port), client address, and HTTP scheme. As the sole edge, it replaces untrusted incoming forwarded-address values. Docker DNS is resolved dynamically to survive application-container recreation. The 21 MiB request ceiling allows a 20 MiB PDF plus multipart overhead; FastAPI remains responsible for the 20 MiB file limit. API read timeout is 180 seconds; the proxy does not retry failed write requests across application instances.

```sh
curl -f http://localhost:8080/
curl -i http://localhost:8080/api/v1/auth/me
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read().decode())"
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml logs --tail=100 backend frontend nginx
```

Unauthenticated `/api/v1/auth/me` should return 401 (Backend routing works). Backend health endpoints remain internal: `/health/live` checks only the process, `/health/ready` checks PostgreSQL and Document Storage. No provider or full migration/extension polling is included. Frontend health checks HTTP `/`; Nginx health checks the proxied homepage. Database unavailability marks Backend unhealthy and restores readiness when the database returns; Docker health status alone does not restart an unhealthy process.

Never log request bodies, JWTs, passwords, or API keys. Nginx access logs omit headers, bodies, and query strings. Review diagnostic logs before sharing them; Docker-administrator access remains privileged.

## Restart, persistence, and shutdown

```sh
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml restart backend frontend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml up -d --no-deps --force-recreate backend frontend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml down
```

The first two commands are for the **same already-migrated release**, not substitutes for the deployment gate. Nginx re-resolves replaced application addresses within approximately 10 seconds. Preserve a stable project name and Docker host: default volumes are `flowmind-prod_postgres_data` and `flowmind-prod_documents`. The Document volume mounts at `/var/lib/flowmind/documents`; the image initializes ownership for UID/GID 10001. Database paths remain relative keys. If replacing it with a host bind mount, arrange permissions before startup; do not run Backend as root to hide a permission problem.

**`docker compose ... down` does not delete the named volumes.** It stops/removes the stack containers and network. **`docker compose ... down -v` deletes its data volumes, including PostgreSQL records and uploaded PDFs. Do not run it against data that must survive.** Rebuilding images also does not delete named volumes. Removing a Docker Desktop VM or changing hosts does not transfer data automatically.

## Common failures and limitations

- Port occupied: choose another `HTTP_PORT`; never stop unrelated containers to free a port.
- Missing/unreadable secret file: check the path, Docker file sharing, and UID permissions without printing file contents.
- PostgreSQL unhealthy: inspect its safe logs, volume/disk availability, and DB/user configuration. Never delete the volume as a repair shortcut.
- Migration failure: inspect exit status and sanitized logs; Backend must remain stopped. Re-run only after correcting the cause; no automatic downgrade or data reset.
- Backend unready: check PostgreSQL connectivity and writable document volume. Qwen/Embedding outages do not affect readiness.
- Nginx 502: check application health, Docker DNS/network, and the 10-second re-resolution window after recreation. Verify `nginx -t` inside the container.
- PDF 413: Nginx permits 21 MiB total requests, while the application limits each PDF to 20 MiB. Do not change application limits to work around a proxy setting.
- Provider failure: check server-side configuration and provider availability; never change the browser API base to a Docker hostname.
- In-process ingestion is not a durable queue. Interrupted processing follows the existing stale-recovery/retry behavior. File deletion and database commit remain non-atomic. No backup automation, HA, zero-downtime migration, or automatic recovery system is claimed.

## CI

`.github/workflows/ci.yml` runs for PRs, pushes to main/master/codex branches, and manual dispatch. Backend installs constraints-locked test dependencies, upgrades an ephemeral PostgreSQL/pgvector database, and runs pytest including the vector integration test. Frontend runs frozen install, lint, typecheck, and build. A separate job builds both images from their own contexts. CI has read-only repository permission, no real provider credentials, no paid evaluation, no image push, and no deployment. A repository owner must configure the three CI jobs as required checks to enforce a merge gate; adding YAML alone cannot change branch protection.

References: [Compose startup dependencies](https://docs.docker.com/compose/how-tos/startup-order/), [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/), [Nginx proxy semantics](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).
