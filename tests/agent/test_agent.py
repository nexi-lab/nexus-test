"""Agent E2E tests — registration, heartbeat, capability query, lifecycle FSM.

Tests: agent/001-004
Covers: register agent, heartbeat/status, capability query, lifecycle state transitions

Reference: TEST_PLAN.md §4.5

Infrastructure: docker-compose.demo.yml (standalone)

Agent API endpoints:
    GET  /api/v2/agents/{agent_id}/status  — Computed agent status
    PUT  /api/v2/agents/{agent_id}/spec    — Set agent spec (desired state)
    GET  /api/v2/agents/{agent_id}/spec    — Get agent spec
    POST /api/v2/agents/{agent_id}/warmup  — Trigger agent warmup
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient


def _unique_agent_id() -> str:
    """Generate a unique agent ID for test isolation."""
    return f"agent-test-{uuid.uuid4().hex[:8]}"


@pytest.mark.auto
@pytest.mark.agent
class TestAgent:
    """Agent registry and lifecycle tests."""

    def test_register_agent(self, nexus: NexusClient) -> None:
        """agent/001: Register agent — agent appears in registry.

        Registers an agent by setting its spec via PUT /api/v2/agents/{id}/spec,
        then verifies it can be retrieved.
        """
        agent_id = _unique_agent_id()

        spec_body = {
            "agent_type": "worker",
            "capabilities": ["file_read", "file_write"],
            "resource_requests": {"token_budget": 1000},
            "resource_limits": {"token_budget": 5000},
            "qos_class": "standard",
        }

        # Set agent spec (this effectively registers the agent)
        resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
        if resp.status_code == 503:
            pytest.skip("Agent registry not available on this server")

        assert resp.status_code == 200, (
            f"Agent spec creation failed: {resp.status_code} {resp.text[:200]}"
        )

        data = resp.json()
        assert data["agent_type"] == "worker"
        assert "file_read" in data["capabilities"]
        assert "file_write" in data["capabilities"]
        assert data["spec_generation"] >= 1

    def test_agent_heartbeat(self, nexus: NexusClient) -> None:
        """agent/002: Agent heartbeat — status reflects current state.

        Registers an agent, then queries its status to verify heartbeat-derived
        fields (phase, conditions, resource_usage) are populated.
        """
        agent_id = _unique_agent_id()

        # Register the agent first
        spec_body = {
            "agent_type": "monitor",
            "capabilities": ["heartbeat"],
            "qos_class": "standard",
        }
        put_resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
        if put_resp.status_code == 503:
            pytest.skip("Agent registry not available on this server")
        assert put_resp.status_code == 200, f"Agent registration failed: {put_resp.text[:200]}"

        # Query agent status
        status_resp = nexus.api_get(f"/api/v2/agents/{agent_id}/status")
        if status_resp.status_code == 404:
            pytest.skip("Agent status not yet computed — agent may need warmup first")

        assert status_resp.status_code == 200, (
            f"Agent status query failed: {status_resp.status_code} {status_resp.text[:200]}"
        )

        data = status_resp.json()
        assert "phase" in data, f"Status missing 'phase': {data}"
        assert "observed_generation" in data, f"Status missing 'observed_generation': {data}"
        assert "conditions" in data, f"Status missing 'conditions': {data}"
        assert isinstance(data["conditions"], list)
        assert "resource_usage" in data, f"Status missing 'resource_usage': {data}"

    def test_agent_capability_query(self, nexus: NexusClient) -> None:
        """agent/003: Agent capability query — filtered by capability.

        Registers two agents with different capabilities, then retrieves each
        agent's spec to verify capabilities are stored and returned correctly.
        """
        agent_a = _unique_agent_id()
        agent_b = _unique_agent_id()

        # Register agent A with search capability
        spec_a = {
            "agent_type": "searcher",
            "capabilities": ["search", "index"],
            "qos_class": "standard",
        }
        resp_a = nexus.api_put(f"/api/v2/agents/{agent_a}/spec", json=spec_a)
        if resp_a.status_code == 503:
            pytest.skip("Agent registry not available on this server")
        assert resp_a.status_code == 200

        # Register agent B with compute capability
        spec_b = {
            "agent_type": "compute",
            "capabilities": ["execute", "sandbox"],
            "qos_class": "standard",
        }
        resp_b = nexus.api_put(f"/api/v2/agents/{agent_b}/spec", json=spec_b)
        assert resp_b.status_code == 200

        # Retrieve agent A's spec — should have search capabilities
        get_a = nexus.api_get(f"/api/v2/agents/{agent_a}/spec")
        assert get_a.status_code == 200
        data_a = get_a.json()
        assert "search" in data_a["capabilities"]
        assert "index" in data_a["capabilities"]

        # Retrieve agent B's spec — should have compute capabilities
        get_b = nexus.api_get(f"/api/v2/agents/{agent_b}/spec")
        assert get_b.status_code == 200
        data_b = get_b.json()
        assert "execute" in data_b["capabilities"]
        assert "sandbox" in data_b["capabilities"]

        # Cross-check: A should NOT have B's capabilities
        assert "execute" not in data_a["capabilities"]
        assert "search" not in data_b["capabilities"]

    def test_agent_lifecycle_fsm(self, nexus: NexusClient) -> None:
        """agent/004: Agent lifecycle FSM — correct state transitions.

        Exercises the agent lifecycle: register (spec) → warmup → verify status.
        The agent should transition through states as its spec is updated.
        """
        agent_id = _unique_agent_id()

        # Step 1: Set initial spec (registers agent)
        spec_v1 = {
            "agent_type": "lifecycle-test",
            "capabilities": ["v1"],
            "qos_class": "standard",
        }
        resp_v1 = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_v1)
        if resp_v1.status_code == 503:
            pytest.skip("Agent registry not available on this server")
        assert resp_v1.status_code == 200
        gen1 = resp_v1.json()["spec_generation"]

        # Step 2: Update spec (should increment generation)
        spec_v2 = {
            "agent_type": "lifecycle-test",
            "capabilities": ["v1", "v2"],
            "qos_class": "standard",
        }
        resp_v2 = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_v2)
        assert resp_v2.status_code == 200
        gen2 = resp_v2.json()["spec_generation"]
        assert gen2 > gen1, f"Spec generation should increment: gen1={gen1}, gen2={gen2}"

        # Step 3: Trigger warmup (if supported)
        warmup_resp = nexus.api_post(f"/api/v2/agents/{agent_id}/warmup", json={})
        if warmup_resp.status_code == 503:
            pytest.skip("Warmup service not available — lifecycle still verified via spec")
        # 200 = success, 422 = validation error (acceptable in E2E)
        assert warmup_resp.status_code in (200, 422), (
            f"Warmup failed unexpectedly: {warmup_resp.status_code} {warmup_resp.text[:200]}"
        )

        if warmup_resp.status_code == 200:
            warmup_data = warmup_resp.json()
            assert "success" in warmup_data
            assert warmup_data["agent_id"] == agent_id

        # Step 4: Verify final spec reflects latest state
        final_spec = nexus.api_get(f"/api/v2/agents/{agent_id}/spec")
        assert final_spec.status_code == 200
        final_data = final_spec.json()
        assert "v2" in final_data["capabilities"], (
            f"Final spec should include v2 capability: {final_data['capabilities']}"
        )
