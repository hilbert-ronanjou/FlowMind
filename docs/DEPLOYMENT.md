# Production HTTPS deployment (Sprint 3 / M2-C2-B)

This is a single-host, single-Backend-worker HTTPS deployment for `flowmind.hilbertspace.cloud`. Nginx remains the only public entry. The stack deliberately retains two explicit configurations: `default.conf` for first-deployment HTTP/ACME bootstrap without a certificate, and `default.https.conf` for final HTTPS operation after issuance. No business behavior, CD, or automated backup is included.

## Prerequisites and configuration

- Docker Engine with Linux containers and Docker Compose v2 supporting `service_completed_successfully` (validated with Engine 28.4.0 / Compose 2.39.2).
- Run all commands at the repository root. Use this **separate** production file and a stable project name; do not combine it with the development Compose file.
- An available HTTP port; disk space for images, PostgreSQL, and PDF volumes; outbound access to the configured Model Studio endpoint.
- Copy `.env.production.example` to `.env.production`. It is ignored by Git. Protect it with owner-only host permissions / Windows ACLs. Never paste its contents, expanded Compose configuration, or credentials into logs or tickets.
- Create `secrets/postgres_password.txt` containing a newly generated random password, without shell quotes. Use a high-entropy hexadecimal password (e.g. 32 random bytes encoded as hex) to avoid URL/INI escaping pitfalls. Protect this file too; `secrets/` is ignored by Git.
- Set `POSTGRES_PASSWORD_FILE` to this file and put the **same** password in `DATABASE_URL`, whose hostname must be `postgres`, not localhost. Keep database/user names consistent. Set a separate random `JWT_SECRET` of at least 32 characters.
- Set `FRONTEND_URL` to the exact browser origin: `http://flowmind.hilbertspace.cloud` during Phase A, then `https://flowmind.hilbertspace.cloud` during Phase B. Configure existing `DASHSCOPE_API_KEY`, `DASHSCOPE_BASE_URL`, and `QWEN_MODEL` for real AI calls. No AI key is required for container readiness or CI.
- Keep the approved embedding, chunking, retrieval, and document-limit settings unchanged. `DOCUMENT_STORAGE_ROOT=/var/lib/flowmind/documents` is enforced by Compose.
- Embedding dimensions are an application/schema invariant: `embedding_dimensions: Literal[1024] = 1024` matches PostgreSQL `vector(1024)`, not an operator-tunable runtime setting. Do not set `EMBEDDING_DIMENSIONS`. For existing deployments, remove any legacy assignment from runtime env files and container environment before starting Backend or migration; leaving it empty or quoting `1024` does not fix the string validation error.
- `BACKEND_ENV_FILE` selects the runtime configuration file; default `./.env.production`. If using another `--env-file`, explicitly set this path as well. The file is mounted read-only at `/app/.env` for Backend and migration, using their existing settings loader. It is **not** a Docker build input or container Config.Env secret. PostgreSQL reads its separate password file through `POSTGRES_PASSWORD_FILE`.
- `NEXT_PUBLIC_API_URL=/api/v1` is public build-time configuration, fixed in the production build. Frontend and Nginx receive no Backend secret file. Compose secrets on a local host are file mounts, not an encrypted secret vault; Docker administrators can still access them. On Linux ensure mounted Backend files are readable by UID 10001 (for example owner UID 10001 and mode 0400), while host-directory permissions prevent other host users from accessing them.
- `NGINX_CONFIG=default.conf` is the certificate-free bootstrap default. Change it to `default.https.conf` only after the named certificate files exist in `letsencrypt_data`. This variable selects a repository-owned configuration filename; it contains no certificate or secret material.

`HTTP_BIND_ADDRESS=127.0.0.1` is intentionally safe by default. `HTTP_PORT=8080` is configurable. On ECS set `HTTP_BIND_ADDRESS=0.0.0.0` and `HTTP_PORT=80`, and allow inbound TCP ports 80 and 443 in the host firewall and security group. Only Nginx publishes these ports; Frontend 3000, Backend 8000, and PostgreSQL 5432 remain within the Docker bridge.

## Two-phase fresh deployment

### Phase A — HTTP bootstrap and certificate issuance

Start every fresh server with `NGINX_CONFIG=default.conf`. This configuration does not reference certificate files, so an empty `letsencrypt_data` volume cannot prevent Nginx startup. Set `FRONTEND_URL=http://flowmind.hilbertspace.cloud`, expose public port 80, and keep the stable project name so the issued certificate remains in the same named volume.

```sh
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml config --quiet
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml build backend frontend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml up -d
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml ps -a
```

Confirm the DNS A record resolves to this ECS public IPv4 and that the HTTP ACME path reaches this Nginx instance. Then substitute a real operator email locally and issue the first certificate:

```sh
curl -i http://flowmind.hilbertspace.cloud/.well-known/acme-challenge/bootstrap-check
docker compose --profile acme --env-file .env.production -p flowmind-prod -f compose.prod.yml run --rm certbot certonly --webroot --webroot-path /var/www/certbot --email replace-with-your-email@example.com --agree-tos --no-eff-email --non-interactive -d flowmind.hilbertspace.cloud
```

The probe should return a direct Nginx 404, not Frontend content or an HTTPS redirect. The issuance command is documented for the production host; it was not run during repository validation. Backend and migration reuse one image. Frontend uses its frozen lockfile and standalone build. PostgreSQL uses the pinned PostgreSQL 16/pgvector digest, and Nginx 1.28.2-alpine is pinned by digest.

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

Both configurations serve `/.well-known/acme-challenge/` directly from the dedicated Certbot webroot, so those requests never reach Frontend or Backend. In Phase A, other HTTP requests retain the original routing. In Phase B, other HTTP requests receive a permanent 308 redirect to the fixed HTTPS domain; the HTTPS server preserves `/api/v1` routing to Backend and sends every other route to Frontend. It forwards Host, client address, and scheme. Docker DNS is resolved dynamically after application-container recreation. The 21 MiB request ceiling, timeouts, and no-retry behavior remain unchanged.

```sh
curl -f http://localhost:8080/
curl -i http://localhost:8080/api/v1/auth/me
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read().decode())"
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml logs --tail=100 backend frontend nginx
```

Unauthenticated `/api/v1/auth/me` should return 401 (Backend routing works). Backend health endpoints remain internal: `/health/live` checks only the process, `/health/ready` checks PostgreSQL and Document Storage. No provider or full migration/extension polling is included. Frontend checks HTTP internally; the Nginx health check selects local HTTP or local HTTPS according to `NGINX_CONFIG` and verifies the proxied homepage. Its local HTTPS probe disables certificate verification only because it addresses `127.0.0.1`; public clients still perform normal certificate validation.

Never log request bodies, JWTs, passwords, or API keys. Nginx access logs omit headers, bodies, and query strings. Review diagnostic logs before sharing them; Docker-administrator access remains privileged.

### Phase B — switch to HTTPS after successful issuance

Confirm the two certificate paths exist through Certbot without displaying their contents. Then change the real `.env.production` to `NGINX_CONFIG=default.https.conf` and `FRONTEND_URL=https://flowmind.hilbertspace.cloud`. Recreate Backend so it reloads its origin setting, then recreate Nginx because the selected bind-mounted configuration changes at container creation time:

```sh
docker compose --profile acme --env-file .env.production -p flowmind-prod -f compose.prod.yml run --rm certbot certificates
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml config --quiet
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml up -d --no-deps --force-recreate backend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml up -d --no-deps --force-recreate nginx
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml exec nginx nginx -t
```

Validate HTTPS, same-origin API routing, the HTTP redirect, and the non-redirected renewal path:

```sh
curl -fsS https://flowmind.hilbertspace.cloud/
curl -i https://flowmind.hilbertspace.cloud/api/v1/auth/me
curl -I http://flowmind.hilbertspace.cloud/
curl -i http://flowmind.hilbertspace.cloud/.well-known/acme-challenge/renewal-check
```

Expect the API request to return 401 while unauthenticated, the ordinary HTTP request to return 308 with `Location: https://flowmind.hilbertspace.cloud/`, and the missing ACME token to return 404 without a redirect. No HSTS header is configured during this validation milestone.

## Manual certificate renewal

The `certbot` service remains an on-demand tool under the `acme` profile; normal application startup excludes it. Nginx mounts `certbot_webroot` and `letsencrypt_data` read-only, while Certbot has read/write access. PostgreSQL and uploaded-document volumes remain independent.

Run the simulation first. For an actual due renewal, run `renew` and reload Nginx only after Certbot exits successfully:

```sh
docker compose --profile acme --env-file .env.production -p flowmind-prod -f compose.prod.yml run --rm certbot renew --dry-run
docker compose --profile acme --env-file .env.production -p flowmind-prod -f compose.prod.yml run --rm certbot renew
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml exec nginx nginx -s reload
```

No always-running Certbot daemon, cron job, or systemd timer is introduced in this milestone. Never commit the operator email, certificate/private-key data, ACME account data, or a rendered copy of either named volume.

## Restart, persistence, and shutdown

```sh
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml restart backend frontend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml up -d --no-deps --force-recreate backend frontend
docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml down
```

The first two commands are for the **same already-migrated release**, not substitutes for the deployment gate. Nginx re-resolves replaced application addresses within approximately 10 seconds. Preserve a stable project name and Docker host: default volumes are `flowmind-prod_postgres_data` and `flowmind-prod_documents`. The Document volume mounts at `/var/lib/flowmind/documents`; the image initializes ownership for UID/GID 10001. Database paths remain relative keys. If replacing it with a host bind mount, arrange permissions before startup; do not run Backend as root to hide a permission problem.

**`docker compose ... down` does not delete the named volumes.** It stops/removes the stack containers and network. **`docker compose ... down -v` deletes its data volumes, including PostgreSQL records and uploaded PDFs. Do not run it against data that must survive.** Rebuilding images also does not delete named volumes. Removing a Docker Desktop VM or changing hosts does not transfer data automatically.

## Common failures and limitations

- Port 80 or 443 occupied: resolve the conflicting listener deliberately; never stop unrelated containers without identifying them first.
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
