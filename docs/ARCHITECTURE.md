# Oculory — How It Works

Oculory is the observability platform for the GiveWGun Oracle A1 VM. It collects the **three
pillars** — metrics, logs, and traces — from every service on the box and unifies them in
Grafana, with alerting to Telegram and email.

This document explains the moving parts and the data flow. To **add a new service**, see
[ONBOARDING.md](./ONBOARDING.md). For the full metric list, see [METRICS.md](./METRICS.md).

---

## 1. The big picture

```
                         ┌───────────────────────────── Grafana ─────────────────────────────┐
   Cloudflare Tunnel ───▶│  dashboards (provisioned) · Explore · unified alerting             │
   + Access (oculory.    │   datasources: Prometheus (metrics) · Loki (logs) · Tempo (traces) │
   givewgun.com)         └───────┬─────────────────────┬───────────────────────────┬─────────┘
                                 │ PromQL              │ LogQL                      │ TraceQL
                          ┌──────▼──────┐      ┌────────▼────────┐         ┌─────────▼────────┐
                          │ Prometheus  │      │      Loki       │         │      Tempo       │
                          │  (metrics)  │      │     (logs)      │         │     (traces)     │
                          └──────┬──────┘      └────────▲────────┘         └────────▲─────────┘
              scrape /metrics    │                      │ push                      │ OTLP push
        ┌──────────────────────-─┤              ┌───────┴───────────────────────────┴────────┐
        │ exporters + app targets │              │            Grafana Alloy (collector)       │
        │  docker-stats node-exp. │              │  • tails every container's stdout -> Loki   │
        │  postgres  nats  blackbox              │  • OTLP receiver :4317/:4318 -> Tempo       │
        │  cloudflared            │              │    (tail-sampling: keep errors + slow)      │
        │  gunvest/legion/horizon │◀─────────────┘   reads /var/run/docker.sock + log files    │
        │  /metrics               │
        └─────────────────────────┘
   Prometheus ─ rules ─▶ Alertmanager ─▶ Telegram + Email
```

Everything runs as one Docker Compose stack (`/opt/oculory`), bound to `127.0.0.1` except
Grafana, which is published only through the Cloudflare Tunnel behind Cloudflare Access.

## 2. Why these tools

| Need | Tool | Why |
|---|---|---|
| Metrics store + query | **Prometheus** | pull model, PromQL, the de-facto standard; exemplars link to traces |
| Logs | **Loki** | label-indexed, cheap on disk, native Grafana integration |
| Traces | **Tempo** | trace storage + service-graph/span-metrics generator; TraceQL |
| Collection | **Grafana Alloy** | one agent for *both* log tailing and OTLP trace receiving (fewer moving parts than Promtail + a separate OTel Collector) |
| Dashboards/alerting UI | **Grafana** | single pane over all three datasources, provisioned-as-code |
| Routing alerts | **Alertmanager** | dedupe/group/inhibit + Telegram & email receivers |

## 3. The three pillars in detail

### Metrics (pull)
- Prometheus scrapes `/metrics` endpoints every 15s. Jobs are defined in
  [`prometheus/prometheus.yml`](../prometheus/prometheus.yml).
- **Exporters** turn infrastructure into metrics: `docker-stats` (per-container CPU/mem/net by
  name — a small in-repo exporter reading the Docker API, because cAdvisor can't register
  containers on this host's `overlayfs` storage driver), `node-exporter`
  (host), `postgres-exporter` (gunvest-db), `nats-exporter` (legion-nats :8222),
  `blackbox-exporter` (synthetic HTTP + TLS probes of the public hostnames), and cloudflared's
  own `:2000/metrics`.
- **Apps** expose `/metrics` directly (via `prom-client`/`fastify-metrics`). The standard
  histogram is `http_request_duration_seconds{service,method,route,status_code}` — that single
  metric yields **R**ate, **E**rrors, and **D**uration (the RED method).
- Retention: 15d / 6 GB (disk-bounded).

### Logs (push via collector)
- Apps log **structured JSON** to stdout (pino, or winston-JSON for gunvest), including
  `level` and — when a request is traced — `trace_id`/`span_id` (injected by OTel).
- **Alloy** discovers every container from the docker socket, tails stdout/stderr, parses the
  JSON, promotes `level` to a label and `trace_id` to structured metadata, and pushes to Loki.
  Config: [`alloy/config.alloy`](../alloy/config.alloy).
- Result: in Grafana Explore you filter `{service="legion"} | json | level="error"`, and click
  a log line's `trace_id` to jump straight to its trace.
- Retention: 14d.

### Traces (push via OTLP)
- Apps run the **OpenTelemetry SDK** with auto-instrumentation (HTTP, Express/Fastify, `pg`).
  Spans are exported over OTLP to `oculory-alloy:4317`.
- Alloy **tail-samples** (keeps all errors + slow traces, samples the rest) and forwards to
  Tempo. Tempo's metrics-generator also emits span/service-graph metrics back to Prometheus,
  powering the service map and exemplars.
- **Legion is distributed**: each worker is its own process, communicating over NATS. The bus
  (`src/bus/nats.js`) injects the W3C `traceparent` into NATS message headers on publish and
  extracts it on consume, so an entire evaluation cycle (scheduler → agents → Ollama → emitter)
  stitches into **one trace**.
- Retention: 7d.

### Correlation (the payoff)
Exemplars on RED histograms link a latency spike → the exact slow trace; a trace span links to
its logs by `trace_id`; a trace links to RED metrics by `service.name`. Configured in
[`grafana/provisioning/datasources/datasources.yml`](../grafana/provisioning/datasources/datasources.yml).

## 4. Networking on the shared VM

Oculory attaches to the existing shared Docker networks so it can reach every container by name
without publishing extra host ports:

| Network | Real name | Reaches |
|---|---|---|
| `gunvest` | `docker_default` | `gunvest-app:3001`, `gunvest-db:5432` |
| `legion` | `app_legion` | `legion-nats:8222`, `legion-*:9100` |
| `tunnel-gateway` | `tunnel-gateway` | `horizon-app:8080`, `global-tunnel:2000`, serves Grafana to the tunnel |
| `oculory` | `oculory_oculory` | internal platform-to-platform traffic |

`oculory-alloy` sits on all of them, so any app on any stack can push OTLP to it.

## 5. Dashboards & alerts (as code)
- Dashboards are JSON in [`grafana/dashboards/`](../grafana/dashboards/), auto-loaded by the
  provider in `grafana/provisioning/dashboards/`. They live in the **Oculory** folder and are
  read-only in the UI — edit the JSON in the repo, not the UI.
- Alert rules: Prometheus rules in [`prometheus/rules/`](../prometheus/rules/) and Loki
  log-rules in [`loki/rules/`](../loki/rules/). Routing + receivers (Telegram/email) in
  [`alertmanager/alertmanager.tmpl.yml`](../alertmanager/alertmanager.tmpl.yml), rendered from
  secrets at deploy.

## 6. Deployment (CI/CD)
- Oculory is its own GitHub repo (`givewgun/oculory`). Push to `main` runs
  [`.github/workflows/ci.yml`](../.github/workflows/ci.yml): **verify** (renders the
  alertmanager template, `docker compose config`, `promtool check rules`) then **deploy** via
  `appleboy/ssh-action` — `git reset --hard origin/main` into `/opt/oculory`, regenerate `.env`
  from GitHub secrets, render `alertmanager.yml`, `docker compose up -d`.
- Secrets (in the repo): `ORACLE_VM_HOST`, `ORACLE_VM_SSH_KEY`, `GRAFANA_ADMIN_PW`,
  `PG_EXPORTER_DSN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_*`, `ALERT_EMAIL_TO`.
- The three app stacks (gunvest/legion/horizon) deploy via **their own** pipelines on merge to
  their default branch; Oculory only watches/scrapes them.

> ⚠️ Note: gunvest's CI deploys only `docker/docker-compose.prod.yml`, not `gateway/`. Changes
> to the cloudflared tunnel (e.g. `--metrics`) require a manual
> `docker compose -f gateway/docker-compose.yml up -d` on the VM until that's folded in.
