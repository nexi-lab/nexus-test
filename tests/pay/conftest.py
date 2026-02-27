"""Pay module conftest — fixtures for payment E2E tests.

Provides:
    - PayClient: Thin wrapper around NexusClient for /api/v2/pay/ REST calls
    - pay: Parametrized PayClient fixture (local port 10100, remote port 10101)
    - pay_agent_id / pay_recipient_id: Unique agent IDs per test
    - pay_zone: Zone ID for pay tests

Ports (configurable via env):
    NEXUS_TEST_PAY_LOCAL_PORT   = 10100  (local nexus instance)
    NEXUS_TEST_PAY_REMOTE_PORT  = 10101  (remote nexus instance)
    NEXUS_TEST_PAY_DB_PORT      = 10102  (database backend)
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient


# ---------------------------------------------------------------------------
# PayClient — thin wrapper for /api/v2/pay/ endpoints
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayClient:
    """Payment API client wrapping NexusClient REST calls.

    All methods return raw httpx.Response for flexible assertion.
    Amounts are decimal strings (up to 6 decimal places).
    """

    nexus: NexusClient

    # --- Balance ---

    def get_balance(self, *, agent_id: str | None = None) -> httpx.Response:
        """GET /api/v2/pay/balance"""
        headers = _agent_header(agent_id)
        return self.nexus.http.get("/api/v2/pay/balance", headers=headers)

    def can_afford(
        self, amount: str, *, agent_id: str | None = None
    ) -> httpx.Response:
        """GET /api/v2/pay/can-afford?amount=..."""
        headers = _agent_header(agent_id)
        return self.nexus.http.get(
            "/api/v2/pay/can-afford",
            params={"amount": amount},
            headers=headers,
        )

    # --- Transfers ---

    def transfer(
        self,
        to: str,
        amount: str,
        *,
        memo: str = "",
        idempotency_key: str | None = None,
        method: str = "auto",
        agent_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v2/pay/transfer"""
        body: dict[str, Any] = {
            "to": to,
            "amount": amount,
            "memo": memo,
            "method": method,
        }
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        headers = _agent_header(agent_id)
        return self.nexus.http.post(
            "/api/v2/pay/transfer", json=body, headers=headers
        )

    def transfer_batch(
        self,
        transfers: list[dict[str, str]],
        *,
        agent_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v2/pay/transfer/batch"""
        headers = _agent_header(agent_id)
        return self.nexus.http.post(
            "/api/v2/pay/transfer/batch",
            json={"transfers": transfers},
            headers=headers,
        )

    # --- Reservations ---

    def reserve(
        self,
        amount: str,
        *,
        timeout: int = 300,
        purpose: str = "test",
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v2/pay/reserve"""
        body: dict[str, Any] = {
            "amount": amount,
            "timeout": timeout,
            "purpose": purpose,
        }
        if task_id is not None:
            body["task_id"] = task_id
        headers = _agent_header(agent_id)
        return self.nexus.http.post(
            "/api/v2/pay/reserve", json=body, headers=headers
        )

    def commit_reservation(
        self,
        reservation_id: str,
        *,
        actual_amount: str | None = None,
        agent_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v2/pay/reserve/{id}/commit"""
        body: dict[str, Any] = {}
        if actual_amount is not None:
            body["actual_amount"] = actual_amount
        headers = _agent_header(agent_id)
        return self.nexus.http.post(
            f"/api/v2/pay/reserve/{reservation_id}/commit",
            json=body,
            headers=headers,
        )

    def release_reservation(
        self, reservation_id: str, *, agent_id: str | None = None
    ) -> httpx.Response:
        """POST /api/v2/pay/reserve/{id}/release"""
        headers = _agent_header(agent_id)
        return self.nexus.http.post(
            f"/api/v2/pay/reserve/{reservation_id}/release", headers=headers
        )

    # --- Metering ---

    def meter(
        self,
        amount: str,
        *,
        event_type: str = "api_call",
        agent_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v2/pay/meter"""
        headers = _agent_header(agent_id)
        return self.nexus.http.post(
            "/api/v2/pay/meter",
            json={"amount": amount, "event_type": event_type},
            headers=headers,
        )

    # --- Budget & Policies ---

    def get_budget(self, *, agent_id: str | None = None) -> httpx.Response:
        """GET /api/v2/pay/budget"""
        headers = _agent_header(agent_id)
        return self.nexus.http.get("/api/v2/pay/budget", headers=headers)

    def create_policy(self, **kwargs: Any) -> httpx.Response:
        """POST /api/v2/pay/policies"""
        if "zone_id" not in kwargs:
            kwargs["zone_id"] = "corp"
        headers = _agent_header(None)
        return self.nexus.http.post(
            "/api/v2/pay/policies", json=kwargs, headers=headers
        )

    def list_policies(self) -> httpx.Response:
        """GET /api/v2/pay/policies"""
        headers = _agent_header(None)
        return self.nexus.http.get("/api/v2/pay/policies", headers=headers)

    def delete_policy(self, policy_id: str) -> httpx.Response:
        """DELETE /api/v2/pay/policies/{id}"""
        headers = _agent_header(None)
        return self.nexus.http.delete(
            f"/api/v2/pay/policies/{policy_id}", headers=headers
        )

    # --- Approvals ---

    def request_approval(
        self,
        amount: str,
        to: str,
        *,
        memo: str = "",
        agent_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v2/pay/approvals"""
        headers = _agent_header(agent_id)
        return self.nexus.http.post(
            "/api/v2/pay/approvals",
            json={"amount": amount, "to": to, "memo": memo},
            headers=headers,
        )

    def list_pending_approvals(self) -> httpx.Response:
        """GET /api/v2/pay/approvals"""
        headers = _agent_header(None)
        return self.nexus.http.get("/api/v2/pay/approvals", headers=headers)

    def approve(self, approval_id: str) -> httpx.Response:
        """POST /api/v2/pay/approvals/{id}/approve"""
        headers = _agent_header(None)
        return self.nexus.http.post(
            f"/api/v2/pay/approvals/{approval_id}/approve", headers=headers
        )

    def reject(self, approval_id: str) -> httpx.Response:
        """POST /api/v2/pay/approvals/{id}/reject"""
        headers = _agent_header(None)
        return self.nexus.http.post(
            f"/api/v2/pay/approvals/{approval_id}/reject", headers=headers
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_header(agent_id: str | None) -> dict[str, str]:
    """Build optional X-Agent-ID + X-Nexus-Zone-ID headers."""
    headers: dict[str, str] = {"X-Nexus-Zone-ID": "corp"}
    if agent_id is not None:
        headers["X-Agent-ID"] = agent_id
    return headers


def _build_pay_client(
    base_url: str, api_key: str, *, timeout: float = 60.0
) -> tuple[httpx.Client, PayClient]:
    """Create an httpx.Client + PayClient for a given base URL."""
    http = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(timeout, connect=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    nexus = NexusClient(http=http, base_url=base_url, api_key=api_key)
    return http, PayClient(nexus=nexus)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _pay_local_port() -> int:
    return int(os.getenv("NEXUS_TEST_PAY_LOCAL_PORT", "10100"))


@pytest.fixture(scope="session")
def _pay_remote_port() -> int:
    return int(os.getenv("NEXUS_TEST_PAY_REMOTE_PORT", "10101"))


@pytest.fixture(scope="session")
def _pay_db_port() -> int:
    return int(os.getenv("NEXUS_TEST_PAY_DB_PORT", "10102"))


@pytest.fixture(
    scope="session",
    params=["local", "remote"],
    ids=["local", "remote"],
)
def pay(
    request: pytest.FixtureRequest,
    settings: TestSettings,
    _pay_local_port: int,
    _pay_remote_port: int,
) -> Generator[PayClient]:
    """Session-scoped PayClient parametrized for local and remote modes.

    - local:  http://localhost:{NEXUS_TEST_PAY_LOCAL_PORT}  (default 10100)
    - remote: http://localhost:{NEXUS_TEST_PAY_REMOTE_PORT} (default 10101)

    Skips automatically if the target server is unreachable.
    """
    if request.param == "local":
        base_url = f"http://localhost:{_pay_local_port}"
    else:
        base_url = f"http://localhost:{_pay_remote_port}"

    http, client = _build_pay_client(
        base_url, settings.api_key, timeout=settings.request_timeout
    )

    # Health check — skip if server is not reachable
    try:
        resp = http.get("/health", timeout=5.0)
        resp.raise_for_status()
    except Exception as exc:
        http.close()
        pytest.skip(
            f"Pay server not reachable at {base_url} ({request.param}): {exc}"
        )

    yield client

    http.close()


@pytest.fixture
def pay_agent_id() -> str:
    """Generate a unique agent ID per test for isolation."""
    return f"test-pay-agent-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def pay_recipient_id() -> str:
    """Generate a unique recipient agent ID per test."""
    return f"test-pay-recv-{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def pay_zone(settings: TestSettings) -> str:
    """Zone ID for pay tests (defaults to TestSettings.zone)."""
    return settings.zone
