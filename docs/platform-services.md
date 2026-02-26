# Platform Services

## Architecture

### System Overview

The Nexus control plane exposes seven platform service categories through a
unified FastAPI server. All services share the same authentication middleware
and zone isolation layer.

```
  Client (NexusClient)
      |
      | HTTP (REST + JSON-RPC)
      v
  +-----------------------------+
  | FastAPI Server (:2026)      |
  |                             |
  |  +-----------------------+  |
  |  | Auth Middleware        |  |
  |  | (API key / JWT)       |  |
  |  +-----------------------+  |
  |           |                 |
  |  +-----------------------+  |
  |  | Zone Isolation        |  |
  |  | (ReBAC enforcement)   |  |
  |  +-----------------------+  |
  |           |                 |
  |  +-------+-------+-------+ |
  |  |       |       |       | |
  |  v       v       v       v |
  | Zone   Agent  Sched  Event ||
  | REST   REST   REST   REST  |
  |  |       |       |       | |
  |  +-------+-------+-------+ |
  |  |       |       |       | |
  |  v       v       v       v |
  | Sync   Mount  Upload  RPC ||
  | REST   REST   TUS    NFS  |
  +-----------------------------+
           |
           v
  +-----------------------------+
  | PostgreSQL / SQLite         |
  | (zones, api_keys, rebac,   |
  |  scheduled_tasks, events)  |
  +-----------------------------+
```

### Service Categories

| # | Service     | API Layer       | Storage                           |
|---|-------------|-----------------|-----------------------------------|
| 1 | Namespace   | REST `/api/zones`     | `zones` table (PostgreSQL)  |
| 2 | Agent       | REST + RPC            | `agent_specs`, `agent_status` |
| 3 | Scheduler   | REST `/api/v2/scheduler` | `scheduled_tasks` table  |
| 4 | Event Log   | REST `/api/v2/events`  | `event_log` table          |
| 5 | Sync        | REST `/api/v2/sync`    | WriteBack service          |
| 6 | Mount       | REST + RPC             | Brick lifecycle manager    |
| 7 | Upload      | REST (TUS 1.0.0)       | Chunked file store         |

### Request Flow

Every request follows the same authentication and authorization path:

```
  HTTP Request
      |
      v
  Token Extraction (Authorization header)
      |
      +--- sk- prefix ---------> Database API Key Auth
      +--- JWT format ----------> JWT / OIDC Provider
      +--- no token / unknown --> authenticated=false
      |
      v
  AuthResult (cached 15 min, singleflight dedup)
      |
      +--- 401 if require_auth and not authenticated
      +--- 403 if require_admin and not is_admin
      |
      v
  Route Handler (zone_id derived from key)
```

---

## Test Matrix

| ID | Test | Groups | Pass Criteria |
|----|------|--------|---------------|
| namespace/001 | Create namespace | auto, namespace | Isolation active |
| namespace/002 | List namespaces | auto, namespace | Returns all |
| namespace/003 | Switch namespace | auto, namespace | Context switches |
| namespace/004 | Namespace quota enforcement | auto, namespace | Write rejected at limit |
| namespace/005 | Namespace delete + cleanup | auto, namespace | All data removed |
| agent/001 | Register agent | auto, agent | Agent in registry |
| agent/002 | Agent heartbeat | auto, agent | Status updated |
| agent/003 | Agent capability query | auto, agent | Filtered by capability |
| agent/004 | Agent lifecycle FSM | auto, agent | Correct state transitions |
| scheduler/001 | Schedule task | auto, scheduler | Task created |
| scheduler/002 | Task retry with backoff | auto, scheduler | Retried correctly |
| scheduler/003 | Task priority ordering | auto, scheduler | High first |
| scheduler/004 | Task cancellation | auto, scheduler | Graceful stop |
| eventlog/001 | Write emits event | auto, eventlog | Event in log |
| eventlog/002 | Event filtering by type | auto, eventlog | Correct results |
| eventlog/003 | Event replay | auto, eventlog | In-order replay |
| sync/001 | Create sync job | auto, sync | Job runs |
| sync/002 | Conflict detection | auto, sync | Conflicts flagged |
| sync/003 | Conflict resolution | auto, sync | Resolved correctly |
| mount/001 | Mount + read through | auto, mount | Transparent access |
| mount/002 | Unmount | auto, mount | Mount removed |
| mount/003 | Read-only mount | auto, mount | Writes rejected |
| upload/001 | Chunked upload (TUS) | auto, upload | File assembled |
| upload/002 | Resume interrupted upload | auto, upload | Completes from checkpoint |
| zone/001 | Cross-zone read blocked | auto, zone | Permission denied |
| zone/002 | Zone-scoped file access | auto, zone | Own files only |
| zone/003 | Cross-zone write blocked | auto, zone | Isolation enforced |
| zone/004 | Zone creation | auto, zone | Operational after create |
| zone/005 | Zone deletion + cleanup | auto, zone | Keys revoked, tuples removed |
| zone/006 | Zone-scoped glob | auto, zone | No cross-zone results |

---

## Namespace (`tests/namespace/`)

### Architecture

Zone lifecycle management via REST API. Each zone is a tenant with its own
API keys, ReBAC grants, and resource quotas.

```
  POST /api/zones          DELETE /api/zones/{id}
      |                         |
      v                         v
  +------------------+    +------------------+
  | Zone Provisioner |    | Zone Terminator  |
  | (create zone)    |    | (cascade delete) |
  +------------------+    +------------------+
      |                         |
      v                         v
  +------------------+    +------------------+
  | zones table      |    | Cascade:         |
  | phase=Active     |    |  api_keys.revoke |
  | limits, quotas   |    |  rebac.delete    |
  +------------------+    |  phase=Terminated|
                          +------------------+

  Zone Lifecycle FSM:
  Active --> Terminating --> Terminated
```

### namespace/001 -- Create namespace

Creates a zone via `POST /api/zones`, then verifies it exists with
`GET /api/zones/{zone_id}`. Asserts the returned zone has a matching `zone_id`
and `status` of `"Active"`.

Skips on 401 (JWT auth not configured).

### namespace/002 -- List namespaces

Creates a zone, lists all zones via `GET /api/zones`, verifies the new zone
appears in the response list.

Skips on 401.

### namespace/003 -- Switch namespace

Writes a file in zone A using `X-Nexus-Zone-ID` header, then reads from zone B.
Expects file-not-found in zone B, proving zone isolation at the RPC layer.

**Server requirement**: `_scope_params_for_zone()` in `rpc.py` prefixes paths
with `/zone/{zone_id}/`. The `X-Nexus-Zone-ID` header must be respected in all
auth paths (database, cached, singleflight) -- not just open-access mode.

Skips if zone isolation is not enforced (standalone without zone support).

### namespace/004 -- Namespace quota enforcement

Retrieves zone details via `GET /api/zones/{zone_id}` and checks for quota
fields (`limits` or `quota`). Verifies the server exposes resource limits.

Skips on 401 or if zone doesn't expose quota fields.

### namespace/005 -- Namespace delete + cleanup

Creates a zone, deletes it via `DELETE /api/zones/{zone_id}`, verifies the zone
returns 404 or status `"Terminating"`.

Skips on 401.

---

## Agent (`tests/agent/`)

### Architecture

Agent registration and lifecycle via a hybrid RPC + REST surface. RPC handles
the initial registration (identity assignment), while REST manages the
declarative spec/status model.

```
  register_agent (RPC)           PUT /api/v2/agents/{id}/spec
      |                               |
      v                               v
  +------------------+         +------------------+
  | Agent Registry   |         | Spec Store       |
  | (identity assign)|         | (capabilities,   |
  +------------------+         |  generation++)   |
      |                        +------------------+
      v                               |
  +------------------+                v
  | AgentModel       |         +------------------+
  | (id, zone_id)    |         | Agent Status     |
  +------------------+         | (phase, heartbeat|
                               |  resource_usage) |
                               +------------------+
                                      |
                                      v
                               +------------------+
                               | Warmup           |
                               | (POST .../warmup)|
                               +------------------+

  Agent Lifecycle FSM:
  Registered --> Configured --> Warming --> Ready --> Draining --> Stopped
```

### agent/001 -- Register agent

Registers an agent via RPC `register_agent`, sets its spec via
`PUT /api/v2/agents/{id}/spec`, verifies the spec is stored.

Skips on 503 (agent registry unavailable).

### agent/002 -- Agent heartbeat

Registers agent, sets spec, queries status via
`GET /api/v2/agents/{id}/status`. Verifies heartbeat fields: `phase`,
`conditions`, `resource_usage`.

Skips on 503 or 404 (agent may need warmup first).

### agent/003 -- Agent capability query

Registers two agents with different capabilities, retrieves each spec via
`GET /api/v2/agents/{id}/spec`. Verifies capabilities are stored per-agent.

Skips on 503.

### agent/004 -- Agent lifecycle FSM

Exercises the lifecycle: register -> set spec -> update spec (generation
increments) -> warmup via `POST /api/v2/agents/{id}/warmup`.

Skips on 503. Warmup may return 422 (accepted as long as spec management works).

---

## Scheduler (`tests/scheduler/`)

### Architecture

PostgreSQL-backed priority task queue with HRRN (Highest Response Ratio Next)
scheduling. Tasks are ordered by `(priority_class ASC, HRRN score DESC,
enqueued_at ASC)` for Astraea-style fair scheduling.

```
  POST /api/v2/scheduler/submit
      |
      v
  +------------------+     +------------------+
  | Request Classify |---->| Priority Tier    |
  | (priority param) |     | (low=0, norm=1,  |
  +------------------+     |  high=2, crit=3) |
                           +------------------+
                                  |
                                  v
  +--------------------------------------------------+
  | scheduled_tasks table (PostgreSQL)                |
  |                                                   |
  | id | agent_id | executor_id | task_type | status  |
  | priority_tier | effective_tier | idempotency_key  |
  | priority_class | estimated_service_time           |
  | enqueued_at | started_at | completed_at           |
  +--------------------------------------------------+
      |                   |                   |
      v                   v                   v
  +----------+    +------------+    +----------+
  | Dequeue  |    | Aging      |    | Cancel   |
  | (SKIP    |    | Sweep      |    | (queued  |
  |  LOCKED) |    | (promote   |    |  -> cancelled)
  +----------+    |  starved)  |    +----------+
                  +------------+

  Task FSM:
  queued --> running --> completed
    |                      |
    +--> cancelled         +--> failed

  Idempotency: ON CONFLICT (idempotency_key) returns existing task.
  Concurrency: SELECT ... FOR UPDATE SKIP LOCKED for safe dequeue.
```

### scheduler/001 -- Schedule task

Submits a task via `POST /api/v2/scheduler/submit`, verifies response contains
valid `task_id`, `status`, and `priority`.

Skips on 503 (scheduler unavailable).

### scheduler/002 -- Task retry with backoff

Submits a task, re-submits with the same `idempotency_key`. Verifies the
scheduler deduplicates or handles retries correctly.

Skips on 503.

### scheduler/003 -- Task priority ordering

Submits tasks at four priority tiers (low, normal, high, critical). Verifies
priority is stored correctly. Tests `/api/v2/scheduler/classify` and
`/api/v2/scheduler/metrics` endpoints.

Skips on 503.

### scheduler/004 -- Task cancellation

Submits a task, cancels via `POST /api/v2/scheduler/task/{id}/cancel`, verifies
cancellation is acknowledged and task status reflects it.

Skips on 503.

---

## Event Log (`tests/eventlog/`)

### Architecture

Append-only event log that captures all file system operations. Events are
emitted inline during VFS operations and stored in the `event_log` table.
Supports filtering, cursor-based replay, and SSE streaming.

```
  VFS Operation (write, delete, rename, ...)
      |
      v
  +------------------+
  | Event Emitter    |
  | (inline publish) |
  +------------------+
      |
      v
  +--------------------------------------------------+
  | event_log table (PostgreSQL)                      |
  |                                                   |
  | event_id | seq_number | type | path | timestamp   |
  | zone_id | user_id | metadata                      |
  +--------------------------------------------------+
      |              |              |
      v              v              v
  +----------+  +----------+  +----------+
  | List     |  | Filter   |  | Replay   |
  | /events  |  | ?type=   |  | /replay  |
  | (latest) |  | write    |  | ?cursor= |
  +----------+  +----------+  +----------+
                                    |
                                    v
                              +----------+
                              | Paginate |
                              | next_cursor
                              | has_more |
                              +----------+

  Cursors:
    /events       -- operation_id based (v1 compat)
    /events/replay -- seq_number based (monotonic)
    /events/stream -- SSE real-time (server-sent events)
```

### eventlog/001 -- Write emits event

Writes a file via RPC, queries `GET /api/v2/events`, verifies a write event was
recorded with the matching file path.

Skips on 503 (event log service unavailable).

### eventlog/002 -- Event filtering by type

Creates and deletes a file to generate events. Filters by `operation_type` via
query param. Verifies filtered results are a subset of unfiltered.

Skips on 503.

### eventlog/003 -- Event replay

Writes multiple files, replays events via `GET /api/v2/events/replay` with
cursor-based pagination. Verifies ordering, `next_cursor`, `has_more` fields,
and no duplicate events across pages.

Skips on 503.

---

## Sync (`tests/sync/`)

### Architecture

Write-back synchronization pushes local changes to remote backends. Each mount
point has a write buffer that tracks dirty entries. The sync push endpoint
flushes the buffer and reports statistics.

```
  POST /api/v2/sync/mounts/{mount}/push
      |
      v
  +------------------+
  | Sync Push        |
  | (per-mount)      |
  +------------------+
      |
      v
  +------------------+     +------------------+
  | WriteBack        |---->| Remote Backend   |
  | (dirty buffer)   |     | (S3, GCS, local) |
  +------------------+     +------------------+
      |
      v
  +------------------+
  | Push Statistics  |
  | {                |
  |   changes_pushed |
  |   changes_failed |
  |   conflicts      |
  |   conflicts_detected
  | }                |
  +------------------+

  Standalone Mode:
    InMemoryWriteBack fallback returns zero-change responses
    (prevents 503 when no remote backend configured).

  Idempotency:
    Repeated pushes return same stats (no double-write).
```

### sync/001 -- Create sync job

Triggers a sync push for a mount point via
`POST /api/v2/sync/mounts/{mount_point}/push`. Verifies response contains push
statistics: `changes_pushed`, `changes_failed`, `conflicts`.

**Server requirement**: `InMemoryWriteBack` fallback must be installed so sync
endpoints return zero-change responses instead of 503 in standalone mode.

Skips if no mounts configured, or on 403/404/503.

### sync/002 -- Conflict detection

Verifies the sync response includes a `conflicts_detected` field.

Skips if no mounts, or on 403/404/503.

### sync/003 -- Conflict resolution

Triggers sync push twice (idempotency test). Verifies metrics show resolution
state; second push should be idempotent.

Skips if no mounts, or on 403/404/503.

---

## Mount (`tests/mount/`)

### Architecture

The brick lifecycle manager controls mount/unmount operations. Each brick
is a composable service module with health monitoring and graceful drain.

```
  RPC list_mounts            GET /api/v2/bricks/health
      |                           |
      v                           v
  +------------------+     +------------------+
  | Mount Registry   |     | Brick Health     |
  | (active mounts)  |     | (per-brick state)|
  +------------------+     +------------------+
                                  |
              +-------------------+-------------------+
              |                   |                   |
              v                   v                   v
        +----------+       +----------+       +----------+
        | Active   |       | Unmount  |       | Remount  |
        | (r/w or  |       | (drain + |       | (restore |
        |  r/o)    |       |  detach) |       |  state)  |
        +----------+       +----------+       +----------+

  Brick Lifecycle FSM:
  Pending --> Active --> Draining --> Unmounted
                ^                       |
                +-------<remount>-------+

  Read-Only Mount:
    Mount with readonly=true rejects all write operations
    at the VFS layer before reaching the storage backend.
```

### mount/001 -- Mount + read through

Lists mounts via RPC `list_mounts`, writes a file and reads back to test
transparent access through the mount layer. Falls back to
`GET /api/v2/bricks/health`.

Skips if mount listing and brick health are both unavailable.

### mount/002 -- Unmount

Gets brick list via `GET /api/v2/bricks/health`, finds a non-essential active
brick, unmounts via `POST /api/v2/bricks/{name}/unmount`, verifies state
changed, then remounts.

Skips on 503, 403, or if no active non-essential bricks.

### mount/003 -- Read-only mount

Checks existing mounts for `readonly` flag. Attempts to add a read-only mount
via RPC `add_mount` with `readonly=True`, then verifies writes are rejected.

Skips if mount listing unavailable or no readonly mounts exist.

---

## Upload (`tests/upload/`)

### Architecture

Chunked upload via the TUS 1.0.0 resumable upload protocol. The server
implements the core TUS protocol with creation and offset verification.

```
  OPTIONS /api/v2/uploads     (capabilities)
      |
      v
  Tus-Version: 1.0.0
  Tus-Extension: creation
      |
      v
  POST /api/v2/uploads       (create session)
      |
      +--- Upload-Length: N
      +--- Upload-Metadata: filename ...
      |
      v
  Location: /api/v2/uploads/{upload_id}
      |
      v
  HEAD /api/v2/uploads/{id}  (check offset)
      |
      +--- Upload-Offset: 0
      |
      v
  PATCH /api/v2/uploads/{id} (upload chunk)
      |
      +--- Upload-Offset: 0
      +--- Content-Type: application/offset+octet-stream
      +--- Body: <binary data>
      |
      v
  Upload-Offset: N (complete)
      |
      v
  RPC read_file (verify)     (content matches)

  Resume Flow:
    If upload is interrupted, HEAD returns current offset.
    Client resumes PATCH from that offset.
    Server assembles chunks into final file.
```

### upload/001 -- Chunked upload (TUS)

Full TUS flow: `OPTIONS` to check server capabilities, `POST` to create upload
session, `PATCH` to upload content as a single chunk, then RPC `read_file` to
verify content.

Skips on 404/405 (TUS endpoint unavailable). Verifies `Tus-Version: 1.0.0`
header.

### upload/002 -- Resume interrupted upload

TUS resume flow: `POST` to create session, `HEAD` to check offset (should be 0),
`PATCH` to upload content, verify final offset and file content via RPC read.

Skips on 404/405.

---

## Zone Isolation (`tests/zone/`)

### Architecture

Zone isolation ensures strict data boundaries between tenants. Each zone has
its own API keys and ReBAC grants. Cross-zone access is denied by the
permission system.

```
  Admin Client (bypass)          Zone-scoped Client
      |                               |
      v                               v
  +------------------+         +------------------+
  | Write file       |         | Read file        |
  | (any zone)       |         | (own zone only)  |
  +------------------+         +------------------+
                                      |
                                      v
                               +------------------+
                               | ReBAC Enforcer   |
                               | (zone_id filter) |
                               +------------------+
                                      |
                              +-------+-------+
                              |               |
                           granted         denied
                              |               |
                              v               v
                       +----------+    +----------+
                       | Data     |    | 403      |
                       | Access   |    | Access   |
                       +----------+    | denied   |
                                       +----------+

  Zone Lifecycle:
  POST /api/zones           --> zones.phase = Active
  create_zone_key()         --> api_keys (HMAC-SHA256 hash)
  grant_zone_permission()   --> rebac_tuples (zone_id, path)

  Zone Deletion (cascade):
  DELETE /api/zones/{id}    --> zones.phase = Terminated
  delete_zone_direct()      --> api_keys.revoked = 1
                            --> rebac_tuples deleted
```

### zone/001 -- Cross-zone read blocked

Writes a file via admin client, reads from zone B. Zone B should fail
(different zone, no grant on that namespace).

### zone/002 -- Zone-scoped file access

Writes files via admin to both zone prefixes. Verifies each zone client can
read its own but not the other's.

### zone/003 -- Cross-zone write blocked

Zone A writes a file; zone B cannot read the same path. Zone isolation is
enforced by ReBAC, not path naming.

### zone/004 -- Zone creation

Creates an ephemeral zone, writes a file via admin, verifies the zone is
operational and zone-scoped API keys authenticate correctly.

### zone/005 -- Zone deletion + cleanup

Creates a zone, writes files, terminates the zone, verifies database state:
API keys revoked, ReBAC tuples removed, zone phase is `Terminated`.

### zone/006 -- Zone-scoped glob

Writes `.py` files via admin, verifies zone B cannot glob files from another
zone's namespace.

---

## API Endpoints Summary

| API Layer | Endpoints | Auth |
|-----------|-----------|------|
| Zone REST | `POST/GET/DELETE /api/zones` | Yes (API key or JWT) |
| Agent REST | `PUT/GET /api/v2/agents/{id}/spec`, `GET .../status`, `POST .../warmup` | Yes |
| Agent RPC | `register_agent` | Yes |
| Scheduler REST | `POST /api/v2/scheduler/submit`, `GET .../task/{id}`, `POST .../cancel`, `POST .../classify`, `GET .../metrics` | Yes |
| Event Log REST | `GET /api/v2/events`, `GET /api/v2/events/replay` | Yes |
| Sync REST | `POST /api/v2/sync/mounts/{mount}/push` | Yes (admin) |
| Bricks REST | `GET /api/v2/bricks/health`, `POST .../unmount`, `POST .../remount` | Yes (admin) |
| Upload REST | `OPTIONS/POST/PATCH/HEAD /api/v2/uploads` | Yes |
| File RPC | `write_file`, `read_file`, `delete_file`, `list_mounts`, `add_mount` | Yes |

---

## Server Setup

```bash
# Start server with all services (full profile + PostgreSQL)
NEXUS_DATABASE_URL="postgresql://user:pass@localhost:5432/nexus" \
NEXUS_PROFILE=full \
NEXUS_ENFORCE_PERMISSIONS=true \
NEXUS_ENABLE_WRITE_BUFFER=true \
uv run nexus serve --auth-type database --init

# Or with SQLite (lighter, fewer services)
NEXUS_DATABASE_URL="sqlite:///./test-e2e-data/nexus.db" \
NEXUS_ENFORCE_PERMISSIONS=true \
nexus serve --init
```

The `--init` flag creates default zones (`corp`, `corp-eng`) and generates an
admin API key written to `.nexus-admin-env`.

The `NEXUS_PROFILE=full` flag enables all 21+ bricks including scheduler,
event log, uploads, and agent registry.

---

## Fixture Architecture

### Scoping

```
Session-scoped (from root conftest)
  nexus                 Admin NexusClient (authenticated, health-checked)
  settings              TestSettings from .env.test

Function-scoped (per-test)
  unique_path           Unique path prefix per test (UUID-based)
  Each test creates its own resources (zones, agents, files) with unique IDs

Zone fixtures (from tests/zone/conftest.py)
  zone_a / zone_b       Zone ID strings (corp / corp-eng)
  nexus_a / nexus_b     Zone-scoped NexusClients (non-admin, unique user_ids)
  ephemeral_zone        UUID-based zone per test (auto-cleanup)
  make_file_a/b         File factories with auto-cleanup
```

### Skip Strategy

All tests use a consistent pattern for graceful degradation:

```python
if resp.status_code == 503:
    pytest.skip("Service unavailable")
if resp.status_code == 401:
    pytest.skip("JWT auth required, not configured")
```

This allows running the full suite against any server topology -- tests that
require unavailable services skip rather than fail.

### Zone Client Setup

Zone-scoped clients are created with three steps:

1. **Create zone-scoped API key** via `create_zone_key()` (tries REST, falls
   back to direct DB insertion)
2. **Grant ReBAC permission** on `/` via `grant_zone_permission(zone_id,
   user_id, "/", "direct_owner")`
3. **Instantiate client** via `nexus.for_zone(raw_key)` -- returns a new
   `NexusClient` bound to the zone key

```python
raw_key = create_zone_key(nexus, zone_id, name="test-key", user_id=user_id)
grant_zone_permission(zone_id, user_id, "/", "direct_owner")
client = nexus.for_zone(raw_key)
```

### Database Helpers

Zone and key management helpers in `tests/helpers/zone_keys.py` support both
PostgreSQL (`psycopg2`) and SQLite (`sqlite3`) backends. Database URL is
discovered from `NEXUS_DATABASE_URL` or `NEXUS_TEST_DATABASE_URL` environment
variables.
