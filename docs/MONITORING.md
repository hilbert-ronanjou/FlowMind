# Private application monitoring (Sprint 3 / M3-C2)

The optional `compose.monitoring.yml` overlay adds only Prometheus and Grafana to the existing `flowmind-prod` project and private `app` network. Application services in `compose.prod.yml`, Nginx routing, and SLS / LoongCollector remain unchanged. No ECS deployment is performed by adding these repository files; complete the checks below on the production host.

Prometheus scrapes `http://backend:8000/metrics` every 30 seconds and has no published host port. Grafana uses the internal `http://prometheus:9090` data source; its sole host binding is `127.0.0.1:3000`. Do not add an ECS security-group rule for 3000/9090 or a public Nginx route. Keep one Backend worker/replica because its existing counters are process-local.

## Versions and resource budget

- Official Prometheus image: `prom/prometheus:v3.13.3` (current 3.13 LTS patch when selected).
- Official Grafana OSS Alpine image: `grafana/grafana:13.2.3`.
- Each container: 512 MiB memory limit, no additional swap, at most 0.5 CPU; combined ceiling 1 GiB on the 4 GiB ECS host. Watch actual use alongside application services and LoongCollector.
- TSDB: both 7-day retention and `1GB` size retention (Prometheus units are powers of two, approximately 1 GiB); whichever triggers first removes old blocks. This is **not a filesystem quota**: WAL, head chunks, compaction and delayed cleanup can exceed the target. Keep additional free disk space and inspect usage. Grafana's local database/configuration state is independently persisted.
- Monitoring container stdout logs rotate at 10 MiB x 3 files each. No application/SLS log configuration changes are made.

## Prepare credentials and deploy

Run these commands on ECS at the repository root, with the existing production stack healthy. Define this shell helper for all monitoring operations in this shell session:

```sh
fmmon() {
  docker compose --env-file .env.production -p flowmind-prod -f compose.prod.yml -f compose.monitoring.yml --profile monitoring "$@"
}
```

Prepare a new admin password locally; do not overwrite an existing password file:

```sh
install -d -m 0700 secrets
if [ ! -e secrets/grafana_admin_password.txt ]; then
  (umask 077; openssl rand -hex 32 > secrets/grafana_admin_password.txt)
fi
sudo chown 472:472 secrets/grafana_admin_password.txt
sudo chmod 0400 secrets/grafana_admin_password.txt
sudo test -s secrets/grafana_admin_password.txt
git check-ignore secrets/grafana_admin_password.txt
fmmon config --quiet
fmmon pull prometheus grafana
fmmon run --rm --no-deps --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
docker run --rm --network none --memory 512m --entrypoint /bin/promtool -v "$PWD/deploy/monitoring:/config:ro" prom/prometheus:v3.13.3 test rules /config/tests/flowmind-promql.test.yml
fmmon up -d prometheus grafana
fmmon ps prometheus grafana
```

Grafana runs under its official image UID 472. Compose file-backed secrets preserve host file permissions, so host ownership matters; `uid`/`mode` fields alone do not fix bind-mount permissions. Docker root can access the protected host directory, and Grafana can read its mounted secret. `secrets/` is already Git-ignored. Never commit this file or a rendered configuration containing credentials. No default password is supplied in Git: missing/unreadable/empty secret files must be corrected before startup. Use the same owner-only handling for production env files as in `DEPLOYMENT.md`.

The login name is `flowmind-admin`. Retrieve the generated password privately on ECS; do not paste it in command arguments, logs, tickets, or this repository. `GF_SECURITY_ADMIN_PASSWORD__FILE` bootstraps the initial Grafana database account. Changing that file after the database exists does **not** rotate the account; change the password through the authenticated Grafana UI and update the protected operator record. Anonymous access and self-sign-up are disabled; login remains required. Telemetry, update checks, and unified alerting are disabled. No plugins are installed.

`monitoring` is optional. The normal command using only `compose.prod.yml` retains its existing startup. Even when both files are supplied, an `up -d` without `--profile monitoring` does not start the monitoring services. Explicit `up -d prometheus grafana` above starts only these services; it neither recreates Backend nor reruns migrations.

## Verify private connectivity and access Grafana

```sh
fmmon exec prometheus wget -q -O - http://backend:8000/metrics
fmmon exec prometheus wget -q -O - http://127.0.0.1:9090/-/ready
fmmon exec prometheus wget -q -O - 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22flowmind-backend%22%7D'
fmmon exec prometheus wget -q -O - http://127.0.0.1:9090/api/v1/targets
curl -fsS http://127.0.0.1:3000/api/health
fmmon port grafana 3000
ss -lnt | grep -E ':3000|:9090'
```

Wait for at least two successful 30-second scrapes before evaluating rates. `up{job="flowmind-backend"}` should be 1; `activeTargets` should show `backend:8000/metrics`. Grafana should report a healthy database and bind only to loopback; there should be no host listener for 9090. Grafana's `/api/health` is a process/database check, not access to dashboards or administration data.

From the operator's workstation, keep an SSH tunnel open (substitute the existing SSH user, host, and key as needed):

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:13000:127.0.0.1:3000 <ssh-user>@<ecs-host>
```

Open `http://127.0.0.1:13000`, log in, and select **FlowMind / FlowMind Production Overview**. Do not expose the tunnel on `0.0.0.0`. The default provisioned data source is server-side and stays inside Docker. Unauthenticated dashboard/API requests must be rejected or redirected to login, while health checks remain accessible.

## Dashboard interpretation

One version-controlled dashboard is loaded read-only from `deploy/monitoring/grafana/dashboards/flowmind.json`. Edit this source through review rather than saving UI changes. Grafana provisioning updates the same dashboard UID; the data source has the fixed UID `flowmind-prometheus` and a 30-second minimum interval. Default view is six hours, refreshed every 30 seconds.

Panels cover scrape health; HTTP requests/s, 5xx percentage and histogram P95; AI requests by operation/outcome and P95 duration; reported tokens by operation/direction; RAG outcomes/P95; and document success/failure/P95. HTTP rate windows use `$__rate_interval` (at least four scrape intervals), operation counts use rolling 15-minute `increase`, and token change uses the selected dashboard range. `rate`/`increase` handle observed counter resets, but cannot reconstruct operations between the final scrape and a process crash/restart or before the first scrape. Low-volume `increase` values may be fractional because Prometheus extrapolates them.

The HTTP rate/error panels show zero when no traffic series exists; always inspect target health before interpreting zero as healthy inactivity. Duration panels preserve missing/NaN rather than inventing zero latency. Quantiles are estimates from the existing histogram buckets. Provider token series are displayed only when Backend actually receives usage; currently grounded-answer Qwen input/output tokens. Missing usage is shown as **Not reported**, with no embedding token estimate or cost calculation. Counter changes are observations, not billing evidence. Request IDs and user/Course/Document IDs are never dashboard label dimensions. Backend health probes and `/metrics` remain excluded from generic HTTP measurements.

## Persistence, resource inspection, recovery and shutdown

```sh
docker volume inspect flowmind-prod_prometheus_data flowmind-prod_grafana_data
fmmon exec prometheus du -sh /prometheus
fmmon exec grafana du -sh /var/lib/grafana
docker stats --no-stream $(fmmon ps -q prometheus grafana)
free -h
df -h
docker system df
fmmon logs --tail=100 prometheus grafana
fmmon restart prometheus grafana
```

Verify volume mounts survive restart, historical graphs remain, and Grafana accepts the same account. The provisioned dashboard is also restored from Git. Prometheus replays its WAL after interruption; allow time to become ready. Inspect OOM state if either service repeatedly restarts:

```sh
docker inspect --format '{{.Name}} memory={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}} oom={{.State.OOMKilled}}' $(fmmon ps -q prometheus grafana)
```

If Backend is down, monitoring remains available and the target reports down; restore Backend using the application runbook. If a monitoring configuration changes, validate first, then use `fmmon up -d --no-deps --force-recreate prometheus grafana` without changing volume names. Never delete TSDB/Grafana volumes to fix a startup error. Preserve the same Docker host and project name. For unrecoverable storage failure, restore an operator-held backup while the affected service is stopped; automated backup is outside this milestone.

To stop only monitoring safely:

```sh
fmmon stop -t 30 grafana prometheus
```

Use `fmmon up -d prometheus grafana` to resume. Do not use `fmmon down` to stop only monitoring: the merged project also includes the application stack. Never use `down -v`, `docker volume prune`, or remove either monitoring volume when data must survive. Grafana and Prometheus volumes are separate from PostgreSQL, PDFs, and certificate data.

References: [Prometheus releases](https://prometheus.io/download/), [Prometheus retention behavior](https://prometheus.io/docs/prometheus/latest/storage/), [Grafana OSS images](https://grafana.com/grafana/download?edition=oss&platform=docker), [Grafana Docker secrets](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/), [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).
