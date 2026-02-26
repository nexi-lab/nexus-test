#!/usr/bin/env python3
"""Launch nexus server with pay services wired for E2E testing.

Patches the startup lifecycle to inject:
  - CreditsService (disabled mode — unlimited virtual balance, no TigerBeetle)
  - SpendingPolicyService (DB-backed via SQLAlchemy repository)

Usage:
    cd ~/nexus && uv run python ~/nexus-test/scripts/serve-pay-tests.py \
        --port 10100 --auth-type database --init \
        --host 0.0.0.0 --data-dir /tmp/nexus-test-10100
"""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)

# ── Monkey-patch: wire CreditsService + SpendingPolicyService during startup ──

import nexus.server.lifespan.services as _svc_mod  # noqa: E402

_original_startup = _svc_mod.startup_services


async def _patched_startup(app, svc):  # type: ignore[no-untyped-def]
    """Wrap original startup_services to also wire pay services."""
    bg_tasks = await _original_startup(app, svc)

    # 1) CreditsService in disabled mode (no TigerBeetle required)
    try:
        from nexus.bricks.pay.credits import CreditsService

        cs = CreditsService(enabled=False)
        app.state.credits_service = cs
        logger.info("CreditsService wired (disabled mode — unlimited balance)")
    except Exception as exc:
        logger.warning("Failed to wire CreditsService: %s", exc)

    # 2) SpendingPolicyService with DB-backed repository
    try:
        record_store = getattr(app.state, "record_store", None)
        if record_store is not None:
            from nexus.bricks.pay.spending_policy_service import SpendingPolicyService
            from nexus.storage.repositories.spending_policy import (
                SQLAlchemySpendingPolicyRepository,
            )

            repo = SQLAlchemySpendingPolicyRepository(record_store)
            sps = SpendingPolicyService(repo)
            app.state.spending_policy_service = sps
            logger.info("SpendingPolicyService wired (DB-backed)")
        else:
            logger.warning("No record_store — SpendingPolicyService not wired")
    except Exception as exc:
        logger.warning("Failed to wire SpendingPolicyService: %s", exc)

    return bg_tasks


_svc_mod.startup_services = _patched_startup

# ── Launch server via CLI ──

from nexus.cli import main as cli_main  # noqa: E402

sys.exit(cli_main())
