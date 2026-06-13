# Oculory

Observability platform for the **GiveWGun** Oracle A1 VM — one Grafana-based stack covering all
**three pillars**: metrics (Prometheus), logs (Loki), and distributed traces (Tempo), collected
by a single agent (Grafana Alloy) and surfaced as a central fleet dashboard plus per-service
dashboards. Alerts go to **Telegram and email**. Reachable at `https://oculory.givewgun.com`
behind Cloudflare Access.

```
Grafana ── Prometheus (metrics) ── exporters + app /metrics
   │     ── Loki (logs)       ─┐
   │     ── Tempo (traces)    ─┴── Grafana Alloy (docker log tail + OTLP receiver)
   └── Alertmanager → Telegram + Email
```

Monitored: `gunvest-app`, `gunvest-db`, the 10 `legion-*` containers (NATS/Ollama/agents/api/web),
`horizon-app`, and the `global-tunnel` cloudflared.

## Documentation
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how it works: the three pillars, data flow,
  networking, correlation, and CI/CD. **Start here.**
- **[docs/ONBOARDING.md](docs/ONBOARDING.md)** — step-by-step to **plug a new service/project**
  into Oculory (instrument metrics/logs/traces, add a scrape target, dashboard, alerts).
- **[docs/METRICS.md](docs/METRICS.md)** — full, audience-segmented metric catalog (NOC / App /
  Financial) — the source of truth for every metric name, type, and label.

## Layout
```
docker-compose.yml      platform stack (ARM64), joins docker_default + app_legion + tunnel-gateway
.env.example            copy to .env and fill
prometheus/             prometheus.yml + rules/
loki/ tempo/ alloy/     pillar configs (+ loki/rules for log alerts)
alertmanager/           alertmanager.tmpl.yml -> rendered to alertmanager.yml at deploy
blackbox/               synthetic probe modules
grafana/provisioning/   datasources (with trace<->log<->metric correlation) + dashboard provider
grafana/dashboards/     Fleet, GunVest, Legion, Horizon, Infra/USE, Logs&Traces
scripts/                deploy.sh, create-pg-monitor.sql
```

## Prerequisites (one-time)

1. **Telegram bot** — create a new bot via [@BotFather](https://t.me/BotFather), grab the token;
   message the bot, then get your chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. **Postgres monitoring role** — create the read-only role on gunvest-db:
   ```bash
   sudo docker exec -i gunvest-db psql -U <DB_USER> -d <DB_NAME> < scripts/create-pg-monitor.sql
   ```
   (edit the password in the SQL first), then put it in `PG_EXPORTER_DSN`.
3. **App instrumentation** — rebuild the three app stacks so they expose `/metrics`, push OTLP
   traces to `oculory-alloy:4317`, and log JSON. See "App changes" below.
4. **Infra endpoints** — enable on the existing stacks (already applied by this project):
   - legion `docker-compose.prod.yml`: NATS `command: ['-js','-m','8222']`, `expose: ['8222']`.
   - gunvest `gateway/docker-compose.yml`: cloudflared `command: tunnel --metrics 0.0.0.0:2000 run`, `expose: ['2000']`.

## Configure & deploy
```bash
cp .env.example .env        # fill Grafana pw, PG DSN, Telegram, SMTP
./scripts/deploy.sh         # rsync -> /opt/oculory, render alertmanager, compose up -d
```
The deploy script renders `alertmanager.yml` from the template using your `.env` (the rendered
file holds secrets and is git-ignored).

## Cloudflare Access (console — only manual UI step)
In **Cloudflare Zero Trust → Networks → Tunnels →** the existing `global-tunnel` → Public Hostnames,
add:
- **Hostname:** `oculory.givewgun.com`  **Service:** `http://oculory-grafana:3000`
Then **Access → Applications → Add** a self-hosted app for `oculory.givewgun.com` with an
allow policy for `aongoong.jp@gmail.com`. (Grafana's own admin login stays as a second gate.)

## Verify
```bash
ssh -i <key> -L 9090:localhost:9090 -L 3000:localhost:3000 ubuntu@161.118.201.235
```
- Prometheus `http://localhost:9090/targets` — every target **UP**.
- Grafana `https://oculory.givewgun.com` (after Access) — dashboards populate.
- Logs: Explore → Loki → `{service="gunvest"}`.
- Traces: trigger a legion cycle (`POST /api/trigger`) → Tempo shows one end-to-end trace; an
  exemplar dot on a latency panel opens the trace; "Logs for this span" jumps to Loki.
- Alert test: `docker stop horizon-app` → `ContainerDown` arrives in Telegram **and** email.

## App changes (what was added to each repo)
- **gunvest** `backend/`: prom-client `/metrics` (RED + pg pool + jobs + business gauges), OTel
  auto-instrumentation, pino JSON logs.
- **legion** `src/`: shared `:9100/metrics` + OTel for every worker, api RED, pipeline counters,
  manual agent/Ollama spans, W3C trace context in NATS headers.
- **horizon** `packages/backend/`: fastify-metrics `/metrics`, sqlite counters, OTel, pino.

OTLP endpoint for all apps: `http://oculory-alloy:4317` (gRPC). Set
`OTEL_EXPORTER_OTLP_ENDPOINT` + `OTEL_SERVICE_NAME` per app via env.

## Operating notes
- Retention (disk-bounded, ~24 GiB free): Prometheus 15d/6GB, Loki 14d, Tempo 7d.
- Everything but Grafana binds to `127.0.0.1`; reach via SSH port-forward.
- Add a scrape target: edit `prometheus/prometheus.yml`, `curl -X POST localhost:9090/-/reload`.
