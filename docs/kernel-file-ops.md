# Kernel File Operations

## Architecture

### VFS Kernel

The Nexus kernel exposes a virtual file system over JSON-RPC 2.0. Every file
operation is an RPC call to `/api/nfs/{method}` with zone context derived from
the API key in the `Authorization` header.

```text
  Client (NexusClient)
        |
        | POST /api/nfs/{method}
        | Authorization: Bearer <api_key>
        | Body: {"jsonrpc":"2.0", "method":"...", "params":{...}, "id": N}
        |
        v
  +------------------+
  | JSON-RPC Router  |
  +------------------+
        |
        v
  +------------------+
  | Zone Derivation  |  API key -> zone_id
  +------------------+
        |
        v
  +------------------+
  | Permission Check |  ReBAC lookup
  +------------------+
        |
        v
  +------------------+
  | Storage Backend  |  Content-Addressable Storage (CAS)
  +------------------+
```

### RPC Operations

| Method         | RPC Path                | Parameters                                          |
| -------------- | ----------------------- | --------------------------------------------------- |
| `write`        | `/api/nfs/write`        | `path`, `content` (optional `encoding`, `metadata`) |
| `read`         | `/api/nfs/read`         | `path`                                              |
| `delete`       | `/api/nfs/delete`       | `path`                                              |
| `mkdir`        | `/api/nfs/mkdir`        | `path`, `parents`                                   |
| `list`         | `/api/nfs/list`         | `path` (optional `cursor`)                          |
| `glob`         | `/api/nfs/glob`         | `pattern`                                           |
| `grep`         | `/api/nfs/grep`         | `pattern`, `path`                                   |
| `rename`       | `/api/nfs/rename`       | `old_path`, `new_path`                              |
| `copy`         | `/api/nfs/copy`         | `src_path`, `dst_path`                              |
| `exists`       | `/api/nfs/exists`       | `path`                                              |
| `get_metadata` | `/api/nfs/get_metadata` | `path`                                              |
| `rmdir`        | `/api/nfs/rmdir`        | `path`, `recursive`                                 |

### Response Envelope

All RPC calls return an `RpcResponse` with this structure:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { ... },
  "error": null
}
```

On failure, `error` is populated and `result` is null:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": null,
  "error": { "code": -404, "message": "File not found", "data": null }
}
```

HTTP-level errors (401, 403, 500) are wrapped as RPC errors with code
`-{status_code}`. The client never raises on HTTP errors.

### Content-Addressable Storage (CAS)

Identical content written to different paths produces the same `etag`. Tests
verify this deduplication behavior:

- Write same content to two paths -> etags match
- Rewrite identical content -> etag is stable
- Overwrite with different content -> etag changes

### Result Format Flexibility

`glob()`, `list_dir()`, and `grep()` return variable formats depending on the
server version:

| Format              | Example                                                    |
| ------------------- | ---------------------------------------------------------- |
| List of strings     | `["/path/a.txt", "/path/b.txt"]`                           |
| List of dicts       | `[{"name": "a.txt", "type": "file"}]`                      |
| Dict with entries   | `{"entries": [...], "has_more": false}`                    |
| Dict with matches   | `{"matches": [...]}` (grep)                                |
| Dict with files     | `{"files": [...], "has_more": true, "next_cursor": "..."}` |

Use `extract_paths(result)` to normalize any of these into `list[str]`.

---

## NexusClient API

**Location:** `tests/helpers/api_client.py`

### Core Classes

**NexusClient** -- RPC facade with auto-incrementing request IDs:

```python
@dataclass
class NexusClient:
    http: httpx.Client
    base_url: str = ""
    api_key: str = ""
    _rpc_id: int = field(default=0, repr=False)
```

**RpcResponse** -- immutable Pydantic model:

```python
class RpcResponse(BaseModel):
    @property
    def ok(self) -> bool: ...        # True if error is None

    @property
    def content_str(self) -> str: ...  # Decode result as string
        # Handles: plain string, base64 bytes, nested dict
```

**CliResult** -- frozen dataclass for subprocess output:

```python
@dataclass(frozen=True)
class CliResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool: ...  # exit_code == 0
```

### Zone Client Factory

```python
def for_zone(self, zone_api_key: str) -> NexusClient:
    """New client with dedicated httpx session bound to zone key.
    Caller must close the returned client's http session."""
```

---

## Assertion Helpers

**Location:** `tests/helpers/assertions.py`

| Helper                                           | Purpose                                        |
| ------------------------------------------------ | ---------------------------------------------- |
| `assert_rpc_success(resp)`                       | Assert `resp.ok`, return `resp.result`         |
| `assert_rpc_error(resp, *, code, msg_contains)`  | Assert error with optional code/message match  |
| `assert_file_roundtrip(nexus, path, content)`    | Write then read, assert content matches        |
| `assert_file_not_found(nexus, path)`             | Assert read returns error                      |
| `assert_directory_contains(nexus, path, names)`  | Assert listing contains expected entries       |
| `assert_cli_success(result)`                     | Assert `exit_code == 0`, return stdout         |
| `assert_cli_error(result, *, stderr_contains)`   | Assert non-zero exit code                      |
| `extract_paths(result)`                          | Normalize glob/grep/list results to `list[str]`|

### extract_paths

Handles all result format variations with bounded recursion (depth <= 3):

```python
def extract_paths(result: object, *, _depth: int = 0) -> list[str]:
    # List of dicts -> extract "path" or "name" field
    # List of strings -> convert directly
    # Dict with "entries"/"matches"/"files" -> recurse
```

---

## Data Generators

**Location:** `tests/helpers/data_generators.py`

### generate_tree

Creates a file tree of configurable depth and breadth:

```python
def generate_tree(nexus: NexusClient, base_path: str, *,
                  depth: int = 3, breadth: int = 3) -> TreeStats:
```

```text
{base_path}/
├── data.txt
├── dir_0/
│   ├── data.txt
│   ├── dir_0/ ...
│   └── dir_{breadth-1}/
├── dir_1/ ...
└── dir_{breadth-1}/ ...
```

Returns `TreeStats(files_created, dirs_created, total_bytes, base_path)` (frozen).

### load_benchmark_files

```python
def load_benchmark_files(benchmark_dir: str | Path) -> dict[str, list[Path]]:
    # Recursively scan, group by extension
    # Returns {".txt": [Path, ...], ".json": [Path, ...]}
    # Empty dict if directory missing
```

### seed_herb_data

```python
def seed_herb_data(nexus: NexusClient, zone: str,
                   benchmark_dir: str | Path, *,
                   max_files: int = 100) -> SeedResult:
    # Load benchmark files, write to /seed-data/{filename}
    # Returns SeedResult(files_seeded, zones_seeded, errors) (frozen)
```

---

## Test Setup

### Server Startup

```bash
docker compose -f dockerfiles/docker-compose.cross-platform-test.yml up -d
```

The session-scoped `_cluster_ready` fixture (autouse) blocks until
`GET /health` returns OK, with exponential backoff up to
`settings.cluster_wait_timeout` (default 120s). The entire session is skipped if
the cluster is unreachable.

### Running Tests

```bash
pytest -m quick              # Smoke tests (<2 min)
pytest -m auto               # Full regression
pytest -m stress             # Concurrency and large data
pytest -m kernel             # All kernel tests
pytest -m "quick and kernel" # Fast kernel smoke tests
pytest -m "auto or perf"     # Regression + benchmarks
```

### Test Markers

| Marker       | Purpose                                  |
| ------------ | ---------------------------------------- |
| `quick`      | Smoke tests, fast runtime                |
| `auto`       | Full regression suite                    |
| `stress`     | Concurrency, large data, resource limits |
| `perf`       | Performance benchmarks                   |
| `kernel`     | Core FS operations                       |
| `zone`       | Multi-tenancy isolation                  |
| `federation` | Multi-node replication                   |
| `hooks`      | VFS pre/post write hooks                 |
| `dangerous`  | May crash server or corrupt data         |
| `property`   | Property-based / fuzz testing            |
| `chaos`      | Fault injection, partition, corruption   |

### Hypothesis Profiles

Set via `HYPOTHESIS_PROFILE` env var:

| Profile    | max_examples | deadline | Notes                       |
| ---------- | ------------ | -------- | --------------------------- |
| `dev`      | 10           | 500ms    | Default, fast local dev     |
| `ci`       | 1,000        | None     | Derandomized, deterministic |
| `thorough` | 100,000      | None     | Full fuzzing, all phases    |

---

## Fixture Architecture

### Scoping Strategy

```text
Session-scoped (created once)
  settings            TestSettings from env / .env.test
  http_client         httpx.Client (leader, auth, pooling)
  follower_http_client  httpx.Client (follower node)
  nexus               NexusClient (leader, admin)
  nexus_follower      NexusClient (follower)
  _cluster_ready      Health check gate (autouse)
  benchmark_data      Lazy-loaded benchmark files

Module-scoped
  scratch_zone        Pre/post cleans /test-data, returns zone ID

Function-scoped (per test)
  worker_id           pytest-xdist worker ID or "main"
  unique_path         /test-{worker_id}/{uuid[:8]}/
  make_file           File factory with auto-cleanup
  kernel_tree         Pre-built 3-level test tree
```

### unique_path

Generates a collision-free path prefix per test using the xdist worker ID and a
UUID fragment:

```text
/test-main/a1b2c3d4/
/test-gw0/e5f6a7b8/
```

### make_file  (function)

Factory callable that creates files and deletes them on teardown:

```python
def make_file(name: str, content: str = "test content") -> str:
    # Writes {unique_path}/{name} via nexus.write_file()
    # Tracks path for cleanup
    # Returns full path
```

Cleanup iterates in reverse order, suppressing exceptions.

### kernel_tree  (function)

**Location:** `tests/kernel/conftest.py`

Creates a small 3-level tree for glob/grep/listing tests:

```text
{unique_path}/tree/
├── root.txt          "root content"
├── level1_a/
│   ├── a.txt         "level 1a content"
│   └── level2_a/
│       └── deep.txt  "level 2 deep content"
└── level1_b/
    └── b.py          "print('hello')"
```

Yields `{"base": str, "files": list[str], "dirs": list[str]}`.

---

## Test Coverage

### CRUD  (`tests/kernel/test_crud.py`)

| ID         | Test                   | Markers     | Operations                |
| ---------- | ---------------------- | ----------- | ------------------------- |
| kernel/001 | Write-read roundtrip   | quick, auto | write, read               |
| kernel/002 | Overwrite changes etag | quick, auto | write x2, etag compare    |
| kernel/003 | Delete file            | quick, auto | write, delete, read       |
| kernel/004 | Mkdir and list         | quick, auto | mkdir, write, list        |
| kernel/005 | Nested mkdir           | quick, auto | mkdir(parents=True)       |
| kernel/006 | Rmdir empty            | quick, auto | mkdir, rmdir              |
| kernel/007 | Rmdir recursive        | quick, auto | write, rmdir(recursive)   |
| kernel/008 | Rename                 | quick, auto | write, rename, read       |
| kernel/009 | Copy                   | quick, auto | write, copy, read (xfail) |
| kernel/010 | Tree view              | quick, auto | write, list, glob         |

### Glob & Grep  (`tests/kernel/test_glob_grep.py`)

| ID         | Test                     | Markers     | Operations       |
| ---------- | ------------------------ | ----------- | ---------------- |
| kernel/011 | Glob pattern (`**/*.py`) | quick, auto | write, glob      |
| kernel/012 | Glob exclusion           | quick, auto | write, glob x2   |
| kernel/013 | Grep content match       | quick, auto | write, grep      |
| kernel/014 | Grep regex               | auto        | write, grep      |

### CAS & Metadata  (`tests/kernel/test_cas_metadata.py`)

| ID         | Test                             | Markers | Operations                         |
| ---------- | -------------------------------- | ------- | ---------------------------------- |
| kernel/015 | Duplicate etag detection         | auto    | write x2, etag compare             |
| kernel/016 | File metadata stat               | auto    | write, get_metadata                |
| kernel/017 | Custom metadata                  | auto    | write(metadata=), get_metadata     |
| kernel/018 | Binary file roundtrip            | auto    | write(base64), read, SHA-256       |
| kernel/019 | Empty file                       | auto    | write(""), read                    |
| kernel/020 | Unicode filename                 | auto    | write, read (skip if unsupported)  |
| kernel/021 | Special chars in path            | auto    | write, read (spaces, `=`, `&`)     |
| kernel/022 | CAS dedup hash                   | auto    | write x2, get_metadata             |
| kernel/023 | Stable etag on identical rewrite | auto    | write x2, etag compare             |

### Error Cases  (`tests/kernel/test_crud.py`)

| ID         | Test                         | Markers     | Assertion                         |
| ---------- | ---------------------------- | ----------- | --------------------------------- |
| kernel/024 | Path traversal blocked       | quick, auto | `/../../../etc/passwd` denied     |
| kernel/025 | Read nonexistent             | quick, auto | Returns error                     |
| kernel/026 | Write missing parent         | quick, auto | Server-dependent                  |
| kernel/027 | Max path length (4096 chars) | quick, auto | Error or success, no crash        |

### Stress & Performance  (`tests/kernel/test_stress.py`)

| ID         | Test                           | Markers      | Scale                      |
| ---------- | ------------------------------ | ------------ | -------------------------- |
| kernel/050 | Large file (100 MB)            | stress       | SHA-256 verified roundtrip |
| kernel/051 | Concurrent writes (10 threads) | stress       | ThreadPoolExecutor         |
| kernel/052 | Large flat dir ls (10K files)  | stress, perf | Paginated listing          |
| kernel/053 | Nested glob (~4K files)        | stress, perf | depth=6, breadth=5         |
| kernel/054 | Grep large dataset (10K files) | stress, perf | Needle every 100th file    |

---

## Stress Test Patterns

### Parallel Write

All stress tests use a shared `_parallel_write` helper:

```python
def _parallel_write(nexus: NexusClient,
                    files: list[tuple[str, str]], *,
                    workers: int = 20) -> tuple[int, int]:
    # ThreadPoolExecutor with max_workers
    # Returns (success_count, failure_count)
```

Workers scale by test: 20 for flat directory, 50 for nested glob.

### Large File

Deterministic 100 MB content (`b"A" * 1MB * 100`), base64-encoded for transport.
Falls back to 10 MB text if binary encoding fails. Verified via SHA-256 checksum.

### Pagination

Large listing results are paginated via cursor:

```python
all_entries = []
cursor = None
while True:
    result = nexus.rpc("list", {"path": base, "cursor": cursor})
    entries = result.get("files", result.get("entries", []))
    all_entries.extend(entries)
    if not result.get("has_more", False):
        break
    cursor = result.get("next_cursor")
```

### Recovery Sleeps

Stress tests include deliberate pauses (`time.sleep(3-10s)`) between tests to
let the server recover from prior load. Timeouts are set to 300-600s per test.
