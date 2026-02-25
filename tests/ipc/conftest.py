"""IPC test fixtures — enforcement gate, agent provisioning, cleanup.

Fixture scoping:
    module:  _ipc_available (auto-skip gate)
    function: provisioned_agents (per-test agent pair with cleanup)
"""

from __future__ import annotations

import logging
import uuid

import pytest

from tests.helpers.api_client import NexusClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-scoped enforcement gate
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _ipc_available(nexus: NexusClient) -> None:
    """Skip IPC tests if the IPC subsystem is not available.

    Probes POST /api/v2/ipc/provision with a disposable agent to confirm
    the IPC REST router is mounted and the storage driver is initialized.
    """
    # Try 1: Check features endpoint for IPC brick
    try:
        feat_resp = nexus.features()
        if feat_resp.status_code == 200:
            feat = feat_resp.json()
            enabled = feat.get("enabled_bricks", [])
            if isinstance(enabled, list) and "ipc" not in enabled:
                pytest.skip("Server does not have IPC brick enabled")
    except Exception as exc:
        logger.debug("Features endpoint unavailable (%s), trying probe", exc)

    # Try 2: Probe by provisioning a throwaway agent
    probe_id = f"__probe_{uuid.uuid4().hex[:8]}"
    probe_resp = nexus.ipc_provision(probe_id)
    if not probe_resp.ok:
        error_msg = probe_resp.error.message.lower() if probe_resp.error else ""
        if any(
            kw in error_msg
            for kw in ("not initialized", "not found", "503", "not available")
        ):
            pytest.skip("IPC subsystem not available on this server")


# ---------------------------------------------------------------------------
# Per-test agent provisioning fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def provisioned_agents(
    nexus: NexusClient,
) -> tuple[str, str]:
    """Provision a pair of agents for IPC testing.

    Returns (agent_a, agent_b) IDs. Agents are provisioned before the test
    and their inboxes are available for send/receive operations.
    """
    tag = uuid.uuid4().hex[:8]
    agent_a = f"test-agent-a-{tag}"
    agent_b = f"test-agent-b-{tag}"

    resp_a = nexus.ipc_provision(agent_a)
    assert resp_a.ok, f"Failed to provision agent A ({agent_a}): {resp_a.error}"

    resp_b = nexus.ipc_provision(agent_b)
    assert resp_b.ok, f"Failed to provision agent B ({agent_b}): {resp_b.error}"

    return agent_a, agent_b
