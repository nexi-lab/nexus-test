"""Docker orchestration and chaos injection helpers for E2E tests.

Provides utilities for:
    - Starting/stopping docker-compose clusters
    - Health-check polling with retries
    - Failover testing (stop/start individual nodes)
    - Network partition simulation (disconnect/reconnect)

All functions return immutable DockerResult objects matching the CliResult pattern.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from tenacity import retry, stop_after_delay, wait_exponential

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NETWORK_NAME = "nexus-test-network"
CONTAINER_NODE_1 = "nexus-node-1"
CONTAINER_NODE_2 = "nexus-node-2"
CONTAINER_WITNESS = "nexus-witness"


# ---------------------------------------------------------------------------
# Result model (matches CliResult pattern)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DockerResult:
    """Result from a docker subprocess invocation."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


# ---------------------------------------------------------------------------
# Compose file path helper
# ---------------------------------------------------------------------------


def compose_file_path(nexus_repo_dir: str, variant: str = "demo") -> Path:
    """Build the path to a nexus docker-compose file.

    Args:
        nexus_repo_dir: Path to the nexus repository (e.g. ~/nexus).
        variant: Compose variant name (e.g. "demo", "cross-platform-test").

    Returns:
        Resolved Path to the compose file.
    """
    repo = Path(nexus_repo_dir).expanduser().resolve()
    return repo / "dockerfiles" / f"docker-compose.{variant}.yml"


# ---------------------------------------------------------------------------
# Cluster lifecycle
# ---------------------------------------------------------------------------


def _run_docker(*args: str, timeout: float = 120.0) -> DockerResult:
    """Run a docker/docker-compose command and return a DockerResult."""
    cmd = list(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return DockerResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired:
        return DockerResult(exit_code=-1, stdout="", stderr=f"Timeout after {timeout}s")
    except FileNotFoundError:
        return DockerResult(exit_code=-1, stdout="", stderr="docker not found in PATH")


def start_cluster(compose_file: str | Path) -> DockerResult:
    """Start the cluster using docker compose up -d.

    Args:
        compose_file: Path to the docker-compose YAML file.

    Returns:
        DockerResult from the compose up command.
    """
    return _run_docker(
        "docker", "compose", "-f", str(compose_file), "up", "-d", "--wait",
        timeout=300.0,
    )


def stop_cluster(compose_file: str | Path) -> DockerResult:
    """Stop the cluster using docker compose down.

    Args:
        compose_file: Path to the docker-compose YAML file.

    Returns:
        DockerResult from the compose down command.
    """
    return _run_docker(
        "docker", "compose", "-f", str(compose_file), "down", "--timeout", "30",
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def wait_for_healthy(
    url: str,
    api_key: str,
    *,
    timeout: float = 120.0,
) -> None:
    """Block until a node's /health endpoint returns OK.

    Uses tenacity for exponential-backoff retries.

    Args:
        url: Base URL of the node (e.g. http://localhost:2026).
        api_key: API key for authentication.
        timeout: Maximum seconds to wait.

    Raises:
        Exception: If the node is not healthy within the timeout.
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


# ---------------------------------------------------------------------------
# Node lifecycle (failover testing)
# ---------------------------------------------------------------------------


def stop_node(container: str) -> DockerResult:
    """Stop a single docker container (simulate node failure).

    Args:
        container: Container name (e.g. CONTAINER_NODE_1).
    """
    return _run_docker("docker", "stop", container)


def start_node(container: str) -> DockerResult:
    """Start a previously stopped docker container (simulate recovery).

    Args:
        container: Container name (e.g. CONTAINER_NODE_1).
    """
    return _run_docker("docker", "start", container)


# ---------------------------------------------------------------------------
# Network partition (chaos testing)
# ---------------------------------------------------------------------------


def disconnect_node(container: str, network: str = NETWORK_NAME) -> DockerResult:
    """Disconnect a container from the network (simulate network partition).

    Args:
        container: Container name to disconnect.
        network: Docker network name.
    """
    return _run_docker("docker", "network", "disconnect", network, container)


def reconnect_node(container: str, network: str = NETWORK_NAME) -> DockerResult:
    """Reconnect a container to the network (heal partition).

    Args:
        container: Container name to reconnect.
        network: Docker network name.
    """
    return _run_docker("docker", "network", "connect", network, container)
