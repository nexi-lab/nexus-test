# Nexus E2E Test Suite

End-to-end test suite for [Nexus AI Filesystem](https://github.com/nexus) covering ~355 tests across 14 run-type groups and 40+ feature groups. Tests exercise the system exclusively through the HTTP API and CLI — no internal Python imports.

## Prerequisites

- **Python 3.12+**
- **uv** (package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Docker** (for cluster management)
- A running Nexus cluster (local docker-compose or remote)

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Copy and configure environment
cp .env.test.example .env.test
# Edit .env.test with your API key and cluster URLs

# 3. Start the nexus cluster (if not already running)
cd ~/nexus && docker compose -f dockerfiles/docker-compose.demo.yml up -d

# 4. Run smoke tests
uv run pytest -m quick -v

# 5. Run full regression
uv run pytest -m auto
```

## Configuration

All settings are loaded from environment variables (prefix: `NEXUS_TEST_`) or `.env.test`.

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_TEST_URL` | `http://localhost:2026` | Primary node URL |
| `NEXUS_TEST_URL_FOLLOWER` | `http://localhost:2027` | Follower node URL |
| `NEXUS_TEST_API_KEY` | `sk-test-...` | API key for authentication |
| `NEXUS_TEST_ZONE` | `corp` | Default zone for tests |
| `NEXUS_TEST_SCRATCH_ZONE` | `corp-eng` | Scratch zone (wiped per module) |
| `NEXUS_TEST_NEXUS_REPO_DIR` | `~/nexus` | Path to nexus repo (for compose files) |
| `NEXUS_TEST_BENCHMARK_DIR` | `~/nexus/benchmarks` | Benchmark data directory |
| `NEXUS_TEST_REQUEST_TIMEOUT` | `30.0` | HTTP request timeout (seconds) |
| `NEXUS_TEST_CLUSTER_WAIT_TIMEOUT` | `120.0` | Cluster readiness timeout (seconds) |
| `HYPOTHESIS_PROFILE` | `dev` | Hypothesis profile: dev / ci / thorough |

## Running Tests

```bash
# Smoke tests (< 2 min)
uv run pytest -m quick -v

# Full regression (~15 min)
uv run pytest -m auto

# Specific feature group
uv run pytest -m kernel -v
uv run pytest -m federation -v

# Stress tests (~30 min)
uv run pytest -m stress

# Chaos / fault injection (~30 min, requires full cluster)
uv run pytest -m chaos

# Performance benchmarks
uv run pytest -m perf

# Property-based / fuzz testing
uv run pytest -m property

# Parallel execution (4 workers)
uv run pytest -m auto -n 4

# With coverage
uv run pytest -m auto --cov=tests --cov-report=html
```

## Directory Structure

```
nexus-test/
├── pyproject.toml              # Project config, pytest markers, dependencies
├── .env.test.example           # Environment template
├── TEST_PLAN.md                # Full test plan (~355 tests)
├── README.md                   # This file
├── CONTRIBUTING.md             # How to add tests
├── scripts/                    # Utility scripts
└── tests/
    ├── conftest.py             # Root fixtures (settings, clients, make_file)
    ├── config.py               # Pydantic TestSettings
    ├── helpers/
    │   ├── api_client.py       # NexusClient (RPC + REST + CLI)
    │   ├── assertions.py       # Reusable assertion helpers
    │   ├── docker_helpers.py   # Docker orchestration & chaos injection
    │   └── data_generators.py  # Test data generation & seeding
    ├── fixtures/               # Shared fixture modules
    ├── kernel/                 # Core FS operations (read, write, delete, etc.)
    ├── zone/                   # Multi-tenancy, zone isolation
    ├── auth/                   # API key, OAuth, sessions, rate limiting
    ├── federation/             # Multi-node replication, failover
    ├── chaos/                  # Fault injection, partition, corruption
    ├── hooks/                  # VFS pre/post write hooks
    ├── rebac/                  # Permissions (grant, check, revoke)
    ├── memory/                 # Memory store, query, trajectories
    ├── search/                 # Full-text, semantic search
    ├── pay/                    # Credits, X402, ledger
    ├── llm/                    # Completions, streaming, RAG
    ├── mcp/                    # MCP tool registry, execution
    ├── sandbox/                # Sandbox create, execute, limits
    ├── snapshot/               # Point-in-time capture, restore
    ├── skills/                 # Register, execute, version
    ├── ...                     # (40+ feature groups total)
    ├── property/               # Property-based / fuzz tests
    └── cli/                    # CLI command E2E tests
```

## Fixture Architecture

Fixtures follow a scoping strategy to balance isolation and performance:

| Scope | Fixtures | Purpose |
|-------|----------|---------|
| **session** | `settings`, `http_client`, `nexus`, `nexus_follower`, `benchmark_data` | Shared across all tests |
| **module** | `scratch_zone` | Clean zone per test module |
| **function** | `unique_path`, `make_file`, `worker_id` | Per-test isolation |

Feature-specific fixtures live in each feature's `conftest.py`:
- `tests/zone/conftest.py` — zone pairs, clean paths
- `tests/auth/conftest.py` — unauthenticated clients, API key factories
- `tests/federation/conftest.py` — replication helpers, managed nodes
- `tests/chaos/conftest.py` — partition/kill fixtures with auto-recovery
