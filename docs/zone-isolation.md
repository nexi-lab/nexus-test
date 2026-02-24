# Zone Isolation

## Architecture

### Zone Derivation

The Nexus server extracts the **zone from the API key**, not from RPC parameters.
Every VFS call carries a `Bearer <zone_api_key>` header; the server resolves the
key to a `zone_id` and enforces isolation before the operation reaches storage.

Admin keys bypass zone isolation entirely.

```
                      API Key
                        |
                        v
               +------------------+
               | Zone Derivation  |
               | (key -> zone_id) |
               +------------------+
                        |
            +-----------+-----------+
            |                       |
        admin key              zone-scoped key
            |                       |
            v                       v
      bypass isolation     +------------------+
                           | Permission Check |
                           | (ReBAC lookup)   |
                           +------------------+
                                    |
                              +-----+-----+
                              |           |
                           granted      denied
                              |           |
                              v           v
                     +------------+   403 error
                     | Data Access|
                     +------------+
```

### Isolation Layers

**Layer 1 -- ReBAC zone_id filtering (primary)**

Relationship-Based Access Control enforces zone boundaries via `rebac_tuples`.
Each zone-scoped key can only access objects whose `zone_id` matches the key's
zone. Grants are scoped to path prefixes (e.g. `/test-zone-corp`).

```sql
INSERT OR IGNORE INTO rebac_tuples
  (tuple_id, zone_id, subject_zone_id, object_zone_id,
   subject_type, subject_id, subject_relation, relation,
   object_type, object_id, created_at, expires_at, conditions)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

**Layer 2 -- Owner fast-path bypass prevention**

Tests use **different `user_id` values** per zone to prevent the owner fast-path
from short-circuiting ReBAC checks:

| Zone   | user_id              |
|--------|----------------------|
| Zone A | `test-user-zone-a`   |
| Zone B | `test-user-zone-b`   |

**Layer 3 -- Path-prefix strategy**

Each zone is bound to a unique path prefix derived from its zone ID:

| Zone   | Path prefix                          | Default              |
|--------|--------------------------------------|----------------------|
| Zone A | `/test-zone-{settings.zone}`         | `/test-zone-corp`    |
| Zone B | `/test-zone-{settings.scratch_zone}` | `/test-zone-corp-eng`|

A zone-scoped key has no ReBAC grant for another zone's prefix, so cross-zone
writes and reads are denied even if the underlying storage is shared.

### Standalone Limitation

`RaftMetadataStore` (used in standalone mode) does not support `zone_id` filtering
for list/glob operations. Zone-scoped clients attempting glob across zone boundaries
receive permission errors -- this is the intended isolation behavior, but it means
there is no positive control for glob without an admin client in standalone mode.

---

## Test Setup

### Server Startup

Start the federation cluster (3-node Raft with zones):

```bash
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d
```

Pre-configured zone mounts in the compose file:

```
/corp=corp
/corp/engineering=corp-eng
/corp/sales=corp-sales
/family=family
/family/work=corp
```

### Key Environment Variables

All variables use the `NEXUS_TEST_` prefix and can be set in `.env.test`:

| Variable                         | Default                                            | Purpose                        |
|----------------------------------|----------------------------------------------------|--------------------------------|
| `NEXUS_TEST_URL`                 | `http://localhost:2026`                             | Leader node endpoint           |
| `NEXUS_TEST_URL_FOLLOWER`        | `http://localhost:2027`                             | Follower node endpoint         |
| `NEXUS_TEST_API_KEY`             | `sk-test-federation-e2e-admin-key`                  | Admin API key                  |
| `NEXUS_TEST_ZONE`               | `corp`                                              | Primary zone ID                |
| `NEXUS_TEST_SCRATCH_ZONE`       | `corp-eng`                                          | Secondary zone ID              |
| `NEXUS_TEST_DATABASE_URL`        | `postgresql://postgres:nexus@localhost:5432/nexus`  | RecordStore connection         |
| `NEXUS_TEST_REQUEST_TIMEOUT`     | `30.0`                                              | HTTP timeout (seconds)         |
| `NEXUS_TEST_CLUSTER_WAIT_TIMEOUT`| `120.0`                                             | Cluster readiness timeout      |

### TestSettings

`tests/config.py` defines a Pydantic `BaseSettings` class that loads config from
environment variables (prefix `NEXUS_TEST_`) or `.env.test`:

```python
class TestSettings(BaseSettings):
    url: str = "http://localhost:2026"
    url_follower: str = "http://localhost:2027"
    api_key: str = "sk-test-federation-e2e-admin-key"
    zone: str = "corp"                    # primary zone
    scratch_zone: str = "corp-eng"        # secondary zone
    request_timeout: float = 30.0
    cluster_wait_timeout: float = 120.0
    # ...
```

Validation rejects production URLs (containing "prod", "staging", ".cloud", ".io").

### SQLite Fallback for Zone/Key Creation

The REST API for zone and key management requires a JWT, which the test harness
may not have. The helpers in `tests/helpers/zone_keys.py` fall back to **direct
SQLite insertion** when the RPC call fails.

**Database discovery** (`_find_db_path`) searches in order:

1. `NEXUS_TEST_DB_PATH` env var
2. `~/nexus/test-e2e-data/nexus.db`
3. `~/nexus/nexus-data/nexus.db`
4. `./nexus.db`

**Zone creation** (`create_zone_direct`):

```sql
INSERT OR IGNORE INTO zones
  (zone_id, name, domain, description, settings,
   phase, finalizers, deleted_at, created_at, updated_at)
VALUES (?, ?, NULL, NULL, NULL, 'Active', '[]', NULL, ?, ?)
```

**API key creation** (`_create_key_direct`):

```
Key format:  sk-<zone[:8]>_<user[:8]>_<key_id_hex>_<random_hex>
Key hash:    HMAC-SHA256(salt="nexus-api-key-v1", message=raw_key)
```

```sql
INSERT INTO api_keys
  (key_id, key_hash, user_id, subject_type, subject_id,
   zone_id, is_admin, inherit_permissions, name, created_at, revoked)
VALUES (?, ?, ?, 'user', ?, ?, 0, 0, ?, ?, 0)
```

**Zone deletion** (`delete_zone_direct`) cascades cleanup:

1. Set `zones.phase = 'Terminated'`
2. Revoke all API keys for the zone
3. Delete all ReBAC tuples for the zone

---

## Fixture Architecture

### Scoping Strategy

```
Session-scoped (created once)
  settings          TestSettings loaded from env
  http_client       httpx.Client with admin auth
  nexus             Admin NexusClient
  zone_a_root       "/test-zone-{zone}"       path prefix
  zone_b_root       "/test-zone-{scratch_zone}" path prefix
  nexus_a           Zone A client (non-admin)
  nexus_b           Zone B client (non-admin)

Function-scoped (per test)
  make_file_a       File factory for zone A with auto-cleanup
  make_file_b       File factory for zone B with auto-cleanup
  ephemeral_zone    Unique zone per test (UUID-based)
  zone_a            Zone A ID string
  zone_b            Zone B ID string
```

### nexus_a / nexus_b  (session)

Each zone client is created with three steps:

1. **Create zone-scoped API key** via `create_zone_key()` (tries RPC, falls back
   to SQLite)
2. **Grant ReBAC permission** on the zone's path prefix via
   `grant_zone_permission(zone_id, user_id, path_prefix)`
3. **Instantiate client** via `nexus.for_zone(raw_key)` -- returns a new
   `NexusClient` with its own `httpx.Client` session bound to the zone key

```python
# Zone A
raw_key = create_zone_key(nexus, settings.zone, name="test-zone-a-key",
                          user_id="test-user-zone-a")
grant_zone_permission(settings.zone, "test-user-zone-a", zone_a_root)
client_a = nexus.for_zone(raw_key)

# Zone B
raw_key = create_zone_key(nexus, settings.scratch_zone, name="test-zone-b-key",
                          user_id="test-user-zone-b")
grant_zone_permission(settings.scratch_zone, "test-user-zone-b", zone_b_root)
client_b = nexus.for_zone(raw_key)
```

### make_file_a / make_file_b  (function)

Factory callables that auto-prefix paths with the zone root and register files
for cleanup on teardown:

```python
def _make(relative_path: str, content: str) -> str:
    full_path = f"{zone_root}/{relative_path.lstrip('/')}"
    assert_rpc_success(client.write_file(full_path, content))
    created.append(full_path)
    return full_path

# Usage:
path = make_file_a("isolation/cross_zone.txt", "hello")
# Writes to /test-zone-corp/isolation/cross_zone.txt
```

Cleanup iterates in reverse order, suppressing exceptions.

### ephemeral_zone  (function)

Creates a UUID-based zone for single-test use:

- **ID format**: `test-{worker_id}-{uuid[:8]}` (pytest-xdist safe)
- **Creation**: tries REST `POST /api/zones` first, falls back to SQLite
- **Teardown**: tries REST `DELETE /api/zones/{id}`, falls back to `delete_zone_direct()`
- **Skip**: if neither creation method works, the test is skipped
