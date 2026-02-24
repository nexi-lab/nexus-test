# Platform Services

## Overview

Platform services E2E tests validate the seven service categories that make up
the Nexus control plane. Each test exercises real HTTP/RPC calls against a
running server and skips gracefully when a service returns 503 (unavailable).

**Server companion PR**: [nexi-lab/nexus#2591](https://github.com/nexi-lab/nexus/pull/2591)

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

---

## Namespace (`tests/namespace/`)

Zone lifecycle via REST `/api/zones`.

### namespace/001 — Create namespace

Creates a zone via `POST /api/zones`, then verifies it exists with
`GET /api/zones/{zone_id}`. Asserts the returned zone has a matching `zone_id`
and `status` of `"Active"`.

Skips on 401 (JWT auth not configured).

### namespace/002 — List namespaces

Creates a zone, lists all zones via `GET /api/zones`, verifies the new zone
appears in the response list.

Skips on 401.

### namespace/003 — Switch namespace

Writes a file in zone A using `X-Nexus-Zone-ID` header, then reads from zone B.
Expects file-not-found in zone B, proving zone isolation at the RPC layer.

**Server requirement**: `_scope_params_for_zone()` in `rpc.py` prefixes paths
with `/zone/{zone_id}/`. The `X-Nexus-Zone-ID` header must be respected in all
auth paths (database, cached, singleflight) — not just open-access mode.

Skips if zone isolation is not enforced (standalone without zone support).

### namespace/004 — Namespace quota enforcement

Retrieves zone details via `GET /api/zones/{zone_id}` and checks for quota
fields (`limits` or `quota`). Verifies the server exposes resource limits.

Skips on 401 or if zone doesn't expose quota fields.

### namespace/005 — Namespace delete + cleanup

Creates a zone, deletes it via `DELETE /api/zones/{zone_id}`, verifies the zone
returns 404 or status `"Terminating"`.

Skips on 401.

---

## Agent (`tests/agent/`)

Agent registration and lifecycle via RPC `register_agent` + REST `/api/v2/agents`.

### agent/001 — Register agent

Registers an agent via RPC `register_agent`, sets its spec via
`PUT /api/v2/agents/{id}/spec`, verifies the spec is stored.

Skips on 503 (agent registry unavailable).

### agent/002 — Agent heartbeat

Registers agent, sets spec, queries status via
`GET /api/v2/agents/{id}/status`. Verifies heartbeat fields: `phase`,
`conditions`, `resource_usage`.

Skips on 503 or 404 (agent may need warmup first).

### agent/003 — Agent capability query

Registers two agents with different capabilities, retrieves each spec via
`GET /api/v2/agents/{id}/spec`. Verifies capabilities are stored per-agent.

Skips on 503.

### agent/004 — Agent lifecycle FSM

Exercises the lifecycle: register -> set spec -> update spec (generation
increments) -> warmup via `POST /api/v2/agents/{id}/warmup`.

Skips on 503. Warmup may return 422 (accepted as long as spec management works).

---

## Scheduler (`tests/scheduler/`)

Task scheduling via REST `/api/v2/scheduler`.

### scheduler/001 — Schedule task

Submits a task via `POST /api/v2/scheduler/submit`, verifies response contains
valid `task_id`, `status`, and `priority`.

Skips on 503 (scheduler unavailable).

### scheduler/002 — Task retry with backoff

Submits a task, re-submits with the same `idempotency_key`. Verifies the
scheduler deduplicates or handles retries correctly.

Skips on 503.

### scheduler/003 — Task priority ordering

Submits tasks at four priority tiers (low, normal, high, critical). Verifies
priority is stored correctly. Tests `/api/v2/scheduler/classify` and
`/api/v2/scheduler/metrics` endpoints.

Skips on 503.

### scheduler/004 — Task cancellation

Submits a task, cancels via `POST /api/v2/scheduler/task/{id}/cancel`, verifies
cancellation is acknowledged and task status reflects it.

Skips on 503.

---

## Event Log (`tests/eventlog/`)

Event emission and replay via REST `/api/v2/events`.

### eventlog/001 — Write emits event

Writes a file via RPC, queries `GET /api/v2/events`, verifies a write event was
recorded with the matching file path.

Skips on 503 (event log service unavailable).

### eventlog/002 — Event filtering by type

Creates and deletes a file to generate events. Filters by `operation_type` via
query param. Verifies filtered results are a subset of unfiltered.

Skips on 503.

### eventlog/003 — Event replay

Writes multiple files, replays events via `GET /api/v2/events/replay` with
cursor-based pagination. Verifies ordering, `next_cursor`, `has_more` fields,
and no duplicate events across pages.

Skips on 503.

---

## Sync (`tests/sync/`)

Write-back synchronization via REST `/api/v2/sync`.

### sync/001 — Create sync job

Triggers a sync push for a mount point via
`POST /api/v2/sync/mounts/{mount_point}/push`. Verifies response contains push
statistics: `changes_pushed`, `changes_failed`, `conflicts`.

**Server requirement**: `InMemoryWriteBack` fallback must be installed so sync
endpoints return zero-change responses instead of 503 in standalone mode.

Skips if no mounts configured, or on 403/404/503.

### sync/002 — Conflict detection

Verifies the sync response includes a `conflicts_detected` field.

Skips if no mounts, or on 403/404/503.

### sync/003 — Conflict resolution

Triggers sync push twice (idempotency test). Verifies metrics show resolution
state; second push should be idempotent.

Skips if no mounts, or on 403/404/503.

---

## Mount (`tests/mount/`)

Mount lifecycle via RPC + REST `/api/v2/bricks`.

### mount/001 — Mount + read through

Lists mounts via RPC `list_mounts`, writes a file and reads back to test
transparent access through the mount layer. Falls back to
`GET /api/v2/bricks/health`.

Skips if mount listing and brick health are both unavailable.

### mount/002 — Unmount

Gets brick list via `GET /api/v2/bricks/health`, finds a non-essential active
brick, unmounts via `POST /api/v2/bricks/{name}/unmount`, verifies state
changed, then remounts.

Skips on 503, 403, or if no active non-essential bricks.

### mount/003 — Read-only mount

Checks existing mounts for `readonly` flag. Attempts to add a read-only mount
via RPC `add_mount` with `readonly=True`, then verifies writes are rejected.

Skips if mount listing unavailable or no readonly mounts exist.

---

## Upload (`tests/upload/`)

Chunked upload via TUS 1.0.0 protocol at `/api/v2/uploads`.

### upload/001 — Chunked upload (TUS)

Full TUS flow: `OPTIONS` to check server capabilities, `POST` to create upload
session, `PATCH` to upload content as a single chunk, then RPC `read_file` to
verify content.

Skips on 404/405 (TUS endpoint unavailable). Verifies `Tus-Version: 1.0.0`
header.

### upload/002 — Resume interrupted upload

TUS resume flow: `POST` to create session, `HEAD` to check offset (should be 0),
`PATCH` to upload content, verify final offset and file content via RPC read.

Skips on 404/405.

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
# Start server with zone support
NEXUS_DATABASE_URL="sqlite:///./test-e2e-data/nexus.db" \
NEXUS_ENFORCE_PERMISSIONS=true \
nexus serve --init

# Or use the test script
./scripts/serve-for-tests.sh
```

The `--init` flag creates default zones (`corp`, `corp-eng`) and generates an
admin API key written to `.nexus-admin-env`.

---

## Fixture Architecture

### Scoping

```
Session-scoped (from root conftest)
  nexus                 Admin NexusClient (authenticated, health-checked)
  settings              TestSettings from .env.test

Function-scoped (per-test)
  Each test creates its own resources (zones, agents, files) with unique IDs
```

### Skip Strategy

All tests use a consistent pattern for graceful degradation:

```python
if resp.status_code == 503:
    pytest.skip("Service unavailable")
if resp.status_code == 401:
    pytest.skip("JWT auth required, not configured")
```

This allows running the full suite against any server topology — tests that
require unavailable services skip rather than fail.
