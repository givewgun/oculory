#!/usr/bin/env python3
"""Oculory docker-stats exporter.

cAdvisor cannot register containers on hosts using Docker's newer `overlayfs`
storage driver (it fails to resolve the RW layer), so per-container metrics come
from here instead: a tiny pure-stdlib exporter that talks to the Docker Engine
API over /var/run/docker.sock and exposes Prometheus metrics WITH container names.

Metrics (label: name):
  docker_container_up{name}                     1 running / 0 otherwise
  docker_container_cpu_percent{name}            CPU % (sum across cores)
  docker_container_memory_usage_bytes{name}     working-set memory
  docker_container_memory_limit_bytes{name}     mem limit (0 = unlimited)
  docker_container_network_rx_bytes{name}       cumulative RX
  docker_container_network_tx_bytes{name}       cumulative TX
  docker_container_restarts{name}               restart count
  docker_container_oom_killed{name}             1 if last state was OOM-killed
  docker_stats_scrape_errors_total              exporter-side errors
"""
import http.client
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOCKER_SOCK = "/var/run/docker.sock"
LISTEN_PORT = 9101


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, sock_path, timeout=10):
        super().__init__("localhost", timeout=timeout)
        self._sock_path = sock_path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._sock_path)
        self.sock = s


def _docker_get(path):
    conn = _UnixHTTPConnection(DOCKER_SOCK)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"docker GET {path} -> {resp.status}")
        return json.loads(body)
    finally:
        conn.close()


def _cpu_percent(stats):
    try:
        c = stats["cpu_stats"]
        p = stats["precpu_stats"]
        cpu_delta = c["cpu_usage"]["total_usage"] - p["cpu_usage"]["total_usage"]
        sys_delta = c.get("system_cpu_usage", 0) - p.get("system_cpu_usage", 0)
        ncpu = c.get("online_cpus") or len(c["cpu_usage"].get("percpu_usage") or []) or 1
        if sys_delta > 0 and cpu_delta >= 0:
            return (cpu_delta / sys_delta) * ncpu * 100.0
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return 0.0


def _mem(stats):
    m = stats.get("memory_stats", {}) or {}
    usage = m.get("usage", 0) or 0
    # match cAdvisor "working set": usage minus inactive_file
    inactive = (m.get("stats", {}) or {}).get("inactive_file", 0) or 0
    ws = max(usage - inactive, 0)
    return ws, (m.get("limit", 0) or 0)


def _net(stats):
    rx = tx = 0
    for _iface, n in (stats.get("networks", {}) or {}).items():
        rx += n.get("rx_bytes", 0) or 0
        tx += n.get("tx_bytes", 0) or 0
    return rx, tx


def _collect_container(c):
    """Returns (lines, errors) for one container. Runs in a worker thread."""
    out = []
    errors = 0
    name = (c.get("Names") or ["/unknown"])[0].lstrip("/")
    lbl = f'{{name="{name}"}}'
    running = c.get("State") == "running"
    out.append(f"docker_container_up{lbl} {1 if running else 0}")
    cid = c.get("Id")
    try:
        ins = _docker_get(f"/containers/{cid}/json")
        state = ins.get("State", {}) or {}
        out.append(f"docker_container_restarts{lbl} {ins.get('RestartCount', 0)}")
        out.append(f"docker_container_oom_killed{lbl} {1 if state.get('OOMKilled') else 0}")
    except Exception:
        errors += 1
    if running:
        try:
            st = _docker_get(f"/containers/{cid}/stats?stream=false")
            ws, lim = _mem(st)
            rx, tx = _net(st)
            out.append(f"docker_container_cpu_percent{lbl} {_cpu_percent(st):.3f}")
            out.append(f"docker_container_memory_usage_bytes{lbl} {ws}")
            out.append(f"docker_container_memory_limit_bytes{lbl} {lim}")
            out.append(f"docker_container_network_rx_bytes{lbl} {rx}")
            out.append(f"docker_container_network_tx_bytes{lbl} {tx}")
        except Exception:
            errors += 1
    return out, errors


def collect():
    lines = []
    errors = 0

    def metric(name, help_, typ):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {typ}")

    metric("docker_container_up", "1 if the container is running", "gauge")
    metric("docker_container_cpu_percent", "CPU percent (sum across cores)", "gauge")
    metric("docker_container_memory_usage_bytes", "Working-set memory", "gauge")
    metric("docker_container_memory_limit_bytes", "Memory limit (0=unlimited)", "gauge")
    metric("docker_container_network_rx_bytes", "Cumulative network RX", "counter")
    metric("docker_container_network_tx_bytes", "Cumulative network TX", "counter")
    metric("docker_container_restarts", "Restart count", "gauge")
    metric("docker_container_oom_killed", "1 if last state was OOM-killed", "gauge")

    try:
        containers = _docker_get("/containers/json?all=1")
    except Exception:
        containers = []
        errors += 1

    if containers:
        with ThreadPoolExecutor(max_workers=min(32, len(containers))) as ex:
            for out, errs in ex.map(_collect_container, containers):
                lines.extend(out)
                errors += errs

    lines.append("# HELP docker_stats_scrape_errors_total Exporter-side errors during scrape")
    lines.append("# TYPE docker_stats_scrape_errors_total counter")
    lines.append(f"docker_stats_scrape_errors_total {errors}")
    return ("\n".join(lines) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        try:
            body = collect()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:  # never crash the server
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, *_args):
        pass  # quiet


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"[docker-stats] exposing :{LISTEN_PORT}/metrics", flush=True)
    srv.serve_forever()
