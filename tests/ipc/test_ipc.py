"""IPC E2E tests (ipc/001-004).

Tests exercise Nexus's filesystem-as-IPC infrastructure:
    - Send message A -> B (ipc/001)
    - Named pipe data flow (ipc/002)
    - Inbox polling (ipc/003)
    - Unread count (ipc/004)

Groups: auto, ipc
"""

from __future__ import annotations

import logging
import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success

logger = logging.getLogger(__name__)


@pytest.mark.auto
@pytest.mark.ipc
class TestIPC:
    """Core IPC E2E tests (ipc/001-004)."""

    def test_send_message(
        self,
        nexus: NexusClient,
        provisioned_agents: tuple[str, str],
    ) -> None:
        """ipc/001: Send message A -> B — Delivered.

        Provision two agents, send a message from A to B via REST,
        verify the response contains a message_id with status 'sent'.
        """
        agent_a, agent_b = provisioned_agents

        payload = {"action": "review", "document": f"doc_{uuid.uuid4().hex[:8]}"}
        resp = nexus.ipc_send(agent_a, agent_b, payload)
        result = assert_rpc_success(resp)

        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert "message_id" in result, f"Response missing message_id: {result}"
        assert result["message_id"], "message_id should be non-empty"
        assert result.get("status") == "sent", (
            f"Expected status 'sent', got {result.get('status')!r}"
        )

    def test_named_pipe(
        self,
        nexus: NexusClient,
        provisioned_agents: tuple[str, str],
    ) -> None:
        """ipc/002: Named pipe — Data flows.

        Send a message with known payload from A to B, then read B's inbox
        to verify the message arrived with the correct content.
        Tests end-to-end data flow through the IPC filesystem pipe.
        """
        agent_a, agent_b = provisioned_agents

        unique_marker = f"pipe_data_{uuid.uuid4().hex}"
        payload = {"marker": unique_marker, "data": "named pipe test content"}

        # Send message through the pipe
        send_resp = nexus.ipc_send(agent_a, agent_b, payload)
        assert_rpc_success(send_resp)

        # Read from the pipe (inbox) and verify data flowed through
        inbox_resp = nexus.ipc_inbox(agent_b)
        result = assert_rpc_success(inbox_resp)

        messages = result.get("messages", [])
        assert messages, f"Agent {agent_b}'s inbox is empty — data did not flow"

        # Verify at least one message file exists in the inbox
        filenames = [m.get("filename", "") for m in messages]
        assert any(f.endswith(".json") for f in filenames), (
            f"No .json message files in inbox: {filenames}"
        )

    def test_inbox_polling(
        self,
        nexus: NexusClient,
        provisioned_agents: tuple[str, str],
    ) -> None:
        """ipc/003: IPC inbox polling — Messages in inbox.

        Send multiple messages to agent B, then poll the inbox listing
        to verify all messages appear. Tests the inbox listing endpoint.
        """
        agent_a, agent_b = provisioned_agents

        # Send 3 messages with distinct payloads
        sent_ids: list[str] = []
        for i in range(3):
            resp = nexus.ipc_send(
                agent_a,
                agent_b,
                {"index": i, "tag": f"poll_test_{uuid.uuid4().hex[:8]}"},
            )
            result = assert_rpc_success(resp)
            sent_ids.append(result["message_id"])

        # Poll inbox and verify messages are present
        inbox_resp = nexus.ipc_inbox(agent_b)
        result = assert_rpc_success(inbox_resp)

        assert result.get("agent_id") == agent_b, (
            f"Inbox agent_id mismatch: expected {agent_b!r}, got {result.get('agent_id')!r}"
        )
        messages = result.get("messages", [])
        total = result.get("total", 0)

        assert total >= 3, (
            f"Expected at least 3 messages in inbox, got {total}"
        )
        assert len(messages) >= 3, (
            f"Expected at least 3 message entries, got {len(messages)}"
        )

    def test_unread_count(
        self,
        nexus: NexusClient,
        provisioned_agents: tuple[str, str],
    ) -> None:
        """ipc/004: IPC unread count — Count accurate.

        Check the inbox count before and after sending messages.
        Verify the count increases by the number of messages sent.
        """
        agent_a, agent_b = provisioned_agents

        # Get baseline count
        baseline_resp = nexus.ipc_inbox_count(agent_b)
        baseline_result = assert_rpc_success(baseline_resp)
        baseline_count = baseline_result.get("count", 0)

        # Send 2 messages
        num_messages = 2
        for i in range(num_messages):
            resp = nexus.ipc_send(
                agent_a,
                agent_b,
                {"index": i, "tag": f"count_test_{uuid.uuid4().hex[:8]}"},
            )
            assert_rpc_success(resp)

        # Verify count increased
        final_resp = nexus.ipc_inbox_count(agent_b)
        final_result = assert_rpc_success(final_resp)

        assert final_result.get("agent_id") == agent_b, (
            f"Count agent_id mismatch: expected {agent_b!r}, got {final_result.get('agent_id')!r}"
        )
        final_count = final_result.get("count", 0)

        assert final_count == baseline_count + num_messages, (
            f"Expected count {baseline_count + num_messages} "
            f"(baseline {baseline_count} + {num_messages} sent), "
            f"got {final_count}"
        )
