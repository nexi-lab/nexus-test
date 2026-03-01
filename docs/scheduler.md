# Scheduler (Astraea)

## Architecture

### Hybrid Priority Scheduling Model

Nexus implements a 4-layer priority scheduling system called **Astraea** (Issue #1274),
located at `system_services/scheduler/`. It supports both PostgreSQL-backed persistent
queues and an in-memory fallback for edge/lite deployments.

```text
  Client (NexusClient)
      |
      | POST /api/v2/scheduler/submit
      | Body: {executor, task_type, payload, priority, request_state, ...}
      v
  +------------------------------------------------------------+
  | SchedulerService (service.py)                              |
  |                                                            |
  |  1. Validate submission                                    |
  |  2. Classify priority (Astraea classifier)                 |
  |     CRITICAL/HIGH → INTERACTIVE                            |
  |     NORMAL        → BATCH                                  |
  |     LOW/BEST_EFFORT → BACKGROUND                           |
  |  3. Fair-share admission check (per-agent concurrency)     |
  |  4. Compute effective_tier (base - boost - aging)          |
  |  5. Enqueue to PostgreSQL (TaskQueue)                      |
  |                                                            |
  |  Dequeue Strategy:                                         |
  |    HRRN mode (default):                                    |
  |      ORDER BY priority_class ASC, HRRN_score DESC          |
  |      HRRN = (wait + est_service_time) / est_service_time   |
  |    Classic mode:                                           |
  |      ORDER BY effective_tier ASC, enqueued_at ASC          |
  |    Both use: FOR UPDATE SKIP LOCKED                        |
  |                                                            |
  |  +------------------------------------------------------+  |
  |  | TaskQueue (queue.py) — PostgreSQL                     |  |
  |  | 12+ raw SQL statements (all parameterized)            |  |
  |  | pg_notify('task_enqueued') on insert                  |  |
  |  +------------------------------------------------------+  |
  |                                                            |
  |  +------------------------------------------------------+  |
  |  | Background Loops (dispatcher.py)                      |  |
  |  | _dispatch_loop: dequeue + execute (tight loop)        |  |
  |  | _aging_loop: recalc effective_tier every 60s          |  |
  |  | _starvation_loop: promote BACKGROUND→BATCH if > 15m  |  |
  |  | _listen_loop: PostgreSQL LISTEN/NOTIFY (optional)     |  |
  |  +------------------------------------------------------+  |
  +------------------------------------------------------------+
```

### Priority Layers (4-layer model)

| Layer | Name           | Description                                      |
|-------|----------------|--------------------------------------------------|
| 1     | Base Tier      | From submission: CRITICAL(0), HIGH(1), NORMAL(2), LOW(3), BEST_EFFORT(4) |
| 2     | Price Boost    | Credits: `min(boost / 0.01, 2)` tiers up        |
| 3     | Aging          | +1 tier per 120s waited                          |
| 4     | Max-Wait       | If waited > 600s, escalate to HIGH               |

Formula: `effective_tier = max(0, base - boost - aging)` with max-wait cap.

### Astraea Classification

| Request State | PriorityTier → PriorityClass mapping               |
|---------------|-----------------------------------------------------|
| Any           | CRITICAL/HIGH → INTERACTIVE                         |
| Any           | NORMAL → BATCH                                      |
| IO_WAIT       | BACKGROUND promoted → BATCH (I/O promotion)         |
| Any           | LOW/BEST_EFFORT → BACKGROUND                        |
| Cost exceeded | INTERACTIVE demoted → BATCH (cost demotion)         |

### HRRN Scoring

```
HRRN_score = (wait_seconds + estimated_service_time) / estimated_service_time
```

Short jobs complete quickly (low score). Long-waiting jobs gain priority.
Default `estimated_service_time = 30.0` seconds.

### Fair-Share Admission

Per-agent concurrency control:
- Default: 10 concurrent tasks per agent
- LRU cache bounded to 4096 agents
- `admit()` checks without incrementing
- `record_start()` / `record_complete()` track running tasks
- `sync_from_db()` restores state on startup

### Dual Backend: PostgreSQL vs InMemory

| Feature              | SchedulerService (PostgreSQL) | InMemoryScheduler (Fallback) |
|----------------------|-------------------------------|------------------------------|
| Persistence          | Yes (asyncpg pool)            | No (lost on restart)         |
| Two-phase init       | Yes (initialize(pool))        | No (instant)                 |
| HRRN dequeue         | Yes (SQL-level)               | No (heap-based)              |
| Fair-share           | Yes (DB-synced)               | No                           |
| LISTEN/NOTIFY        | Yes (pg_notify)               | No                           |
| Max completed tasks  | Unlimited (DB)                | 10K (LRU eviction)           |
| Use case             | Production                    | Edge/lite profiles            |

---

## REST API Endpoints

| Method | Path                               | Status | Description              |
|--------|-------------------------------------|--------|--------------------------|
| POST   | `/api/v2/scheduler/submit`          | 201    | Submit a task            |
| GET    | `/api/v2/scheduler/task/{task_id}`  | 200    | Get task status          |
| POST   | `/api/v2/scheduler/task/{id}/cancel`| 200    | Cancel a queued task     |
| GET    | `/api/v2/scheduler/metrics`         | 200    | Queue metrics + fair-share|
| POST   | `/api/v2/scheduler/classify`        | 200    | Classify priority        |

### Request/Response Examples

**POST /api/v2/scheduler/submit:**

```json
{
  "executor": "agent-worker-1",
  "task_type": "test_task",
  "payload": {"action": "noop"},
  "priority": "normal",
  "request_state": "pending",
  "estimated_service_time": 30.0,
  "idempotency_key": "unique-key-123"
}
```

Response (201):
```json
{
  "id": "uuid-...",
  "status": "queued",
  "priority_tier": "normal",
  "priority_class": "batch",
  "effective_tier": 2,
  "executor_id": "agent-worker-1",
  "task_type": "test_task",
  "enqueued_at": "2025-03-01T10:00:00Z"
}
```

**POST /api/v2/scheduler/classify:**
```json
{"priority": "critical", "request_state": "compute"}
```
Response: `{"priority_class": "interactive"}`

---

## Test Setup

### Prerequisites

```bash
# Start infrastructure
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d

# Start Nexus server (PostgreSQL mode — full scheduler)
export NEXUS_DATABASE_URL=postgresql://postgres:nexus@localhost:5432/nexus
uv run nexus serve --port 2026

# OR start without PostgreSQL (InMemory fallback)
uv run nexus serve --port 2026
```

The server must have the `scheduler` profile enabled (default: on).

### Running Tests

```bash
pytest tests/scheduler/                           # All scheduler tests
pytest tests/scheduler/ -m quick                  # Smoke tests only
pytest tests/scheduler/ -m auto                   # Full regression
pytest tests/scheduler/ -m perf                   # Performance benchmarks
pytest tests/scheduler/ -k "test_classify"        # Specific test
pytest tests/scheduler/ -v --tb=short             # Verbose
```

### Test Markers

| Marker      | Purpose                                  |
|-------------|------------------------------------------|
| `quick`     | Smoke tests (submit, cancel)             |
| `auto`      | Full regression suite                    |
| `scheduler` | All scheduler tests                      |
| `perf`      | Performance benchmarks with SLO          |
| `stress`    | Concurrent submission load tests         |

---

## Fixture Architecture

### Scoping Strategy

```text
Session-scoped (inherited from conftest.py root)
  settings            TestSettings from env / .env.test
  http_client         httpx.Client (leader, auth, pooling)
  nexus               NexusClient (leader, admin)
  _cluster_ready      Health check gate (autouse)

Module-scoped (tests/scheduler/conftest.py)
  _scheduler_available    Skip module if scheduler not enabled (autouse)
  scheduler_backend       "postgresql" or "in_memory" (detected)

Function-scoped (per test)
  submit_task         Factory with automatic cancel/cleanup
  unique_executor     UUID-based executor name for isolation
```

### submit_task Factory

Core fixture for scheduler tests — creates tasks and cleans up:

```python
@pytest.fixture
def submit_task(nexus, settings) -> Generator[SubmitFn, None, None]:
    created_ids: list[str] = []

    def _submit(executor, task_type, payload, *, priority="normal", **kw):
        resp = nexus.api_post("/api/v2/scheduler/submit", json={
            "executor": executor, "task_type": task_type,
            "payload": payload, "priority": priority, **kw,
        })
        if resp.status_code == 201:
            created_ids.append(resp.json()["id"])
        return resp

    yield _submit

    for tid in reversed(created_ids):
        nexus.api_post(f"/api/v2/scheduler/task/{tid}/cancel")
```

---

## Test Coverage

### Lifecycle (test_scheduler.py — scheduler/001-004)

| ID            | Test                          | Markers      | Operations                              |
|---------------|-------------------------------|--------------|-----------------------------------------|
| scheduler/001 | Submit task                   | quick, auto  | submit, verify ID + status + tier       |
| scheduler/002 | Idempotency key retry         | quick, auto  | submit x2 same key, 200/201/409         |
| scheduler/003 | Priority ordering             | auto         | submit 4 tiers, verify each             |
| scheduler/004 | Task cancellation             | quick, auto  | submit, cancel, verify status           |

### Astraea Classification (test_scheduler_classify.py — scheduler/005-010)

| ID            | Test                          | Markers      | Operations                              |
|---------------|-------------------------------|--------------|-----------------------------------------|
| scheduler/005 | Classify critical→interactive | quick, auto  | classify endpoint                       |
| scheduler/006 | Classify normal→batch         | auto         | classify endpoint                       |
| scheduler/007 | Classify low→background       | auto         | classify endpoint                       |
| scheduler/008 | IO_WAIT promotes background   | auto         | classify(low, io_wait)→batch            |
| scheduler/009 | All request states valid      | auto         | classify with each RequestState         |
| scheduler/010 | Classify invalid rejected     | auto         | classify bad input → 422                |

### Task Lifecycle (test_scheduler_lifecycle.py — scheduler/011-017)

| ID            | Test                          | Markers      | Operations                              |
|---------------|-------------------------------|--------------|-----------------------------------------|
| scheduler/011 | Get status by ID              | auto         | submit, get, verify all fields          |
| scheduler/012 | Get nonexistent → 404         | auto         | get random UUID → 404                   |
| scheduler/013 | Cancel already cancelled      | auto         | cancel, cancel again → idempotent       |
| scheduler/014 | Metrics endpoint              | auto         | metrics, verify queue_by_class shape    |
| scheduler/015 | Metrics after submits         | auto         | submit 3 tiers, check metrics counts    |
| scheduler/016 | Astraea fields in response    | auto         | verify priority_class, request_state    |
| scheduler/017 | Zone isolation                | auto         | submit in zone A, metrics zone B empty  |

### Performance (test_scheduler_perf.py — scheduler/018-020)

| ID            | Test                          | Markers      | SLO                                     |
|---------------|-------------------------------|--------------|-----------------------------------------|
| scheduler/018 | Submit latency p95            | perf         | < 200ms                                 |
| scheduler/019 | Status query latency p95      | perf         | < 100ms                                 |
| scheduler/020 | Classify latency p95          | perf         | < 100ms                                  |

### Stress (test_scheduler_stress.py — scheduler/021-022)

| ID            | Test                          | Markers      | Operations                              |
|---------------|-------------------------------|--------------|-----------------------------------------|
| scheduler/021 | Concurrent submit (20 tasks)  | stress       | ThreadPoolExecutor, all succeed         |
| scheduler/022 | Burst + cancel cycle          | stress       | submit 10 → cancel all → verify clean   |

### Permissions (test_scheduler_permissions.py — scheduler/023-025)

| ID            | Test                          | Markers      | Operations                              |
|---------------|-------------------------------|--------------|-----------------------------------------|
| scheduler/023 | Unauthenticated access denied | auto         | raw httpx (no auth) → submit/classify/metrics return 401/403 |
| scheduler/024 | Invalid API key rejected      | auto         | Bearer sk-completely-bogus-key → 401/403 |
| scheduler/025 | Task visible to creator       | auto         | submit → retrieve by ID → fake UUID returns 404 |
