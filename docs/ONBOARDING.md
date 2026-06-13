# Onboarding a New Service into Oculory

This guide adds full observability (metrics + logs + traces + alerts + dashboard) to a new
service or project on the VM. Read [ARCHITECTURE.md](./ARCHITECTURE.md) first for the why.

**Mental model:** Oculory *pulls* metrics (your app exposes `/metrics`), *tails* logs
automatically (just log JSON to stdout), and *receives* traces over OTLP (your app pushes to
`oculory-alloy:4317`). You wire three small things in the app, then one scrape target + one
dashboard in this repo.

---

## TL;DR checklist

- [ ] App exposes `GET /metrics` with the standard `http_request_duration_seconds` histogram (RED)
- [ ] App logs **structured JSON** to stdout (gets a `trace_id` when traced)
- [ ] App sends OTLP traces to `http://oculory-alloy:4317` (`OTEL_SERVICE_NAME` set)
- [ ] App container shares a Docker network with `oculory-alloy` / Prometheus
- [ ] Add a scrape job in `prometheus/prometheus.yml`
- [ ] (Optional) add a dashboard JSON + alert rules
- [ ] Open a PR to `givewgun/oculory` → merge → auto-deploy

---

## Step 1 — Instrument the app

### 1a. Metrics — `/metrics` (Node example)

Install `prom-client` (Express) or `fastify-metrics` (Fastify). Use the **exact** metric name
and labels below so it lights up the shared dashboards and alert rules automatically.

**Express:**
```js
const client = require('prom-client');
const SERVICE = process.env.OTEL_SERVICE_NAME || 'myservice';
client.register.setDefaultLabels({ service: SERVICE });
client.collectDefaultMetrics();                       // process CPU/mem, event-loop lag, GC

const httpDuration = new client.Histogram({
  name: 'http_request_duration_seconds',              // <-- standard name (RED)
  help: 'HTTP request duration in seconds',
  labelNames: ['service', 'method', 'route', 'status_code'],
  buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
});

app.use((req, res, next) => {
  if (req.path === '/metrics') return next();
  const end = httpDuration.startTimer();
  res.on('finish', () => {
    const route = req.route ? (req.baseUrl || '') + req.route.path : 'other';  // bound cardinality!
    end({ service: SERVICE, method: req.method, route, status_code: res.statusCode });
  });
  next();
});

app.get('/metrics', async (_req, res) => {
  res.set('Content-Type', client.register.contentType);
  res.end(await client.register.metrics());
});
```

> **Cardinality rule:** label `route` with the *templated* path (`/users/:id`), never the raw
> URL — otherwise every id becomes a new series and Prometheus drowns.

**Other languages:** use the official Prometheus client (Python `prometheus_client`, Go
`client_golang`, etc.). Emit the same `http_request_duration_seconds` histogram with a `service`
label and you inherit the RED dashboards.

Add **domain metrics** for what matters to your service (counters/gauges/histograms) and
document them in [METRICS.md](./METRICS.md).

### 1b. Traces — OpenTelemetry → OTLP

**CommonJS (require first, before any other import):**
```js
const { NodeSDK } = require('@opentelemetry/sdk-node');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-grpc');
if (process.env.NODE_ENV !== 'test') {                // skip in CI
  const sdk = new NodeSDK({
    traceExporter: new OTLPTraceExporter({
      url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://oculory-alloy:4317',
    }),
    instrumentations: [getNodeAutoInstrumentations({ '@opentelemetry/instrumentation-fs': { enabled: false } })],
  });
  try { sdk.start(); } catch (e) { console.error('otel off:', e.message); }
}
```

**ESM:** the SDK must start *before* app modules load. Launch with
`node --import ./path/to/tracing.mjs server.js` (see legion's `otel.mjs` / horizon's Dockerfile
CMD). A top-of-file `import './tracing.js'` only works if it is the very first import.

**Cross-process propagation (message buses):** if your service hands work to another over a
queue/bus, inject the W3C `traceparent` into the message headers on publish and extract it on
consume (see `legion/src/bus/nats.js`) so the whole flow is one trace.

### 1c. Logs — structured JSON to stdout

Nothing to install on the Oculory side — Alloy already tails every container. Just make your
app log **JSON** to stdout with at least `level` and `msg`. With OTel running, add `trace_id`
so logs link to traces. `pino` does this out of the box; for winston use `format.json()`.
Avoid pretty/colorized console output in production (Alloy can't parse it as JSON).

## Step 2 — Wire env + networking (the app's compose)

Add to the app service's `environment:` (committed in the app repo, not the VM `.env`):
```yaml
environment:
  OTEL_SERVICE_NAME: myservice
  OTEL_EXPORTER_OTLP_ENDPOINT: http://oculory-alloy:4317
```

Ensure the container shares a network with `oculory-alloy` and Prometheus. Oculory is on
`docker_default`, `app_legion`, and `tunnel-gateway`. If your service is on one of those, you're
set. If it's a brand-new stack with its own network, either:
- attach it to `tunnel-gateway` (already shared), **or**
- add your network as `external` to Oculory's `docker-compose.yml` `networks:` block and to
  `prometheus` + `alloy` `networks:` lists, then redeploy Oculory.

Expose the metrics port to the network with `expose: ["<port>"]` (no host publish needed).

> **Many processes / one service?** (like legion) Each process is its own container with its own
> registry — run a tiny metrics server per process on a fixed port (e.g. `:9100`) and scrape
> each container. See legion's `src/instrumentation/`.

## Step 3 — Add the Prometheus scrape target

In [`prometheus/prometheus.yml`](../prometheus/prometheus.yml):
```yaml
  - job_name: myservice
    metrics_path: /metrics
    static_configs:
      - targets: ['myservice-container:PORT']
        labels: { service: myservice }
```
Then `curl -X POST http://localhost:9090/-/reload` (or just redeploy). Confirm **UP** at
`http://localhost:9090/targets` (SSH `-L 9090:localhost:9090`).

## Step 4 — Dashboard (optional but recommended)

Copy an existing per-service board (e.g. `grafana/dashboards/30-horizon.json`) to
`grafana/dashboards/NN-myservice.json`, change the `uid`, `title`, and `service="myservice"`
filters. Because RED uses the shared metric name, the rate/error/latency panels work with only
the filter changed. Drop in panels for your domain metrics + a logs panel
(`{service="myservice"} | json`).

## Step 5 — Alerts (optional)

The shared rules in `prometheus/rules/platform.yml` already cover **any** service with the
standard RED metric (HighErrorRate, HighLatencyP99) and any container (ContainerDown, OOM, etc.)
— so a compliant new service is alerted on automatically. Add service-specific rules there if
needed; Loki log-rate alerts go in `loki/rules/fake/log-alerts.yml`.

## Step 6 — Ship it

1. App-side changes → PR to the app's repo → merge → its pipeline deploys (exposes `/metrics`,
   pushes traces, logs JSON).
2. Oculory-side changes (scrape job, dashboard, rules) → PR to `givewgun/oculory` → merge →
   CI auto-deploys.

## Verify
- `http://localhost:9090/targets` → your job **UP**.
- `curl http://myservice-container:PORT/metrics` (from another container) → histograms present.
- Grafana Explore → Loki → `{service="myservice"}` → JSON logs with `trace_id`.
- Generate traffic → Tempo shows traces; RED panels move; a latency exemplar opens its trace.

## Common pitfalls
- **`/metrics` 404 / SPA catch-all** — register the metrics route *before* any wildcard/static
  handler.
- **Exploding cardinality** — templated `route` label only; never raw paths or ids.
- **No traces in ESM** — you imported the SDK too late; use `node --import`.
- **Logs not parsed** — output is pretty/colorized, not JSON. Switch prod logging to JSON.
- **Target DOWN** — the app isn't on a network Prometheus is attached to, or `expose:` is missing.
- **`/metrics` is public** — it must stay internal; never route it through the Cloudflare tunnel.
