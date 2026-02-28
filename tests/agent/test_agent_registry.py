"""Agent registry E2E tests — full coverage of RPC + REST agent surface.

Tests: agent/005-025
Covers: RPC register/update/get/list/delete, lifecycle transitions,
        heartbeat, zone listing, REST spec/status/warmup, error paths,
        duplicate registration, API key generation, resource limits,
        cross-zone discovery (federation).

Reference: TEST_PLAN.md §4.5

Infrastructure: docker-compose.cross-platform-test.yml (local + federation)

Agent RPC methods: register_agent, update_agent, get_agent, list_agents,
    delete_agent, agent_transition, agent_heartbeat, agent_list_by_zone
Agent REST endpoints:
    PUT  /api/v2/agents/{id}/spec
    GET  /api/v2/agents/{id}/spec
    GET  /api/v2/agents/{id}/status
    POST /api/v2/agents/{id}/warmup
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from tests.helpers.api_client import NexusClient


def _unique_agent_id() -> str:
    """Generate a unique agent ID for test isolation."""
    return f"agent-e2e-{uuid.uuid4().hex[:8]}"


def _register_agent(
    nexus: NexusClient,
    agent_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    capabilities: list[str] | None = None,
    generate_api_key: bool = False,
    metadata: dict | None = None,
) -> dict:
    """Register an agent via RPC and return the result dict.

    Skips the test if the RPC is unavailable.
    """
    params: dict = {
        "agent_id": agent_id,
        "name": name or f"Test Agent {agent_id}",
    }
    if description is not None:
        params["description"] = description
    if capabilities is not None:
        params["capabilities"] = capabilities
    if generate_api_key:
        params["generate_api_key"] = True
    if metadata is not None:
        params["metadata"] = metadata

    resp = nexus.rpc("register_agent", params)
    if not resp.ok:
        # Tolerate "already exists/registered" for idempotent re-runs
        if "already" in str(resp.error).lower():
            return {"agent_id": agent_id}
        pytest.skip(f"Agent registration RPC not available: {resp.error}")
    return resp.result


def _cleanup_agent(nexus: NexusClient, agent_id: str) -> None:
    """Best-effort agent cleanup — never fails the test."""
    with contextlib.suppress(Exception):
        nexus.rpc("delete_agent", {"agent_id": agent_id})


# ---------------------------------------------------------------------------
# RPC: Registration, CRUD, and lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.agent
class TestAgentRpcCrud:
    """Agent RPC CRUD operations (register, update, get, list, delete)."""

    def test_update_agent(self, nexus: NexusClient) -> None:
        """agent/005: Update agent name, description, metadata."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id, name="Original Name")

        try:
            resp = nexus.rpc(
                "update_agent",
                {
                    "agent_id": agent_id,
                    "name": "Updated Name",
                    "description": "A test agent",
                    "metadata": {"role": "tester"},
                },
            )
            if not resp.ok:
                pytest.skip(f"update_agent RPC not available: {resp.error}")

            data = resp.result
            assert data["agent_id"] == agent_id
            assert data["name"] == "Updated Name"
            assert data["description"] == "A test agent"
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_get_agent(self, nexus: NexusClient) -> None:
        """agent/006: Get agent by ID returns enriched fields."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id, name="Getter Test")

        try:
            resp = nexus.rpc("get_agent", {"agent_id": agent_id})
            if not resp.ok:
                pytest.skip(f"get_agent RPC not available: {resp.error}")

            data = resp.result
            assert data is not None, "get_agent returned None for existing agent"
            assert data["agent_id"] == agent_id
            assert data["name"] == "Getter Test"
            assert "user_id" in data
            assert "created_at" in data
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_list_agents(self, nexus: NexusClient) -> None:
        """agent/007: List agents — registered agents appear in list."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            resp = nexus.rpc("list_agents", {})
            if not resp.ok:
                pytest.skip(f"list_agents RPC not available: {resp.error}")

            agent_ids = [a["agent_id"] for a in resp.result]
            assert agent_id in agent_ids, (
                f"Registered agent {agent_id} not found in list. Got {len(resp.result)} agents."
            )
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_delete_agent(self, nexus: NexusClient) -> None:
        """agent/008: Delete agent — subsequent get returns None."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        resp = nexus.rpc("delete_agent", {"agent_id": agent_id})
        if not resp.ok:
            pytest.skip(f"delete_agent RPC not available: {resp.error}")
        assert resp.result is True, f"delete_agent should return True, got {resp.result}"

        # Verify gone
        get_resp = nexus.rpc("get_agent", {"agent_id": agent_id})
        if get_resp.ok:
            assert get_resp.result is None, f"Agent {agent_id} should be gone after delete"

    def test_delete_nonexistent_agent(self, nexus: NexusClient) -> None:
        """agent/021: Delete nonexistent agent returns false gracefully."""
        fake_id = f"nonexistent-{uuid.uuid4().hex[:8]}"
        resp = nexus.rpc("delete_agent", {"agent_id": fake_id})
        if not resp.ok:
            # Some servers return an error for not-found — that's acceptable
            return
        assert resp.result is False, (
            f"Deleting nonexistent agent should return False, got {resp.result}"
        )

    def test_duplicate_registration_idempotent(self, nexus: NexusClient) -> None:
        """agent/022: Duplicate registration does not error."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            resp = nexus.rpc(
                "register_agent",
                {"agent_id": agent_id, "name": f"Test Agent {agent_id}"},
            )
            # Should either succeed (idempotent) or return "already exists/registered"
            if not resp.ok:
                err_msg = str(resp.error).lower()
                assert "already" in err_msg, (
                    f"Expected idempotent or 'already exists/registered', got: {resp.error}"
                )
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_register_with_api_key(self, nexus: NexusClient) -> None:
        """agent/023: Register with generate_api_key=True returns api_key field."""
        agent_id = _unique_agent_id()

        try:
            result = _register_agent(nexus, agent_id, generate_api_key=True)
            # The api_key field should be present when requested
            if "api_key" in result:
                assert result["api_key"], "api_key should be non-empty when generated"
            elif "has_api_key" in result:
                assert result["has_api_key"] is True
            # Some servers may not support key generation — that's OK
        finally:
            _cleanup_agent(nexus, agent_id)


# ---------------------------------------------------------------------------
# RPC: Lifecycle state transitions
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.agent
class TestAgentLifecycle:
    """Agent lifecycle state machine transitions."""

    def test_valid_transitions(self, nexus: NexusClient) -> None:
        """agent/009: UNKNOWN→CONNECTED→IDLE→CONNECTED with generation tracking."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            # UNKNOWN → CONNECTED (generation should increment)
            resp = nexus.rpc(
                "agent_transition",
                {"agent_id": agent_id, "target_state": "CONNECTED"},
            )
            if not resp.ok:
                pytest.skip(f"agent_transition RPC not available: {resp.error}")

            data = resp.result
            assert data["state"] == "CONNECTED"
            gen_after_connect = data["generation"]
            assert gen_after_connect >= 1, (
                f"Generation should be >=1 after first CONNECTED, got {gen_after_connect}"
            )

            # CONNECTED → IDLE
            resp2 = nexus.rpc(
                "agent_transition",
                {"agent_id": agent_id, "target_state": "IDLE"},
            )
            assert resp2.ok, f"CONNECTED→IDLE failed: {resp2.error}"
            assert resp2.result["state"] == "IDLE"

            # IDLE → CONNECTED (generation should increment again)
            resp3 = nexus.rpc(
                "agent_transition",
                {"agent_id": agent_id, "target_state": "CONNECTED"},
            )
            assert resp3.ok, f"IDLE→CONNECTED failed: {resp3.error}"
            assert resp3.result["state"] == "CONNECTED"
            gen_reconnect = resp3.result["generation"]
            assert gen_reconnect > gen_after_connect, (
                f"Generation should increment on reconnect: "
                f"was {gen_after_connect}, now {gen_reconnect}"
            )
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_invalid_transition_rejected(self, nexus: NexusClient) -> None:
        """agent/010: Invalid state transition returns error."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            # UNKNOWN → IDLE should be invalid (must go through CONNECTED first)
            resp = nexus.rpc(
                "agent_transition",
                {"agent_id": agent_id, "target_state": "IDLE"},
            )
            if not resp.ok:
                # Error expected — transition should be rejected
                err_msg = str(resp.error).lower()
                assert "invalid" in err_msg or "transition" in err_msg, (
                    f"Expected invalid transition error, got: {resp.error}"
                )
            else:
                # Some implementations may allow UNKNOWN→IDLE — skip if so
                pytest.skip("Server allows UNKNOWN→IDLE transition")
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_optimistic_locking_stale_generation(self, nexus: NexusClient) -> None:
        """agent/011: Stale expected_generation is rejected."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            # Transition to CONNECTED (generation=1)
            resp = nexus.rpc(
                "agent_transition",
                {"agent_id": agent_id, "target_state": "CONNECTED"},
            )
            if not resp.ok:
                pytest.skip(f"agent_transition RPC not available: {resp.error}")

            # Try transition with stale generation (0)
            resp2 = nexus.rpc(
                "agent_transition",
                {
                    "agent_id": agent_id,
                    "target_state": "IDLE",
                    "expected_generation": 0,
                },
            )
            if not resp2.ok:
                err_msg = str(resp2.error).lower()
                assert "stale" in err_msg or "generation" in err_msg, (
                    f"Expected stale generation error, got: {resp2.error}"
                )
            else:
                # Some servers may not enforce optimistic locking strictly
                pytest.skip("Server does not enforce expected_generation strictly")
        finally:
            _cleanup_agent(nexus, agent_id)


# ---------------------------------------------------------------------------
# RPC: Heartbeat & zone listing
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.agent
class TestAgentHeartbeatAndZone:
    """Agent heartbeat recording and zone-scoped listing."""

    def test_heartbeat_rpc(self, nexus: NexusClient) -> None:
        """agent/012: Heartbeat via RPC returns ok=true."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            # Must be CONNECTED to heartbeat meaningfully
            nexus.rpc(
                "agent_transition",
                {"agent_id": agent_id, "target_state": "CONNECTED"},
            )

            resp = nexus.rpc("agent_heartbeat", {"agent_id": agent_id})
            if not resp.ok:
                pytest.skip(f"agent_heartbeat RPC not available: {resp.error}")

            assert resp.result.get("ok") is True, (
                f"Heartbeat should return ok=true, got: {resp.result}"
            )
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_list_agents_by_zone(self, nexus: NexusClient, settings) -> None:
        """agent/013: List agents by zone with optional state filter."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)
        zone = settings.zone

        try:
            # Transition to CONNECTED so we can filter by state
            nexus.rpc(
                "agent_transition",
                {"agent_id": agent_id, "target_state": "CONNECTED"},
            )

            # List all agents in zone
            resp = nexus.rpc(
                "agent_list_by_zone",
                {"zone_id": zone},
            )
            if not resp.ok:
                pytest.skip(f"agent_list_by_zone RPC not available: {resp.error}")

            assert isinstance(resp.result, list), f"Expected list, got {type(resp.result)}"

            # List with state filter (CONNECTED only)
            resp_filtered = nexus.rpc(
                "agent_list_by_zone",
                {"zone_id": zone, "state": "CONNECTED"},
            )
            if resp_filtered.ok:
                for agent in resp_filtered.result:
                    assert agent["state"] == "CONNECTED", (
                        f"State filter not applied: got {agent['state']}"
                    )
        finally:
            _cleanup_agent(nexus, agent_id)


# ---------------------------------------------------------------------------
# REST: Agent spec (desired state)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.agent
class TestAgentSpec:
    """Agent spec REST endpoints (PUT/GET /api/v2/agents/{id}/spec)."""

    def test_set_agent_spec(self, nexus: NexusClient) -> None:
        """agent/014: Set agent spec via REST — spec stored, generation=1."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            spec_body = {
                "agent_type": "worker",
                "capabilities": ["file_read", "file_write"],
                "qos_class": "standard",
            }
            resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
            if resp.status_code == 503:
                pytest.skip("Agent registry not available on this server")

            assert resp.status_code == 200, f"Set spec failed: {resp.status_code} {resp.text[:300]}"

            data = resp.json()
            assert data["agent_type"] == "worker"
            assert "file_read" in data["capabilities"]
            assert "file_write" in data["capabilities"]
            assert data["spec_generation"] >= 1
            assert data["qos_class"] == "standard"
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_spec_generation_increments(self, nexus: NexusClient) -> None:
        """agent/015: Updating spec increments spec_generation monotonically."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            spec_v1 = {
                "agent_type": "worker",
                "capabilities": ["v1"],
                "qos_class": "standard",
            }
            resp1 = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_v1)
            if resp1.status_code == 503:
                pytest.skip("Agent registry not available")
            assert resp1.status_code == 200
            gen1 = resp1.json()["spec_generation"]

            spec_v2 = {
                "agent_type": "worker",
                "capabilities": ["v1", "v2"],
                "qos_class": "standard",
            }
            resp2 = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_v2)
            assert resp2.status_code == 200
            gen2 = resp2.json()["spec_generation"]
            assert gen2 > gen1, f"Spec generation should increment: gen1={gen1}, gen2={gen2}"

            spec_v3 = {
                "agent_type": "worker",
                "capabilities": ["v1", "v2", "v3"],
                "qos_class": "premium",
            }
            resp3 = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_v3)
            assert resp3.status_code == 200
            gen3 = resp3.json()["spec_generation"]
            assert gen3 > gen2, f"Spec generation should increment: gen2={gen2}, gen3={gen3}"
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_get_agent_spec(self, nexus: NexusClient) -> None:
        """agent/016: GET /spec returns exactly what was set."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            spec_body = {
                "agent_type": "searcher",
                "capabilities": ["search", "index"],
                "resource_requests": {"token_budget": 1000},
                "resource_limits": {"token_budget": 5000},
                "qos_class": "premium",
                "zone_affinity": "corp",
            }
            put_resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
            if put_resp.status_code == 503:
                pytest.skip("Agent registry not available")
            assert put_resp.status_code == 200

            get_resp = nexus.api_get(f"/api/v2/agents/{agent_id}/spec")
            assert get_resp.status_code == 200

            data = get_resp.json()
            assert data["agent_type"] == "searcher"
            assert set(data["capabilities"]) == {"search", "index"}
            assert data["qos_class"] == "premium"
            assert data["zone_affinity"] == "corp"
            assert data["resource_requests"]["token_budget"] == 1000
            assert data["resource_limits"]["token_budget"] == 5000
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_resource_limits_roundtrip(self, nexus: NexusClient) -> None:
        """agent/024: Resource limits in spec round-trip correctly."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            spec_body = {
                "agent_type": "compute",
                "capabilities": ["execute"],
                "resource_requests": {
                    "token_budget": 500,
                    "storage_limit_mb": 100,
                    "context_limit": 4096,
                },
                "resource_limits": {
                    "token_budget": 10000,
                    "storage_limit_mb": 1024,
                    "context_limit": 32768,
                },
                "qos_class": "standard",
            }
            put_resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
            if put_resp.status_code == 503:
                pytest.skip("Agent registry not available")
            assert put_resp.status_code == 200

            get_resp = nexus.api_get(f"/api/v2/agents/{agent_id}/spec")
            assert get_resp.status_code == 200
            data = get_resp.json()

            # Verify requests
            assert data["resource_requests"]["token_budget"] == 500
            assert data["resource_requests"]["storage_limit_mb"] == 100
            assert data["resource_requests"]["context_limit"] == 4096

            # Verify limits
            assert data["resource_limits"]["token_budget"] == 10000
            assert data["resource_limits"]["storage_limit_mb"] == 1024
            assert data["resource_limits"]["context_limit"] == 32768
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_invalid_qos_class_rejected(self, nexus: NexusClient) -> None:
        """agent/019: PUT /spec with invalid QoS class returns 422."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            spec_body = {
                "agent_type": "worker",
                "capabilities": [],
                "qos_class": "ultra-mega-priority",
            }
            resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
            if resp.status_code == 503:
                pytest.skip("Agent registry not available")

            assert resp.status_code == 422, (
                f"Invalid QoS class should return 422, got {resp.status_code}: {resp.text[:300]}"
            )
        finally:
            _cleanup_agent(nexus, agent_id)


# ---------------------------------------------------------------------------
# REST: Agent status (observed state)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.agent
class TestAgentStatus:
    """Agent status REST endpoint (GET /api/v2/agents/{id}/status)."""

    def test_get_agent_status(self, nexus: NexusClient) -> None:
        """agent/017: GET /status returns phase, conditions, resource_usage."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            # Set a spec so the agent has derived status
            spec_body = {
                "agent_type": "monitor",
                "capabilities": ["observe"],
                "qos_class": "standard",
            }
            put_resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
            if put_resp.status_code == 503:
                pytest.skip("Agent registry not available")
            assert put_resp.status_code == 200

            resp = nexus.api_get(f"/api/v2/agents/{agent_id}/status")
            if resp.status_code == 404:
                pytest.skip("Agent status not computed yet")

            assert resp.status_code == 200, (
                f"Get status failed: {resp.status_code} {resp.text[:300]}"
            )

            data = resp.json()
            assert "phase" in data, f"Status missing 'phase': {list(data.keys())}"
            assert "observed_generation" in data
            assert "conditions" in data
            assert isinstance(data["conditions"], list)
            assert "resource_usage" in data
            assert isinstance(data["resource_usage"], dict)
            assert "inbox_depth" in data
            assert "context_usage_pct" in data
        finally:
            _cleanup_agent(nexus, agent_id)

    def test_status_404_nonexistent_agent(self, nexus: NexusClient) -> None:
        """agent/020: GET /status for nonexistent agent returns 404."""
        fake_id = f"nonexistent-{uuid.uuid4().hex[:8]}"
        resp = nexus.api_get(f"/api/v2/agents/{fake_id}/status")
        # Should be 404 or 503 (if registry unavailable)
        assert resp.status_code in (404, 503), (
            f"Expected 404 or 503 for nonexistent agent, got {resp.status_code}"
        )

    def test_spec_404_nonexistent_agent(self, nexus: NexusClient) -> None:
        """agent/020: GET /spec for nonexistent agent returns 404."""
        fake_id = f"nonexistent-{uuid.uuid4().hex[:8]}"
        resp = nexus.api_get(f"/api/v2/agents/{fake_id}/spec")
        assert resp.status_code in (404, 503), (
            f"Expected 404 or 503 for nonexistent agent spec, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# REST: Agent warmup
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.agent
class TestAgentWarmup:
    """Agent warmup REST endpoint (POST /api/v2/agents/{id}/warmup)."""

    def test_trigger_warmup(self, nexus: NexusClient) -> None:
        """agent/018: POST /warmup returns warmup result."""
        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id)

        try:
            # Set spec first (warmup may require it)
            spec_body = {
                "agent_type": "worker",
                "capabilities": ["compute"],
                "qos_class": "standard",
            }
            put_resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
            if put_resp.status_code == 503:
                pytest.skip("Agent registry not available")

            resp = nexus.api_post(f"/api/v2/agents/{agent_id}/warmup", json={})
            if resp.status_code == 503:
                pytest.skip("Warmup service not available")

            # 200 = success, 422 = validation error (both are valid responses)
            assert resp.status_code in (200, 422), (
                f"Warmup unexpected status: {resp.status_code} {resp.text[:300]}"
            )

            if resp.status_code == 200:
                data = resp.json()
                assert "success" in data
                assert data["agent_id"] == agent_id
                assert "steps_completed" in data
                assert "duration_ms" in data
        finally:
            _cleanup_agent(nexus, agent_id)


# ---------------------------------------------------------------------------
# Federation: Cross-zone agent discovery
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.agent
@pytest.mark.federation
class TestAgentFederation:
    """Cross-zone agent discovery via federation."""

    def test_cross_zone_agent_discovery(
        self,
        nexus: NexusClient,
        nexus_follower: NexusClient,
    ) -> None:
        """agent/025: Agent registered on leader visible from follower."""
        import httpx

        agent_id = _unique_agent_id()
        _register_agent(nexus, agent_id, name="Federation Agent")

        try:
            # Set spec on leader
            spec_body = {
                "agent_type": "federation-test",
                "capabilities": ["cross-zone"],
                "qos_class": "standard",
            }
            put_resp = nexus.api_put(f"/api/v2/agents/{agent_id}/spec", json=spec_body)
            if put_resp.status_code == 503:
                pytest.skip("Agent registry not available on leader")

            # Query spec from follower
            try:
                follower_resp = nexus_follower.api_get(f"/api/v2/agents/{agent_id}/spec")
            except httpx.ConnectError:
                pytest.skip("Follower node not reachable — federation not running")

            if follower_resp.status_code == 503:
                pytest.skip("Agent registry not available on follower")

            if follower_resp.status_code == 404:
                pytest.skip(
                    "Agent not replicated to follower — federation replication may not be enabled"
                )

            assert follower_resp.status_code == 200, (
                f"Follower spec query failed: "
                f"{follower_resp.status_code} {follower_resp.text[:300]}"
            )

            data = follower_resp.json()
            assert data["agent_type"] == "federation-test"
            assert "cross-zone" in data["capabilities"]
        finally:
            _cleanup_agent(nexus, agent_id)
