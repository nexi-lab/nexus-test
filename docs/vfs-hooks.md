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

### AuditLogError Abort Behavior

`AuditLogError` is the mechanism for hooks to signal operation failure:

1. Hook executes after write commits
2. Hook raises `AuditLogError`
3. RPC response is transformed to error status
4. Client sees `RpcResponse.ok == False`
5. File data remains in storage (post-operation semantics)

Other exceptions raised by hooks become **warnings** and do not affect the
client response.

---

## E2E Test Strategy

### Production Observable Effects

E2E tests verify hook pipeline correctness by observing **real production hook
side effects** rather than injected test hooks.  No server-side test
scaffolding (`NEXUS_TEST_HOOKS`, `/api/test-hooks/*`) is required.

| Production Hook             | Observable Effect                           | E2E Query Method              |
|-----------------------------|---------------------------------------------|-------------------------------|
| RecordStoreWriteObserver    | Populates metadata (size, etag, timestamps) | `get_metadata(path)` RPC      |
| RecordStoreWriteObserver    | Updates version on overwrite                | Write response etag/version   |
| PermissionCheckHook         | Blocks unauthorized writes (pre-intercept)  | Write response `ok == False`  |
| AutoParseWriteHook          | Sets parsed_text/parsed_at metadata         | `get_metadata(path)` after write |

### Metadata Extraction

The server may return metadata at the top level or nested under a `"metadata"`
key.  Tests use `extract_metadata_field()` and `flatten_metadata()` helpers
from `tests/hooks/conftest.py` to handle both formats.

---

## Fixture Architecture

### Fixture Summary

| Fixture          | Scope    | Purpose                                  |
|------------------|----------|------------------------------------------|
| `hook_test_path` | function | Unique path for positive hook tests      |
| `hook_file`      | function | Unique path with auto-cleanup on teardown|
| `nexus`          | session  | Admin NexusClient (from root conftest)   |
| `worker_id`      | function | pytest-xdist worker ID or `"main"`       |

### hook_test_path  (function)

Generates a unique file path for hook tests:

```text
/test-hooks/{worker_id}/{uuid[:8]}/data.txt
```

### Test Classes

**TestHookWriteMetadata** (`test_hooks.py`) — verifies metadata population on write:

- `hooks/001`: Write file → `get_metadata()` returns valid size, etag, hash
- `hooks/002`: Write file → metadata has timestamps (created_at/modified_at) and non-zero size

**TestHookFollowerMetadata** (`test_hooks_backends.py`) — follower node:

- `hooks/003`: Write via follower → `get_metadata()` on follower returns valid data

**TestHookOverwriteMetadata** (`test_hooks_backends.py`) — overwrite updates:

- `hooks/004`: Two writes → metadata size reflects latest content

**TestHookConcurrentMetadata** (`test_hooks_backends.py`) — thread safety:

- `hooks/005`: N concurrent writes → all have valid `get_metadata()` results

**TestHookZoneMetadata** (`test_hooks_backends.py`) — zone isolation:

- `hooks/006`: Write in scratch zone → `get_metadata()` in zone returns valid data

**TestHookDistinctMetadata** (`test_hooks_backends.py`) — per-path isolation:

- `hooks/007`: N writes → each has unique metadata (path, etag)

**TestHookLargeContentMetadata** (`test_hooks_backends.py`) — stress:

- `hooks/008`: Write 1 MB → `get_metadata()` size >= 1 MB
