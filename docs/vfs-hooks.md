# VFS Hooks

## Architecture

### KernelDispatch Two-Phase Model

VFS hooks follow a two-phase dispatch model:

| Phase     | Timing          | Semantics           | Failure behavior                |
|-----------|-----------------|---------------------|---------------------------------|
| INTERCEPT | Synchronous     | Ordered by priority | Can abort (via `AuditLogError`) |
| OBSERVE   | Fire-and-forget | Unordered           | Exceptions become warnings      |

```text
  VFS write request
        |
        v
  +-------------+
  | Backend      |
  | Commit       |  <-- data persisted to storage
  +-------------+
        |
        v
  +-------------------------------+
  | KernelDispatch                |
  |  .intercept_post_write()      |
  |                               |
  |  Hook B  -->  Hook A          |  INTERCEPT phase (ordered)
  |  (priority)   (priority)      |
  +-------------------------------+
        |
        |--- AuditLogError? --> client receives error
        |                       (data stays committed)
        |
        v
  +-------------------------------+
  | KernelDispatch                |
  |  .notify_observers()          |
  |                               |
  |  Observer 1, Observer 2 ...   |  OBSERVE phase (fire-and-forget)
  +-------------------------------+
        |
        v
  Response to client
```

### Post-Operation Only Design

All hooks execute **after** the file operation completes. Data is committed to
storage before any hook runs. Hooks cannot prevent the write -- they can only
signal errors after commit.

This means:

- `BlockedPathHook` raises `AuditLogError` for paths under `/blocked/`, but the
  file data **is already persisted** in storage
- The client receives an error response, but cleanup does not happen automatically
- Tests must account for files existing even when the hook "rejected" the write

### AuditLogError Abort Behavior

`AuditLogError` is the mechanism for hooks to signal operation failure:

1. Hook executes after write commits
2. Hook raises `AuditLogError`
3. RPC response is transformed to error status
4. Client sees `RpcResponse.ok == False`
5. File data remains in storage (post-operation semantics)

Other exceptions raised by hooks become **warnings** and do not affect the
client response.

### Hook Registration

Hooks are registered server-side during initialization when `NEXUS_TEST_HOOKS=true`
is set. The registration code lives in `nexus/core/test_hooks.py` and populates
the `KernelDispatch` instance at boot time.

---

## Test Setup

### Server Startup

Start the server with test hooks enabled:

```bash
NEXUS_TEST_HOOKS=true docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d
```

This registers three test hooks and exposes the test hook REST API. Without this
flag, all hook tests are automatically skipped.

### Three Test Hooks

| Hook            | Phase     | Behavior                                                      |
|-----------------|-----------|---------------------------------------------------------------|
| AuditMarkerHook | INTERCEPT | Records audit markers (path, timestamp, size) on every write  |
| BlockedPathHook | INTERCEPT | Raises `AuditLogError` for paths under `/blocked/`            |
| ChainOrderHook  | INTERCEPT | Two instances (B, A) record execution order as a trace string |

No OBSERVE-phase test hooks are currently registered; the architecture supports
them but existing tests only exercise the INTERCEPT phase.

**ChainOrderHook ordering**: Instance B is registered before A, so the expected
execution trace is `"BA"`.

### REST API: /api/test-hooks/*

The test hook endpoints allow tests to query hook state after writes:

**Hook availability check:**

```text
GET /api/test-hooks/state
200 = hooks available
```

**Audit marker query:**

```text
GET /api/test-hooks/audit/{path_hash}
```

```json
{
    "found": true,
    "path": "/test-hooks/.../data.txt",
    "timestamp": 1234567890.5,
    "size": 42
}
```

**Chain order query:**

```text
GET /api/test-hooks/chain/{path_hash}
```

```json
{
    "found": true,
    "trace": "BA"
}
```

`path_hash` is the first 16 hex characters of `SHA256(path)`:

```python
def path_hash(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:16]
```

### Contract Constants

Defined in `tests/hooks/conftest.py` (must match `nexus/core/test_hooks.py`):

```python
HOOK_BLOCKED_PREFIX  = "/blocked/"
HOOK_TEST_ENDPOINT   = "/api/test-hooks"
CHAIN_EXPECTED_ORDER = "BA"
```

---

## Fixture Architecture

### Auto-Skip

An `autouse=True` fixture runs before every test in the `tests/hooks/` module:

```python
@pytest.fixture(autouse=True)
def _skip_if_hooks_unavailable(nexus: NexusClient) -> None:
    if not _hooks_available(nexus):
        pytest.skip(
            "Test hooks not available. "
            "Start server with NEXUS_TEST_HOOKS=true"
        )
```

`_hooks_available()` calls `GET /api/test-hooks/state` and returns `True` only
on a 200 response.

### Fixture Summary

| Fixture                      | Scope    | Purpose                                                 |
| ---------------------------- | -------- | ------------------------------------------------------- |
| `_skip_if_hooks_unavailable` | function | Auto-skip if server lacks hook support                  |
| `hook_test_path`             | function | Unique path for positive hook tests                     |
| `blocked_path`               | function | Unique path under `/blocked/` for rejection tests       |
| `nexus`                      | session  | Admin NexusClient (from root conftest)                  |
| `worker_id`                  | function | pytest-xdist worker ID or `"main"`                      |

### hook_test_path  (function)

Generates a unique file path **not** under `/blocked/` for positive hook tests:

```text
/test-hooks/{worker_id}/{uuid[:8]}/data.txt
```

### blocked_path  (function)

Generates a unique file path under the blocked prefix for rejection tests:

```text
/blocked/{worker_id}/{uuid[:8]}/secret.txt
```

### Test Classes

**TestHookInvocation** -- verifies hooks fire on write:

- `hooks/001`: Write file, query audit endpoint, assert `found == true` and path matches
- `hooks/002`: Write file, assert audit marker contains numeric `timestamp` and `size > 0`

**TestHookRejection** -- verifies blocked-path behavior:

- `hooks/003`: Write to blocked path, assert `resp.ok == False` (cleanup: file may exist)
- Positive control: write to non-blocked path, assert success and content match

**TestHookChainOrdering** -- verifies hook priority:

- `hooks/004`: Write file, query chain endpoint, assert `trace == "BA"`
