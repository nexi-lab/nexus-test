"""LLM module conftest — fixtures for LLM E2E tests.

Provides:
    - LLMClient: Thin wrapper around NexusClient for LLM JSON-RPC calls
    - llm: Parametrized LLMClient fixture (local port 10200, remote port 10201)
    - llm_model: Configurable LLM model name (via NEXUS_TEST_LLM_MODEL)
    - herb_qa_data: HERB Q&A benchmark samples for RAG pipeline tests
    - llm_test_file: Seed file in nexus for LLM to read

Ports (configurable via env):
    NEXUS_TEST_LLM_LOCAL_PORT   = 10200  (local nexus instance)
    NEXUS_TEST_LLM_REMOTE_PORT  = 10201  (remote nexus instance)
    NEXUS_TEST_LLM_DB_PORT      = 10202  (database backend)
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient, RpcResponse


# ---------------------------------------------------------------------------
# LLMClient — thin wrapper for LLM JSON-RPC methods
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMClient:
    """LLM API client wrapping NexusClient JSON-RPC calls.

    All methods return RpcResponse for flexible assertion.
    LLM methods are exposed via JSON-RPC at /api/nfs/{method}.
    """

    nexus: NexusClient
    default_model: str = "gpt-4o-mini"

    # --- Simple completion ---

    def llm_read(
        self,
        path: str,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 1000,
        use_search: bool = False,
        search_mode: str = "semantic",
    ) -> RpcResponse:
        """llm_read — simple Q&A returning answer string."""
        return self.nexus.rpc(
            "llm_read",
            {
                "path": path,
                "prompt": prompt,
                "model": model or self.default_model,
                "max_tokens": max_tokens,
                "use_search": use_search,
                "search_mode": search_mode,
            },
        )

    # --- Detailed completion (with citations, tokens, cost) ---

    def llm_read_detailed(
        self,
        path: str,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 1000,
        use_search: bool = False,
        search_mode: str = "semantic",
        search_limit: int = 10,
        include_citations: bool = True,
    ) -> RpcResponse:
        """llm_read_detailed — returns DocumentReadResult with metadata."""
        return self.nexus.rpc(
            "llm_read_detailed",
            {
                "path": path,
                "prompt": prompt,
                "model": model or self.default_model,
                "max_tokens": max_tokens,
                "use_search": use_search,
                "search_mode": search_mode,
                "search_limit": search_limit,
                "include_citations": include_citations,
            },
        )

    # --- Streaming completion ---

    def llm_read_stream(
        self,
        path: str,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 1000,
        use_search: bool = False,
    ) -> RpcResponse:
        """llm_read_stream — streaming response (collected via JSON-RPC)."""
        return self.nexus.rpc(
            "llm_read_stream",
            {
                "path": path,
                "prompt": prompt,
                "model": model or self.default_model,
                "max_tokens": max_tokens,
                "use_search": use_search,
            },
        )

    # --- RAG completion (search-enabled) ---

    def llm_read_with_rag(
        self,
        path: str,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 2000,
        search_mode: str = "hybrid",
        search_limit: int = 20,
    ) -> RpcResponse:
        """llm_read_detailed with search enabled — for RAG pipeline tests."""
        return self.nexus.rpc(
            "llm_read_detailed",
            {
                "path": path,
                "prompt": prompt,
                "model": model or self.default_model,
                "max_tokens": max_tokens,
                "use_search": True,
                "search_mode": search_mode,
                "search_limit": search_limit,
                "include_citations": True,
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_llm_client(
    base_url: str, api_key: str, *, timeout: float = 120.0, default_model: str = "gpt-4o-mini"
) -> tuple[httpx.Client, LLMClient]:
    """Create an httpx.Client + LLMClient for a given base URL."""
    http = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(timeout, connect=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    nexus = NexusClient(http=http, base_url=base_url, api_key=api_key)
    return http, LLMClient(nexus=nexus, default_model=default_model)


def _load_herb_samples(herb_dir: str, sample_size: int) -> list[dict[str, Any]]:
    """Load HERB Q&A benchmark samples from answerable.jsonl."""
    qa_path = Path(herb_dir) / "qa" / "answerable.jsonl"
    if not qa_path.exists():
        return []

    samples: list[dict[str, Any]] = []
    with qa_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))
            if len(samples) >= sample_size:
                break
    return samples


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _llm_local_port() -> int:
    return int(os.getenv("NEXUS_TEST_LLM_LOCAL_PORT", "10200"))


@pytest.fixture(scope="session")
def _llm_remote_port() -> int:
    return int(os.getenv("NEXUS_TEST_LLM_REMOTE_PORT", "10201"))


@pytest.fixture(scope="session")
def _llm_db_port() -> int:
    return int(os.getenv("NEXUS_TEST_LLM_DB_PORT", "10202"))


@pytest.fixture(scope="session")
def llm_model(settings: TestSettings) -> str:
    """Configured LLM model name (from NEXUS_TEST_LLM_MODEL, default gpt-4o-mini)."""
    return settings.llm_model


def _llm_params() -> list[str]:
    """Build param list: always include 'local'; add 'remote' only when its
    port env-var is explicitly set so we don't pollute the report with 25
    expected skips when no remote server is running."""
    params = ["local"]
    if os.getenv("NEXUS_TEST_LLM_REMOTE_PORT"):
        params.append("remote")
    return params


@pytest.fixture(
    scope="session",
    params=_llm_params(),
)
def llm(
    request: pytest.FixtureRequest,
    settings: TestSettings,
    _llm_local_port: int,
    _llm_remote_port: int,
) -> Generator[LLMClient]:
    """Session-scoped LLMClient parametrized for local (and optionally remote).

    - local:  http://localhost:{NEXUS_TEST_LLM_LOCAL_PORT}  (default 10200)
    - remote: http://localhost:{NEXUS_TEST_LLM_REMOTE_PORT} (only when env-var set)

    Skips automatically if the target server is unreachable.
    """
    if request.param == "local":
        base_url = f"http://localhost:{_llm_local_port}"
    else:
        base_url = f"http://localhost:{_llm_remote_port}"

    http, client = _build_llm_client(
        base_url,
        settings.api_key,
        timeout=settings.request_timeout,
        default_model=settings.llm_model,
    )

    # Health check — skip if server is not reachable
    try:
        resp = http.get("/health", timeout=5.0)
        resp.raise_for_status()
    except Exception as exc:
        http.close()
        pytest.skip(
            f"LLM server not reachable at {base_url} ({request.param}): {exc}"
        )

    yield client

    http.close()


@pytest.fixture(scope="session")
def herb_qa_data(settings: TestSettings) -> list[dict[str, Any]]:
    """Load HERB Q&A benchmark samples (session-scoped).

    Returns up to settings.herb_sample_size samples from answerable.jsonl.
    Returns empty list if HERB data is unavailable.
    """
    return _load_herb_samples(settings.herb_benchmark_dir, settings.herb_sample_size)


@pytest.fixture
def llm_test_file(llm: LLMClient) -> Generator[str]:
    """Create a temporary test file in nexus for LLM to read.

    Seeds the file with known content so tests can verify LLM responses
    are grounded in the document.
    """
    file_id = uuid.uuid4().hex[:8]
    path = f"/test-llm/{file_id}/sample.md"
    content = (
        "# Nexus Architecture Overview\n\n"
        "Nexus is an AI-native distributed filesystem.\n"
        "It supports multi-zone federation with Raft consensus.\n"
        "Key features include:\n"
        "- Content-addressable storage (CAS)\n"
        "- ReBAC permission model\n"
        "- Semantic search with BM25S and pgvector\n"
        "- LLM-powered document reading\n"
        "- TigerBeetle-based payment system\n\n"
        "The system uses a brick architecture where each brick\n"
        "provides a self-contained service (search, memory, pay, llm).\n"
    )
    write_resp = llm.nexus.write_file(path, content)
    if not write_resp.ok:
        pytest.skip(f"Could not seed test file: {write_resp.error}")

    yield path

    # Cleanup
    llm.nexus.delete_file(path)


@pytest.fixture
def llm_test_content() -> str:
    """Return the known content of the seeded test file.

    Used by tests to verify LLM responses reference the source material.
    """
    return (
        "Nexus is an AI-native distributed filesystem. "
        "It supports multi-zone federation with Raft consensus."
    )
