# ReBAC Permissions

## Architecture

### Zanzibar-Style Relationship Model

Nexus implements Relationship-Based Access Control (ReBAC) using a Zanzibar-style
tuple model. Permissions are derived from explicitly stored relationship tuples
rather than static role assignments.

A tuple has the form:

```text
(subject_type, subject_id) --[relation]--> (object_type, object_id)
```

Examples:

```text
(user, alice) --[direct_viewer]--> (file, /docs/readme.txt)
(user, bob)   --[member]---------> (group, engineering)
(group, engineering) --[direct_editor]--> (file, /project/src/)
(file, /docs/readme.txt) --[parent]--> (file, /docs/)
```

### Permission Resolution Pipeline

```text
  Client (NexusClient)
      |
      | POST /api/nfs/rebac_check
      | Body: {subject, permission, object, consistency_mode}
      v
  +------------------------------------------------------+
  | ReBAC Manager (manager.py)                           |
  |                                                      |
  |  1. Consistency level routing                        |
  |     STRONG ──> skip all caches                       |
  |     BOUNDED/EVENTUAL ──> check caches first          |
  |                                                      |
  |  +------------------------------------------------+  |
  |  | Boundary Cache (L0)                             |  |
  |  | O(1) directory inheritance shortcut             |  |
  |  | Skipped if consistency_level == STRONG           |  |
  |  +------------------------------------------------+  |
  |        |  miss                                       |
  |        v                                             |
  |  +------------------------------------------------+  |
  |  | Tiger Cache (L1/L2/L3)                          |  |
  |  | Pre-materialized permission bitmaps             |  |
  |  | L1: In-memory (Roaring Bitmaps)                 |  |
  |  | L2: Dragonfly (Redis-compatible)                |  |
  |  | L3: PostgreSQL                                  |  |
  |  | Skipped if consistency_level == STRONG           |  |
  |  +------------------------------------------------+  |
  |        |  miss                                       |
  |        v                                             |
  |  +------------------------------------------------+  |
  |  | Rust Engine (nexus_pyo3 / PyO3)                 |  |
  |  | InternedGraph: zero-allocation string interning |  |
  |  | Bidirectional tupleToUserset resolution          |  |
  |  +------------------------------------------------+  |
  |        |                                             |
  |        v  result written back to Tiger Cache          |
  +------------------------------------------------------+
```

### Namespace Configuration

Permissions are defined per object type via namespace configs:

```text
namespace "file":
  relations:
    direct_viewer:   Direct           # stored as tuple
    direct_editor:   Direct
    parent:          Direct           # file → parent → folder

  permissions:
    viewer:   union [direct_viewer, group_viewer, parent_viewer]
    editor:   union [direct_editor, group_editor, parent_editor]
    read:     union [viewer, editor]
    write:    union [editor]

  computed usersets:
    group_viewer:    {tupleset: "direct_viewer",  computedUserset: "member"}
    group_editor:    {tupleset: "direct_editor",  computedUserset: "member"}
    parent_viewer:   {tupleset: "parent",         computedUserset: "viewer"}
    parent_editor:   {tupleset: "parent",         computedUserset: "editor"}
```

### tupleToUserset Resolution (Bidirectional)

The Rust engine resolves tupleToUserset in **both directions**:

**Forward (parent pattern):** Object acts as subject to find targets.

```text
parent_viewer = {tupleset: "parent", computedUserset: "viewer"}

file:/docs/readme.txt --[parent]--> file:/docs/
                                        |
                                        v
                              check viewer on file:/docs/
```

**Reverse (group pattern):** Find subjects that have tupleset relation ON object.

```text
group_viewer = {tupleset: "direct_viewer", computedUserset: "member"}

group:engineering --[direct_viewer]--> file:/project/src/
        |
        v
check member on group:engineering
```

### Graph Internals

The Rust engine maintains two adjacency indices for O(1) lookups:

| Index              | Key                                    | Value           | Purpose                          |
| ------------------ | -------------------------------------- | --------------- | -------------------------------- |
| `adjacency_list`   | `(subject_type, subject_id, relation)` | `[objects...]`  | Forward: subject → objects       |
| `reverse_adjacency`| `(object_type, object_id, relation)`   | `[subjects...]` | Reverse: object → subjects       |
| `tuple_index`      | `(obj_type, obj_id, rel, sub_type, sub_id)` | `bool`   | Direct relation check O(1)       |
| `userset_index`    | `(obj_type, obj_id, relation)`         | `[entries...]`  | Userset expansion                |

### Consistency Levels

| Mode                  | Caches Used            | Use Case                              |
| --------------------- | ---------------------- | ------------------------------------- |
| `minimize_latency`    | Boundary + Tiger + all | Default reads, maximum performance    |
| `at_least_as_fresh`   | Boundary + Tiger + all | Read-your-writes with `min_revision`  |
| `fully_consistent`    | None (bypass all)      | Post-revoke verification, audit       |

### Tiger Cache Layers

| Layer | Backend    | TTL       | Eviction             |
| ----- | ---------- | --------- | -------------------- |
| L1    | In-memory  | Configurable | LRU + TTL         |
| L2    | Dragonfly  | Configurable | TTL-based          |
| L3    | PostgreSQL | Persistent   | Explicit invalidation |

Stats are exposed at `GET /api/v2/cache/stats` with keys:
`hits`, `misses`, `sets`, `invalidations`, `l1_size`, `l1_max_size`, `l1_ttl_seconds`, `l2_enabled`.

---

## RPC Operations

All ReBAC operations are JSON-RPC calls to `/api/nfs/{method}`:

| Method              | RPC Path                       | Parameters                                                        |
| ------------------- | ------------------------------ | ----------------------------------------------------------------- |
| `rebac_create`      | `/api/nfs/rebac_create`        | `subject`, `relation`, `object`, `zone_id`, `expires_at`          |
| `rebac_check`       | `/api/nfs/rebac_check`         | `subject`, `permission`, `object`, `zone_id`, `consistency_mode`, `min_revision` |
| `rebac_delete`      | `/api/nfs/rebac_delete`        | `tuple_id`                                                        |
| `rebac_list_tuples` | `/api/nfs/rebac_list_tuples`   | `subject`, `relation`, `object` (all optional filters)            |
| `rebac_explain`     | `/api/nfs/rebac_explain`       | `subject`, `permission`, `object`, `zone_id`                      |
| `rebac_expand`      | `/api/nfs/rebac_expand`        | `permission`, `object`                                            |

### Request/Response Examples

**rebac_create** — returns tuple metadata:

```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "rebac_create",
  "params": {
    "subject": ["user", "alice"],
    "relation": "direct_viewer",
    "object": ["file", "/docs/readme.txt"],
    "zone_id": "corp"
  }
}
```

```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "tuple_id": "t-abc123",
    "revision": 42,
    "consistency_token": "ct-xyz789"
  }
}
```

**rebac_check** — returns boolean permission result:

```json
{
  "jsonrpc": "2.0", "id": 2,
  "method": "rebac_check",
  "params": {
    "subject": ["user", "alice"],
    "permission": "read",
    "object": ["file", "/docs/readme.txt"],
    "zone_id": "corp",
    "consistency_mode": "at_least_as_fresh",
    "min_revision": 42
  }
}
```

```json
{
  "jsonrpc": "2.0", "id": 2,
  "result": {"allowed": true}
}
```

---

## NexusClient API

**Location:** `tests/helpers/api_client.py`

### ReBAC Methods

```python
# Create a relationship tuple
def rebac_create(
    subject: tuple[str, str],   # ("user", "alice")
    relation: str,               # "direct_viewer"
    object_: tuple[str, str],    # ("file", "/doc.txt")
    *,
    zone_id: str | None = None,
    expires_at: str | None = None,
) -> RpcResponse

# Check a permission (Rust-accelerated)
def rebac_check(
    subject: tuple[str, str],
    permission: str,             # "read", "write", "execute"
    object_: tuple[str, str],
    *,
    zone_id: str | None = None,
    consistency_mode: str | None = None,  # "minimize_latency" | "at_least_as_fresh" | "fully_consistent"
    min_revision: int | None = None,
) -> RpcResponse

# Delete a tuple by ID
def rebac_delete(tuple_id: str) -> RpcResponse

# List tuples with optional filters
def rebac_list_tuples(
    *,
    subject: tuple[str, str] | None = None,
    relation: str | None = None,
    object_: tuple[str, str] | None = None,
) -> RpcResponse

# Explain permission resolution (Python engine, full trace)
def rebac_explain(
    subject: tuple[str, str],
    permission: str,
    object_: tuple[str, str],
    *,
    zone_id: str | None = None,
) -> RpcResponse

# Expand: find all subjects with a permission on an object
def rebac_expand(
    permission: str,
    object_: tuple[str, str],
) -> RpcResponse
```

### Response Helpers

```python
def _allowed(resp: RpcResponse) -> bool:
    """Extract boolean from rebac_check response.
    Handles both direct bool and dict with 'allowed' key.
    Returns False on error responses."""

def _explain_allowed(resp: RpcResponse) -> bool:
    """Extract result from rebac_explain trace.
    Uses the explain engine's traced path rather than cached result."""
```

---

## Assertion Helpers

**Location:** `tests/helpers/assertions.py`

| Helper                                     | Purpose                                             |
| ------------------------------------------ | --------------------------------------------------- |
| `assert_rpc_success(resp)`                 | Assert `resp.ok`, return `resp.result`              |
| `assert_rpc_error(resp, *, code, msg)`     | Assert error with optional code/message match       |
| `assert_permission_denied(resp)`           | Assert 403 or forbidden/denied/permission message   |
| `assert_health_ok(nexus)`                  | Assert `/health` returns healthy status             |
| `assert_http_ok(resp)`                     | Assert HTTP 200, return JSON body                   |

---

## Zone Key Provisioning

**Location:** `tests/helpers/zone_keys.py`

Tests requiring zone-scoped clients (e.g., unprivileged users for write enforcement)
use `create_zone_key()` to provision non-admin API keys:

```python
def create_zone_key(
    admin_client: NexusClient,
    zone_id: str,
    *,
    name: str = "test-key",
    user_id: str | None = None,
) -> str:
    """Create a zone-scoped API key. Returns the raw key string.

    Strategy:
    1. Try admin_create_key RPC (preferred)
    2. Fallback to direct database insertion with HMAC-SHA256 hashing
    """
```

Key format: `sk-<zone[:8]>_<user[:8]>_<key_id>_<random>`

HMAC hashing matches the server's `DatabaseAPIKeyAuth._hash_key()` using the
salt `nexus-api-key-v1`.

---

## Test Setup

### Prerequisites

```bash
# Start infrastructure (PostgreSQL, Dragonfly)
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d

# Start Nexus server with required env vars
export NEXUS_DATABASE_URL=postgresql://postgres:nexus@localhost:5432/nexus
uv run nexus serve --port 2026
```

The server must have:
- `enforce_permissions=true` (checked by module-scoped autouse fixture)
- `permissions` brick enabled (checked via `/api/v2/features`)
- ReBAC namespace config loaded for `file` and `group` types

### Running Tests

```bash
pytest tests/rebac/                          # All ReBAC tests
pytest tests/rebac/ -m quick                 # Smoke tests only
pytest tests/rebac/ -m auto                  # Full regression
pytest tests/rebac/ -k "test_grant"          # Specific test
pytest tests/rebac/ -v --tb=short            # Verbose with short tracebacks
```

### Test Markers

| Marker  | Purpose                       |
| ------- | ----------------------------- |
| `quick` | Smoke tests, fast runtime     |
| `auto`  | Full regression suite         |
| `rebac` | All ReBAC permission tests    |

---

## Fixture Architecture

### Scoping Strategy

```text
Session-scoped (inherited from conftest.py root)
  settings            TestSettings from env / .env.test
  http_client         httpx.Client (leader, auth, pooling)
  nexus               NexusClient (leader, admin)
  _cluster_ready      Health check gate (autouse)

Module-scoped (tests/rebac/conftest.py)
  _rebac_enforcement_required   Skip module if enforce_permissions=false (autouse)
  unprivileged_client           UnprivilegedContext (zone key + user_id)

Function-scoped (per test)
  create_tuple        Factory with automatic teardown cleanup
```

### create_tuple Factory

The core fixture for all ReBAC tests. Creates tuples and tracks them for
automatic cleanup:

```python
@pytest.fixture
def create_tuple(nexus, settings) -> Generator[CreateTupleFn, None, None]:
    created_tuple_ids: list[str] = []

    def _create(subject, relation, object_, *, zone_id=None, expires_at=None):
        resp = nexus.rebac_create(subject, relation, object_, zone_id=zone_id or settings.zone)
        if resp.ok and resp.result:
            created_tuple_ids.append(resp.result["tuple_id"])
        return resp

    yield _create

    # Teardown: delete all tuples in reverse order
    for tid in reversed(created_tuple_ids):
        nexus.rebac_delete(tid)  # suppresses exceptions
```

### UnprivilegedContext

Used by rebac/006 (write enforcement) to test operations without ReBAC grants:

```python
@dataclass(frozen=True)
class UnprivilegedContext:
    client: NexusClient   # Zone-scoped client with valid auth but ZERO grants
    user_id: str          # Associated user ID for grant assertions
```

### _rebac_enforcement_required (autouse)

Module-scoped gate that skips all ReBAC tests if the server doesn't enforce
permissions. Checks both `/health` (`enforce_permissions` field) and
`/api/v2/features` (`permissions` in `enabled_bricks`).

---

## Test Coverage

### Lifecycle (tests/rebac/test_rebac.py)

| ID        | Test                             | Markers          | Operations                                         |
| --------- | -------------------------------- | ---------------- | -------------------------------------------------- |
| rebac/001 | Grant creates tuple              | quick, auto      | rebac_create, rebac_list_tuples                    |
| rebac/002 | Check permission true/false      | quick, auto      | rebac_create, rebac_check (viewer, editor)         |
| rebac/003 | Revoke lifecycle                 | quick, auto      | create, check, delete, list, fully_consistent      |
| rebac/003 | Expired tuple denied             | quick, auto      | create(expires_at=past), check                     |

### Inheritance & Groups

| ID        | Test                             | Markers          | Operations                                         |
| --------- | -------------------------------- | ---------------- | -------------------------------------------------- |
| rebac/004 | Group membership inheritance     | quick, auto      | member tuple, group grant, check, revoke member    |
| rebac/005 | Nested group closure (2-hop)     | quick, auto      | user→sub→parent→file, check 1-hop, break middle   |
| rebac/009 | Parent folder inheritance        | quick, auto      | folder grant, parent relation, check child file    |

### Enforcement & Scoping

| ID        | Test                             | Markers          | Operations                                         |
| --------- | -------------------------------- | ---------------- | -------------------------------------------------- |
| rebac/006 | Write denied then granted        | quick, auto      | unprivileged write→403, grant, retry→success       |
| rebac/007 | Zone-scoped permissions          | quick, auto      | grant in zone A, verify no leakage to zone B       |

### Audit & Metadata

| ID        | Test                             | Markers          | Operations                                         |
| --------- | -------------------------------- | ---------------- | -------------------------------------------------- |
| rebac/008 | Changelog audit metadata         | quick, auto      | create x2, delete x1, verify revisions monotonic   |
| rebac/008 | List reflects changes            | quick, auto      | create x2, delete x1, verify list consistency      |

### Caching

| ID        | Test                             | Markers          | Operations                                         |
| --------- | -------------------------------- | ---------------- | -------------------------------------------------- |
| rebac/010 | Tiger Cache write-through cycle  | quick, auto      | grant, check (populate), check (hit), revoke, fully_consistent |
| rebac/011 | Tiger Cache stats endpoint       | quick, auto      | grant, check, GET /api/v2/cache/stats              |

---

## Key Patterns

### Read-Your-Writes with min_revision

After creating a tuple, use `at_least_as_fresh` with the returned `revision` to
avoid stale reads:

```python
resp = create_tuple(subject, "direct_viewer", object_)
revision = resp.result["revision"]

check = nexus.rebac_check(
    subject, "read", object_,
    zone_id=zone,
    consistency_mode="at_least_as_fresh",
    min_revision=revision,
)
```

### Post-Revoke Verification with fully_consistent

After deleting a tuple, use `fully_consistent` to bypass all caches and verify
the revocation took effect:

```python
nexus.rebac_delete(tuple_id)

check = nexus.rebac_check(
    subject, "read", object_,
    zone_id=zone,
    consistency_mode="fully_consistent",
)
assert not _allowed(check)
```

### Group Inheritance Setup

To test group-based access (reverse tupleToUserset):

```python
# 1. User is member of group
create_tuple(("user", "alice"), "member", ("group", "team"))

# 2. Group has viewer on file
create_tuple(("group", "team"), "direct_viewer", ("file", "/secret.txt"))

# 3. User inherits read via group membership
check = nexus.rebac_check(("user", "alice"), "read", ("file", "/secret.txt"), ...)
assert _allowed(check)
```

### Parent Folder Inheritance Setup

To test directory-based access (forward tupleToUserset):

```python
# 1. User has viewer on folder
create_tuple(("user", "alice"), "direct_viewer", ("file", "/docs/"))

# 2. File has parent relation to folder
create_tuple(("file", "/docs/readme.txt"), "parent", ("file", "/docs/"))

# 3. User inherits read on file via parent folder
check = nexus.rebac_check(("user", "alice"), "read", ("file", "/docs/readme.txt"), ...)
assert _allowed(check)
```

### Unprivileged Client for Enforcement Tests

```python
def test_write_denied(unprivileged_client, create_tuple):
    unpriv = unprivileged_client.client
    user_id = unprivileged_client.user_id

    # Write without grant → 403
    resp = unpriv.write_file("/path", "content")
    assert_permission_denied(resp)

    # Grant write permission → retry → success
    create_tuple(("user", user_id), "direct_editor", ("file", "/zone/.../path"))
    resp2 = unpriv.write_file("/path", "content")
    assert resp2.ok
```
