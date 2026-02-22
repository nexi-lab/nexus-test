# Contributing to Nexus E2E Tests

## Adding a New Test

### 1. Choose the Right Directory

Tests are organized by feature group. Place your test in the matching directory:

```
tests/<feature>/test_<topic>.py
```

For example: `tests/kernel/test_crud.py`, `tests/auth/test_api_keys.py`.

### 2. Naming Conventions

- **Files**: `test_<topic>.py` — one file per logical test group
- **Classes**: `TestFeatureTopic` (e.g., `TestKernelCRUD`, `TestAuthApiKeys`)
- **Methods**: `test_<description>` — descriptive, reads like a sentence

### 3. Test ID Convention

Reference the TEST_PLAN.md section in your docstring:

```python
def test_write_read_roundtrip(self, nexus, unique_path):
    """kernel/001: Write + read roundtrip — content matches."""
```

Format: `<feature>/<number>: <short description> — <expected behavior>`

### 4. Apply Markers

Every test must have at least:
- One **run-type** marker: `quick`, `auto`, `stress`, `dangerous`, etc.
- One **feature** marker: `kernel`, `zone`, `auth`, `federation`, etc.

```python
@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.kernel
class TestKernelCRUD:
    ...
```

### 5. Use Fixtures for Isolation

- Use `unique_path` for test-specific file paths (prevents collisions)
- Use `make_file` for files that need automatic cleanup
- Use `scratch_zone` for zone-scoped test data
- Don't create global state — each test should be independent

### 6. Use Assertion Helpers

Import from `tests.helpers.assertions` instead of writing raw assertions:

```python
from tests.helpers.assertions import (
    assert_rpc_success,
    assert_rpc_error,
    assert_file_roundtrip,
    assert_file_not_found,
    assert_cli_success,
    assert_health_ok,
)
```

## Fixture Patterns

### Factory Fixtures (with cleanup)

For tests that create resources, use factory fixtures with teardown:

```python
@pytest.fixture
def create_widget(nexus):
    created_ids = []

    def _create(**kwargs):
        resp = nexus.api_post("/api/v2/widgets", json=kwargs)
        data = resp.json()
        created_ids.append(data["id"])
        return data

    yield _create

    for widget_id in reversed(created_ids):
        with contextlib.suppress(Exception):
            nexus.api_delete(f"/api/v2/widgets/{widget_id}")
```

### Skip-If Fixtures (conditional tests)

For tests that require optional infrastructure:

```python
@pytest.fixture
def requires_follower(settings):
    if not _is_reachable(settings.url_follower):
        pytest.skip("Follower node not available")
```

### Managed Resource Fixtures (auto-recovery)

For chaos tests that modify infrastructure:

```python
@pytest.fixture
def managed_node():
    yield CONTAINER_NAME
    # Always restart in teardown
    with contextlib.suppress(Exception):
        start_node(CONTAINER_NAME)
```

## Adding a New Feature Group

1. Create the directory: `tests/<feature>/__init__.py`
2. Add a marker in `pyproject.toml` under `[tool.pytest.ini_options] markers`
3. Create `tests/<feature>/conftest.py` with feature-specific fixtures
4. Add test files: `tests/<feature>/test_<topic>.py`
5. Reference TEST_PLAN.md test IDs in docstrings

## Running Your Tests

```bash
# Run just your new tests
uv run pytest tests/<feature>/ -v

# Verify no import errors across the suite
uv run pytest --collect-only 2>&1 | head -20

# Run with the quick marker
uv run pytest -m quick -v

# Check for lint issues
uv run ruff check tests/
```

## Code Style

- Python 3.12+ syntax (`from __future__ import annotations`)
- Line length: 100 characters (configured in `pyproject.toml`)
- Imports sorted by ruff (isort-compatible)
- Frozen dataclasses for all result types (immutability)
- Explicit error handling — never silently swallow errors in test code
