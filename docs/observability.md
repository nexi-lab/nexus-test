# Observability

## Architecture

### Endpoint Overview

All observability endpoints are **rate-limit exempt** and most require no
authentication.

| Endpoint              | Auth Required | Purpose                            |
|-----------------------|---------------|------------------------------------|
| `GET /health`         | No            | Basic health status                |
| `GET /health/detailed`| No            | Per-component status               |
| `GET /healthz/live`   | No            | K8s liveness probe                 |
| `GET /healthz/ready`  | No            | K8s readiness probe                |
| `GET /healthz/startup`| No            | K8s startup probe                  |
| `GET /api/v2/features`| No            | Server profile and enabled bricks  |
| `GET /api/v2/operations`| Yes         | Recent operation log               |
| `GET /metrics`        | No            | Prometheus text exposition         |
| `GET /metrics/pool`   | No            | DB connection pool stats           |

---

## Health Endpoints

### GET /health

Returns basic server health. Always 200 unless Raft topology is not ready (503).

```json
{
    "status": "healthy",
    "service": "nexus-rpc",
    "enforce_permissions": true,
    "enforce_zone_isolation": true,
    "has_auth": true
}
```

During federation startup (Raft leader not yet elected):

```json
{"status": "starting", "service": "nexus-rpc", "detail": "Waiting for Raft leader election..."}
```

HTTP 503.

### GET /health/detailed

Returns per-component breakdown. Status is `"healthy"` or `"degraded"` (if any
backend is unhealthy).

```json
{
    "status": "healthy",
    "service": "nexus-rpc",
    "components": {
        "search_daemon": {"status": "disabled"},
        "rebac": {
            "status": "healthy",
            "circuit_state": "closed",
            "failure_count": 0,
            "open_count": 0,
            "last_failure_time": null
        },
        "subscriptions": {"status": "healthy"},
        "websocket": {
            "status": "healthy",
            "current_connections": 0,
            "total_connections": 0,
            "total_messages_sent": 0,
            "connections_by_zone": {}
        },
        "reactive_subscriptions": {"status": "disabled"},
        "backends": {
            "/mnt/s3": {"backend": "s3", "healthy": true, "latency_ms": 12.5}
        },
        "resiliency": {"status": "ok"}
    }
}
```

If any backend is unhealthy: `"status": "degraded"` with
`"unhealthy_backends": ["/mnt/s3"]`.

---

## Kubernetes Probes

All three probes are **zero-I/O, in-memory** checks. They fail open on
unexpected exceptions (return 200 rather than crashing the pod).

### GET /healthz/live

Liveness probe -- verifies the event loop is alive. Should never fail.

```json
{"status": "alive"}
```

### GET /healthz/ready

Readiness probe -- verifies the server can serve traffic. Requires 4 startup
phases to be complete: `observability`, `features`, `permissions`, `services`.

**200 (ready):**
```json
{"status": "ready", "uptime_seconds": 12.3}
```

**503 (not ready):**
```json
{
    "status": "not_ready",
    "reason": "startup_incomplete",
    "pending_phases": ["services"]
}
```

### GET /healthz/startup

Startup probe -- verifies all lifespan phases are complete. Covers all 10
phases: `observability`, `features`, `permissions`, `realtime`, `search`,
`services`, `bricks`, `uploads`, `ipc`, `a2a_grpc`.

**200:**
```json
{"status": "started"}
```

**503:**
```json
{
    "status": "starting",
    "completed_phases": ["observability", "features"],
    "pending_phases": ["permissions", "realtime", "search", "services", "bricks", "uploads", "ipc", "a2a_grpc"]
}
```

---

## Features Endpoint

### GET /api/v2/features

Public, rate-limit exempt. Computed once at startup and cached in `app.state`.

```json
{
    "profile": "embedded",
    "mode": "standalone",
    "enabled_bricks": ["auth", "kernel", "rebac", "hooks"],
    "disabled_bricks": ["search", "mcp", "sandbox"],
    "version": "0.42.0",
    "performance_tuning": {
        "thread_pool_size": 4,
        "default_workers": 2,
        "task_runner_workers": 4,
        "default_http_timeout": 30,
        "db_pool_size": 10,
        "search_max_concurrency": 4,
        "heartbeat_flush_interval": 5.0,
        "default_max_retries": 3,
        "blob_operation_timeout": 60,
        "asyncpg_max_size": 20
    }
}
```

| Field                | Type          | Values                                      |
|----------------------|---------------|---------------------------------------------|
| `profile`            | `str`         | `"embedded"`, `"lite"`, `"full"`, `"cloud"` |
| `mode`               | `str`         | `"standalone"`, `"remote"`, `"federation"`  |
| `enabled_bricks`     | `list[str]`   | Active server modules                       |
| `disabled_bricks`    | `list[str]`   | Inactive server modules                     |
| `version`            | `str | null`  | Server version                              |
| `performance_tuning` | `object|null` | Runtime tuning parameters                   |

Profile is set via `NEXUS_PROFILE` env var.

---

## Operations Endpoint

### GET /api/v2/operations

Returns the recent operation log. **Requires authentication.**

**Known bug**: Currently returns 500 because `get_operation_logger` accesses
`nexus_fs.record_store` (public) but `NexusFS` stores it as `_record_store`
(private). See [nexi-lab/nexus#2589](https://github.com/nexi-lab/nexus/issues/2589).

The `OperationLogModel` table and `OperationLogger` class are fully implemented;
only the FastAPI dependency wiring is broken.

---

## Prometheus Metrics

### GET /metrics

Returns metrics in Prometheus text exposition format (`text/plain; version=0.0.4`).

### Exposed Metrics

| Metric                            | Type      | Labels                              | Description            |
|-----------------------------------|-----------|-------------------------------------|------------------------|
| `http_request_duration_seconds`   | Histogram | `method`, `status`, `endpoint`      | Request duration       |
| `http_requests_total`             | Counter   | `method`, `status`, `endpoint`      | Total requests         |
| `http_requests_in_progress`       | Gauge     | `method`                            | In-flight requests     |
| `nexus_info`                      | Info      | `version`                           | Server version         |

**Histogram buckets**: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0 seconds.

**Status label**: Low-cardinality groups: `2xx`, `3xx`, `4xx`, `5xx`.

**Excluded paths** (not recorded in metrics): `/health`, `/metrics`, `/favicon.ico`.

### Example Output

```
# HELP http_request_duration_seconds HTTP request duration in seconds
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{endpoint="/api/nfs/read",method="POST",status="2xx",le="0.005"} 12.0
http_request_duration_seconds_bucket{endpoint="/api/nfs/read",method="POST",status="2xx",le="0.01"} 45.0
...
http_request_duration_seconds_count{endpoint="/api/nfs/read",method="POST",status="2xx"} 128.0
http_request_duration_seconds_sum{endpoint="/api/nfs/read",method="POST",status="2xx"} 3.456

# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{endpoint="/api/nfs/read",method="POST",status="2xx"} 128.0

# HELP http_requests_in_progress Currently in-flight HTTP requests
# TYPE http_requests_in_progress gauge
http_requests_in_progress{method="POST"} 2.0

# HELP nexus_info Nexus server info
# TYPE nexus_info gauge
nexus_info{version="0.42.0"} 1.0
```

### GET /metrics/pool

Returns database connection pool statistics:

```json
{
    "postgres": {"size": 10, "idle": 8, "active": 2},
    "dragonfly": {"status": "not_configured"}
}
```

---

## Common Response Headers

All responses include a correlation ID from `CorrelationMiddleware`:

```
X-Request-ID: <uuid-hex>
```

Propagated from the request header if present, otherwise freshly generated.

---

## Test Setup

### Server Startup

```bash
./scripts/serve-for-tests.sh
```

Or:

```bash
NEXUS_ENFORCE_PERMISSIONS=true nexus serve
```

Metrics and health endpoints are always available; rate limiting requires
`NEXUS_RATE_LIMIT_ENABLED=true`.

---

## Fixture Architecture

### Scoping

```
Module-scoped (from tests/obs/conftest.py)
  server_features       Cached /api/v2/features response

Session-scoped (from root conftest)
  nexus                 Admin NexusClient
```

### server_features

Caches the features response once per test module to avoid repeated requests:

```python
@pytest.fixture(scope="module")
def server_features(nexus):
    resp = nexus.features()
    return resp.json() if resp.status_code == 200 else {}
```

---

## Test Classes

### TestHealth (obs/001-002)

- `test_health_returns_status`: Assert `/health` returns `status` in
  (`"healthy"`, `"ok"`)
- `test_health_detailed_returns_components`: Assert `/health/detailed` has
  `components` dict with at least one entry, each with a `status` field
- Markers: `quick`, `auto`, `observability`

### TestKubernetesProbes (obs/003-005)

- `test_liveness_probe`: Assert `/healthz/live` returns 200 with `status`
- `test_readiness_probe`: Assert `/healthz/ready` returns 200 or explains 503
  with `pending_phases`
- `test_startup_probe`: Assert `/healthz/startup` returns 200 with `status`
- Markers: `auto`, `observability`

### TestFeatures (obs/006)

- `test_features_returns_profile`: Assert `profile` is one of
  `embedded/lite/full/cloud`, `enabled_bricks` is a list
- Markers: `auto`, `observability`

### TestOperations (obs/007)

- `test_operations_returns_recent_ops`: Write a file, call `/api/v2/operations`,
  verify at least one entry. Skips on 404/500/501.
- Markers: `auto`, `observability`, `audit`

### TestMetrics (obs/008-010)

All three tests scrape `/metrics` once and parse with `parse_prometheus_metric()`.
They skip gracefully if the metric name is not found (tries alternative names).

- `test_latency_histogram_populated`: Assert `http_request_duration_seconds` is
  a histogram with `_count > 0`
- `test_error_rate_counter_exists`: Assert `http_requests_total` is a counter
- `test_saturation_gauge_exists`: Assert `http_requests_in_progress` is a gauge
  with value >= 0
- Markers: `auto`, `observability`, `slo`

### Metrics Parsing Helper

`parse_prometheus_metric(text, metric_name)` in `tests/helpers/assertions.py`:

```python
result = parse_prometheus_metric(metrics_text, "http_requests_total")
# {"type": "counter", "value": 128.0}  or  None if not found
```

Regex anchors on `# TYPE` line for type detection and `^metric_name{labels} value`
for value extraction. The value pattern avoids false-positive suffix matches
(`_bucket`, `_sum`, `_count`) by anchoring with optional `{labels}` block.
