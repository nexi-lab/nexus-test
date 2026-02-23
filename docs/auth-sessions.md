# Authentication & Sessions

## Architecture

### Auth Flow

Every HTTP request passes through `resolve_auth` which supports multiple token formats:

```
Authorization: Bearer sk-<zone>_<user>_<keyid>_<random>   API key (Bearer prefix)
Authorization: sk-<zone>_<user>_<keyid>_<random>          API key (raw)
Authorization: Bearer <jwt-token>                         JWT token
X-Nexus-Subject: user:alice                               Identity hint (open access)
X-Nexus-Zone-ID: my-zone                                  Zone hint (open access)
X-Agent-ID: <agent-id>                                    Overrides subject_type to "agent"
```

```
  HTTP request
      |
      v
  +-----------------------+
  | Token Extraction      |
  | (Authorization header)|
  +-----------------------+
      |
      +--- sk- prefix -------> DatabaseAPIKeyAuth
      |                             |
      +--- JWT format --------> JWT/OIDC Provider
      |                             |
      +--- no token / unknown -> authenticated=false
      |
      v
  +-----------------------+
  | Auth Result           |  <-- cached 15 min (SHA-256 key)
  | (AuthResult model)    |      singleflight dedup
  +-----------------------+
      |
      +--- require_auth? ---> 401 if not authenticated
      +--- require_admin? --> 403 if not is_admin
      |
      v
  Route handler
```

### Three Auth Modes

**Mode 1: Open Access** (no `api_key` and no `auth_provider` configured)

All requests are `authenticated=True`. Identity is inferred from `X-Nexus-Subject`
header or by parsing the `sk-` token structure. Admin status is granted only if
`subject_id` appears in the `NEXUS_STATIC_ADMINS` env var. For dev/testing only.

**Mode 2: Auth Provider** (`DiscriminatingAuthProvider` -- production default)

Detects token type by prefix and routes to the correct provider:
- `sk-` prefix --> `DatabaseAPIKeyAuth` (HMAC-SHA256 hash lookup)
- JWT format (3 base64url parts with `alg` header) --> JWT/OIDC provider
- Unknown/ambiguous format --> `authenticated=False`

Auth results are cached for **15 minutes**. A random 1-5ms delay is added on
auth failure to mitigate timing side-channel attacks.

**Mode 3: Static API Key** (legacy fallback)

Constant-time `hmac.compare_digest` comparison against a configured key. Returns
`authenticated=True`, `is_admin=True`, `subject_id="admin"` when matched.

### Soft Reject vs Hard Reject

| Scenario                        | HTTP Status | `authenticated` |
|---------------------------------|-------------|-----------------|
| No token or invalid token       | **200**     | `false`         |
| `require_auth` guard endpoint   | **401**     | N/A (error)     |
| `require_admin`, not admin      | **403**     | N/A (error)     |

The `/api/auth/whoami` endpoint always returns 200 -- it uses the optional
`get_auth_result` dependency, so unauthenticated requests get
`{"authenticated": false}` rather than a 401.

---

## API Key Structure

### Format

```
sk-<zone_prefix>_<subject_prefix>_<key_id_part>_<random_hex>
```

| Segment          | Source                                         | Length       |
|------------------|------------------------------------------------|--------------|
| `zone_prefix`    | First 8 chars of `zone_id` (omitted for admin) | 0-8 chars    |
| `subject_prefix` | First 8 chars of `user_id` (12 for agents)     | 8-12 chars   |
| `key_id_part`    | `secrets.token_hex(4)`                          | 8 hex chars  |
| `random_hex`     | `secrets.token_hex(16)`                         | 32 hex chars |

Minimum length enforced: `API_KEY_MIN_LENGTH = 32`

Examples:
```
sk-corp_tes_user1234_a1b2c3d4_<32-hex>   zone-scoped user key
sk-_admin___a1b2c3d4_<32-hex>            admin key (no zone prefix)
```

### Hashing

Keys are stored as HMAC-SHA256 hashes, never in plaintext:

```python
HMAC_SALT = "nexus-api-key-v1"
key_hash = hmac.new(HMAC_SALT.encode(), key.encode(), hashlib.sha256).hexdigest()
```

### Admin vs Zone-Scoped Keys

| Field                | Admin Key              | Zone-scoped Key         |
|----------------------|------------------------|-------------------------|
| `is_admin`           | `1`                    | `0`                     |
| `zone_id`            | `None` (or any zone)   | specific `zone_id`      |
| `subject_type`       | `"user"` / `"service"` | `"user"` / `"agent"`    |
| Zone isolation       | Bypassed               | Enforced via ReBAC      |

### Zone Derivation from Key

The `parse_sk_token` utility extracts the zone from the key structure at request
time (for rate limiting) without a database lookup:

```python
# sk-myzone_alice_a1b2_... --> SKTokenFields(zone="myzone", user="alice", key_id="a1b2")
```

For OAuth/password users, `zone_id` is derived from email at registration:
- **Personal email** (gmail, outlook, etc.): `zone_id = email_username`
- **Work email**: `zone_id = email_domain`

---

## Whoami Endpoint

### Request

```
GET /api/auth/whoami
```

No auth required -- performs a soft lookup.

### Response

```json
{
    "authenticated": true,
    "subject_type": "user",
    "subject_id": "admin",
    "zone_id": "root",
    "is_admin": true,
    "inherit_permissions": true,
    "user": "admin"
}
```

| Field                 | Type          | Description                          |
|-----------------------|---------------|--------------------------------------|
| `authenticated`       | `bool`        | Whether the token was valid          |
| `subject_type`        | `str | null`  | `"user"`, `"agent"`, or `"service"`  |
| `subject_id`          | `str | null`  | User ID or agent ID                  |
| `zone_id`             | `str | null`  | Zone the key is scoped to            |
| `is_admin`            | `bool`        | Admin privileges                     |
| `inherit_permissions` | `bool`        | Whether permissions are inherited    |
| `user`                | `str | null`  | Alias for `subject_id`               |

---

## Rate Limiting

### Configuration

Uses `slowapi` with a fixed-window strategy. **Disabled by default.**

| Env Variable                       | Default    | Description                |
|------------------------------------|------------|----------------------------|
| `NEXUS_RATE_LIMIT_ENABLED`         | `false`    | Master switch              |
| `NEXUS_RATE_LIMIT_ANONYMOUS`       | `60`       | Anonymous tier (req/min)   |
| `NEXUS_RATE_LIMIT_AUTHENTICATED`   | `300`      | Authenticated tier         |
| `NEXUS_RATE_LIMIT_PREMIUM`         | `1000`     | Premium/admin tier         |
| `NEXUS_REDIS_URL` / `DRAGONFLY_URL`| (none)     | Distributed backend        |

### Rate Limit Key Extraction

Priority order in `_get_rate_limit_key`:

1. Parse `Bearer sk-<...>` token --> `user:<zone>:<user_prefix>` (no DB lookup)
2. `X-Agent-ID` header --> `agent:<agent_id>`
3. Fallback to remote IP address

### Headers

When rate limiting is active, `SlowAPIMiddleware` adds standard headers:

```
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 299
X-RateLimit-Reset: 1708700000
```

On **429 Too Many Requests**:

```json
{
    "error": "Rate limit exceeded",
    "detail": "<limit detail>",
    "retry_after": 60
}
```

With header: `Retry-After: 60`

### Exempt Endpoints

All health/observability endpoints bypass rate limiting:

- `GET /health`, `GET /health/detailed`
- `GET /healthz/live`, `GET /healthz/ready`, `GET /healthz/startup`
- `GET /api/v2/features`
- `GET /metrics`, `GET /metrics/pool`

---

## Key Management

### Current State

Admin key CRUD exists only as **RPC methods** at `POST /api/nfs/{method}`:

| RPC Method           | Purpose                      |
|----------------------|------------------------------|
| `admin_create_key`   | Create a new API key         |
| `admin_list_keys`    | List all keys                |
| `admin_get_key`      | Get key details              |
| `admin_revoke_key`   | Revoke a key                 |
| `admin_update_key`   | Update key metadata          |

**Known bug**: `admin_create_key` crashes with `DiscriminatingAuthProvider` because
the handler accesses `auth_provider._record_store` which only exists on
`DatabaseAPIKeyAuth`, not on the discriminating wrapper. See
[nexi-lab/nexus#2588](https://github.com/nexi-lab/nexus/issues/2588).

No REST endpoint exists for key management (`POST /api/v2/auth/keys` returns 404).
See [nexi-lab/nexus#2590](https://github.com/nexi-lab/nexus/issues/2590).

### SQLite Fallback

The test helpers in `tests/helpers/zone_keys.py` fall back to **direct SQLite
insertion** when the RPC call fails. See the
[zone isolation docs](zone-isolation.md#sqlite-fallback-for-zonekey-creation)
for details on `_create_key_direct` and database discovery.

---

## Test Setup

### Server Startup

Start with auth and rate limiting enabled:

```bash
./scripts/serve-for-tests.sh
```

Or manually:

```bash
NEXUS_ENFORCE_PERMISSIONS=true \
NEXUS_RATE_LIMIT_ENABLED=true \
nexus serve
```

### Test Configuration

All tests use the admin key from `.env.test` (`NEXUS_TEST_API_KEY`). The default
`sk-test-federation-e2e-admin-key` in `tests/config.py` is overridden by `.env.test`.

---

## Fixture Architecture

### Scoping

```
Session-scoped (from root conftest)
  settings                TestSettings from env / .env.test
  http_client             httpx.Client with admin auth
  nexus                   Admin NexusClient

Function-scoped (from tests/auth/conftest.py)
  unauthenticated_client  httpx.Client with NO auth headers
  create_api_key          Factory: create temp keys with auto-revoke
  rate_limit_client       httpx.Client with short timeout for 429 testing
```

### unauthenticated_client

An `httpx.Client` with no `Authorization` header, used for testing rejection:

```python
with httpx.Client(base_url=settings.url, timeout=...) as client:
    yield client
```

### create_api_key

Factory fixture that creates temporary API keys via `POST /api/v2/auth/keys`
and revokes them on teardown. **Skips** if the endpoint returns non-200/201
(currently 404 -- see known bugs above).

```python
key_info = create_api_key("my-test-key")
# key_info = {"key": "sk-...", "id": "..."}
```

### rate_limit_client

An authenticated `httpx.Client` with a short 5-second timeout so 429 responses
surface immediately without retry delays.

---

## Test Classes

### TestValidAuth (auth/001)

- `test_valid_api_key_authenticates`: Call `whoami()` with admin key, assert
  `authenticated=true`
- Markers: `quick`, `auto`, `auth`

### TestInvalidAuth (auth/002)

- `test_invalid_auth_rejected`: Parametrized across 4 failure modes:
  - `no_header` -- no Authorization header
  - `empty_header` -- empty Authorization value
  - `malformed_key` -- syntactically invalid key
  - `nonexistent_key` -- valid format but key not in database
- Accepts both hard reject (401) and soft reject (200 + `authenticated=false`)
- Markers: `quick`, `auto`, `auth`

### TestRateLimiting (auth/003)

- `test_anonymous_burst_triggers_429`: Send 70 anonymous requests, expect 429.
  Skips if rate limiting is disabled.
- `test_authenticated_request_has_rate_limit_headers`: Check for `X-RateLimit-*`
  headers. Skips if headers absent.
- Markers: `auto`, `auth`
- Group: `xdist_group("rate_limit")` -- serialized to avoid interference

### TestApiKeyLifecycle (auth/004)

- `test_create_use_revoke_key`: Create key --> authenticate with it --> revoke -->
  verify 401. Skips if key creation API unavailable.
- Markers: `auto`, `auth`

### TestWhoami (auth/005)

- `test_admin_whoami_fields`: Verify admin identity fields (`authenticated`,
  `is_admin`, `subject_type`, etc.)
- `test_zone_key_whoami`: Create zone key, verify `is_admin=false`. Skips if
  key creation API unavailable.
- Markers: `quick`, `auto`, `auth`
