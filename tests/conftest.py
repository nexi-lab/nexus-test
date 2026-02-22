"""Root conftest.py — shared fixtures for all E2E tests.

Fixture scoping strategy (Decision #10):
    session:   TestSettings, httpx.Client, NexusClient, cluster health check
    module:    Feature-specific test data seeding
    function:  Unique paths (UUID), per-test file cleanup

Provides:
    - settings: Pydantic TestSettings loaded from env / .env.test
    - http_client: Session-scoped httpx.Client with auth and connection pooling
    - nexus: NexusClient facade (RPC + REST + CLI)
    - follower_client / nexus_follower: For federation tests
    - unique_path: UUID-based path generator for test isolation
    - make_file: Factory fixture with eager cleanup
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Callable, Generator

import httpx
import pytest
from tenacity import retry, stop_after_delay, wait_exponential

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.data_generators import load_benchmark_files


# ---------------------------------------------------------------------------
# Hypothesis profiles (mirrors ~/nexus/tests/conftest.py)
# ---------------------------------------------------------------------------

try:
    from hypothesis import HealthCheck, Phase
    from hypothesis import settings as hypothesis_settings

    hypothesis_settings.register_profile(
        "dev",
        max_examples=10,
        deadline=500,
    )
    hypothesis_settings.register_profile(
        "ci",
        max_examples=1000,
        deadline=None,
        derandomize=True,
        print_blob=True,
        suppress_health_check=[HealthCheck.too_slow],
    )
    hypothesis_settings.register_profile(
        "thorough",
        max_examples=100_000,
        deadline=None,
        derandomize=True,
        print_blob=True,
        suppress_health_check=[HealthCheck.too_slow],
        phases=[Phase.explicit, Phase.reuse, Phase.generate, Phase.shrink],
    )
    hypothesis_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))
except ImportError:
    pass  # hypothesis not installed — property-based tests will be skipped


# ---------------------------------------------------------------------------
# Session-scoped fixtures (created once per test session)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def settings() -> TestSettings:
    """Load test settings from environment / .env.test file."""
    return TestSettings()


@pytest.fixture(scope="session")
def http_client(settings: TestSettings) -> httpx.Client:
    """Session-scoped httpx client with auth headers and connection pooling.

    Points at the primary nexus node (leader).
    """
    with httpx.Client(
        base_url=settings.url,
        headers={"Authorization": f"Bearer {settings.api_key}"},
        timeout=httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    ) as client:
        yield client


@pytest.fixture(scope="session")
def follower_http_client(settings: TestSettings) -> httpx.Client:
    """Session-scoped httpx client pointing at the follower node.

    Used by federation tests. If the follower URL defaults to localhost:2027
    and is unreachable, federation tests will fail at request time.
    """
    with httpx.Client(
        base_url=settings.url_follower,
        headers={"Authorization": f"Bearer {settings.api_key}"},
        timeout=httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    ) as client:
        yield client


@pytest.fixture(scope="session")
def nexus(http_client: httpx.Client, settings: TestSettings) -> NexusClient:
    """Primary NexusClient (leader node)."""
    return NexusClient(
        http=http_client,
        base_url=settings.url,
        api_key=settings.api_key,
    )


@pytest.fixture(scope="session")
def nexus_follower(
    follower_http_client: httpx.Client, settings: TestSettings
) -> NexusClient:
    """NexusClient pointing at the follower node (for federation tests)."""
    return NexusClient(
        http=follower_http_client,
        base_url=settings.url_follower,
        api_key=settings.api_key,
    )


# ---------------------------------------------------------------------------
# Cluster health check (auto-use, session-scoped)
# ---------------------------------------------------------------------------


def _wait_for_node(url: str, api_key: str, *, timeout: float = 120.0) -> None:
    """Block until a node's health endpoint returns OK.

    Retries with exponential backoff for up to ``timeout`` seconds.
    """
    @retry(
        stop=stop_after_delay(timeout),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    def _check() -> None:
        resp = httpx.get(
            f"{url}/health",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5.0,
        )
        resp.raise_for_status()

    _check()


@pytest.fixture(scope="session", autouse=True)
def _cluster_ready(settings: TestSettings) -> None:
    """Ensure the nexus cluster is reachable before running any tests.

    Retries with exponential backoff for up to cluster_wait_timeout seconds.
    Skips the entire test session if the cluster is unreachable.
    """
    try:
        _wait_for_node(settings.url, settings.api_key, timeout=settings.cluster_wait_timeout)
    except Exception as exc:
        pytest.skip(
            f"Nexus cluster not reachable at {settings.url}: {exc}. "
            f"Start the cluster first: docker compose -f dockerfiles/docker-compose.demo.yml up -d"
        )


# ---------------------------------------------------------------------------
# Function-scoped fixtures (per-test isolation)
# ---------------------------------------------------------------------------


@pytest.fixture
def worker_id(request: pytest.FixtureRequest) -> str:
    """Return the xdist worker ID, or 'main' if not running under xdist."""
    return getattr(request.config, "workerinput", {}).get("workerid", "main")


@pytest.fixture
def unique_path(worker_id: str) -> str:
    """Generate a unique file path prefix for test isolation.

    Format: /test-{worker_id}/{uuid[:8]}/
    Ensures no collisions between parallel workers or sequential tests.
    """
    short_uuid = uuid.uuid4().hex[:8]
    return f"/test-{worker_id}/{short_uuid}"


@pytest.fixture
def make_file(
    nexus: NexusClient, unique_path: str
) -> Generator[Callable[[str, str], str]]:
    """Factory fixture: creates files in the test's unique namespace.

    Files are eagerly cleaned up after the test completes (Decision #14A).

    Usage:
        def test_something(make_file):
            path = make_file("hello.txt", "Hello World")
            # ... test logic ...
            # File is automatically deleted after this test
    """
    created_paths: list[str] = []

    def _make(name: str, content: str = "test content") -> str:
        path = f"{unique_path}/{name}"
        nexus.write_file(path, content)
        created_paths.append(path)
        return path

    yield _make

    # Eager cleanup: delete all files created during this test
    for path in reversed(created_paths):
        with contextlib.suppress(Exception):
            nexus.delete_file(path)


# ---------------------------------------------------------------------------
# Module-scoped fixtures (per-module isolation)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scratch_zone(
    nexus: NexusClient, settings: TestSettings
) -> Generator[str]:
    """Provide a clean scratch zone for the duration of a test module.

    Wipes the scratch zone's /test-data/ directory before and after the module.
    Returns the scratch zone ID for use in tests.
    """
    zone = settings.scratch_zone

    # Pre-clean: remove leftover test data from previous runs
    with contextlib.suppress(Exception):
        nexus.rmdir("/test-data", recursive=True, zone=zone)

    yield zone

    # Post-clean: remove test data created during this module
    with contextlib.suppress(Exception):
        nexus.rmdir("/test-data", recursive=True, zone=zone)


# ---------------------------------------------------------------------------
# Session-scoped benchmark data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def benchmark_data(settings: TestSettings) -> dict[str, list]:
    """Load benchmark files from BENCHMARK_DIR, grouped by extension.

    Returns an empty dict if the benchmark directory doesn't exist.
    This fixture is session-scoped — files are loaded once and shared.
    """
    return load_benchmark_files(settings.benchmark_dir)
