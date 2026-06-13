# Oculory Metrics Catalog

Source of truth for every metric Oculory collects, grouped by **audience**. Each app must
expose the names below at `/metrics` (Prometheus text format); exporters provide the rest.

Conventions: durations in **seconds** (histograms, `_bucket/_sum/_count`), counters end in
`_total`, gauges describe current state. Every app series carries a `service` label
(`gunvest` / `legion` / `horizon`); legion also carries `legion_role`.

---

## A. NOC / Platform — availability + USE

| Metric | Type | Source | Notes |
|---|---|---|---|
| `up` | gauge | Prometheus | 1 = target scrapable |
| `container_last_seen` | gauge | cAdvisor | down detection (`time()-x>120`) |
| `container_cpu_usage_seconds_total` | counter | cAdvisor | per-container CPU |
| `container_memory_working_set_bytes` / `container_spec_memory_limit_bytes` | gauge | cAdvisor | mem vs limit (Ollama 12g) |
| `container_oom_events_total` | counter | cAdvisor | OOM kills |
| `container_start_time_seconds` | gauge | cAdvisor | restarts via `changes()` |
| `node_cpu_seconds_total`, `node_load1/5/15` | counter/gauge | node-exporter | host CPU + saturation |
| `node_memory_MemTotal/MemAvailable/Swap*` | gauge | node-exporter | host memory |
| `node_filesystem_size/avail_bytes` | gauge | node-exporter | disk (the binding constraint) |
| `node_disk_*_bytes_total` | counter | node-exporter | disk IO |
| `node_network_*_bytes_total` | counter | node-exporter | host network |
| `probe_success`, `probe_http_duration_seconds`, `probe_ssl_earliest_cert_expiry` | gauge | blackbox | synthetic up + latency + cert expiry |
| `cloudflared_tunnel_ha_connections`, `cloudflared_tunnel_total_requests` | gauge/counter | cloudflared `:2000` | tunnel health |
| `pg_up`, `pg_stat_activity_count`, `pg_settings_max_connections` | gauge | postgres-exporter | DB connections |
| `pg_stat_database_blks_hit/blks_read` | counter | postgres-exporter | cache hit ratio |
| `pg_stat_database_xact_commit/rollback/deadlocks` | counter | postgres-exporter | txn health |
| `nats_varz_in_msgs/out_msgs/mem/connections` | counter/gauge | nats-exporter | NATS server |
| `nats_consumer_num_pending/num_redelivered` | gauge | nats-exporter | JetStream consumer lag |

## B. Application — golden signals + domain

Shared across all 3 apps (emitted by the instrumentation layer):

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `http_request_duration_seconds` | histogram | `service,method,route,status_code` | **RED** — rate (`_count`), errors (`status_code=~"5.."`), duration (`_bucket`) |
| `nodejs_eventloop_lag_p99_seconds` | gauge | `service` | event-loop saturation |
| `nodejs_heap_size_used_bytes`, `nodejs_gc_duration_seconds` | gauge/histogram | `service` | heap + GC |
| `process_cpu_seconds_total`, `process_resident_memory_bytes` | counter/gauge | `service` | process USE (prom-client default) |

### gunvest-specific
| Metric | Type | Labels |
|---|---|---|
| `gunvest_upstream_requests_total` | counter | `upstream,outcome` (Yahoo/CoinGecko/Finnhub/StockTwits/Reddit/GDELT/YouTube/Gemini) |
| `gunvest_upstream_request_duration_seconds` | histogram | `upstream` |
| `gunvest_job_runs_total` | counter | `job_name,outcome` |
| `gunvest_job_duration_seconds` | histogram | `job_name` |
| `gunvest_job_last_success_timestamp_seconds` | gauge | `job_name` (staleness) |
| `pg_pool_total/idle/waiting` | gauge | — (node-postgres pool) |

### legion-specific (pipeline)
| Metric | Type | Labels |
|---|---|---|
| `legion_cycles_total` | counter | `status` = started/completed/failed |
| `legion_cycle_duration_seconds` | histogram | — |
| `legion_votes_total` | counter | `agent` |
| `legion_agent_inference_seconds` | histogram | `agent` |
| `legion_ollama_request_seconds` | histogram | — |
| `legion_consensus_rounds` | histogram | — |
| `legion_signals_total` | counter | `stance` |
| `legion_telegram_delivery_total` | counter | `outcome` |

### horizon-specific
| Metric | Type | Labels |
|---|---|---|
| `horizon_panel_last_update_timestamp_seconds` | gauge | `panel` (freshness) |
| `horizon_upstream_requests_total` / `_request_duration_seconds` | counter/histogram | `upstream` |
| `horizon_sqlite_queries_total` | counter | `op` |

## C. Financial / Business — nice-to-have, gated

Only emitted when `ENABLE_BUSINESS_METRICS=true`; read from the DB; visible **only** behind
Cloudflare Access (never on a public/raw scrape).

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `gunvest_portfolio_value_usd` | gauge | — | total portfolio value |
| `gunvest_pnl_realized_usd` / `gunvest_pnl_unrealized_usd` | gauge | — | P&L |
| `gunvest_position_count` | gauge | — | open positions |
| `gunvest_trades_total` | counter | `side` | trade activity |
| `gunvest_trigger_events_total` | counter | `type` = trim/stop | guardrail fires |
| `gunvest_risk_environment_level` | gauge | — | 0=LOW…3=EXTREME |
| `legion_signal_confidence` | histogram | `stance` | consensus confidence |
| `legion_agent_reliability` | gauge | `agent` | reliability ρ |
| `legion_backtest_hit_rate` | gauge | — | rolling hit-rate |

---

## Pillars 2 & 3

- **Logs:** every app logs JSON via pino with at least `{level, service, msg, trace_id, span_id}`; Alloy lifts `level` (label) and `trace_id` (structured metadata) into Loki.
- **Traces:** OTLP from each app → Alloy → Tempo. `service.name` resource attr = `gunvest`/`legion`/`horizon`. Legion propagates W3C `traceparent` in NATS message headers so one cycle is one trace. Tempo's metrics-generator emits span/service-graph metrics back to Prometheus for the service map and exemplar links.
