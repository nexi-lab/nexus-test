# Payment E2E Test Setup Guide

## Quick Start

```bash
# 1. Ensure PostgreSQL is running (port 5434 per .env.test)
pg_isready -p 5434

# 2. Start two nexus servers with pay services wired:
cd ~/nexus

# Server 1 (local — port 10100, generates admin API key)
NEXUS_DATABASE_URL=postgresql://postgres:nexus@localhost:5434/nexus \
  uv run python ~/nexus-test/scripts/serve-pay-tests.py serve \
  --port 10100 --auth-type database --init \
  --host 0.0.0.0 --data-dir /tmp/nexus-test-10100

# Note the printed Admin API Key (e.g. sk-root_admin_xxxx_yyyyyy)

# Server 2 (remote — port 10101, reuses same DB)
NEXUS_DATABASE_URL=postgresql://postgres:nexus@localhost:5434/nexus \
  uv run python ~/nexus-test/scripts/serve-pay-tests.py serve \
  --port 10101 --auth-type database \
  --host 0.0.0.0 --data-dir /tmp/nexus-test-10101

# 3. Run payment E2E tests
cd ~/nexus-test
NEXUS_TEST_URL=http://localhost:10100 \
NEXUS_TEST_API_KEY=<admin-key-from-step-2> \
uv run pytest tests/pay/ -v -o "addopts="
```

## Architecture

### Payment System Overview

```
  Test Client (PayClient)
      |
      | HTTP REST (httpx)
      v
  +-------------------------------------------+
  | FastAPI Nexus Server (:10100 / :10101)    |
  |                                           |
  |  +-------------------------------------+  |
  |  | Auth Middleware (database auth)      |  |
  |  | API key → AuthResult (subject_id,   |  |
  |  |           zone_id, is_admin)         |  |
  |  +-------------------------------------+  |
  |              |                             |
  |  +-------------------------------------+  |
  |  | X-Agent-ID Header Extraction        |  |
  |  | (per-agent scoping for isolation)   |  |
  |  +-------------------------------------+  |
  |              |                             |
  |  +-------------------------------------+  |
  |  | /api/v2/pay/ Router (16 endpoints)  |  |
  |  |                                     |  |
  |  |  Balance ─── GET /balance           |  |
  |  |  Afford ──── GET /can-afford        |  |
  |  |  Transfer ── POST /transfer         |  |
  |  |  Batch ───── POST /transfer/batch   |  |
  |  |  Reserve ─── POST /reserve          |  |
  |  |  Commit ──── POST /reserve/{id}/..  |  |
  |  |  Release ─── POST /reserve/{id}/..  |  |
  |  |  Meter ───── POST /meter            |  |
  |  |  Budget ──── GET /budget            |  |
  |  |  Policies ── CRUD /policies         |  |
  |  |  Approvals ─ CRUD /approvals        |  |
  |  +-------------------------------------+  |
  |              |                             |
  |  +-----------+-----------+                 |
  |  |                       |                 |
  |  v                       v                 |
  |  CreditsService    SpendingPolicyService   |
  |  (disabled mode)   (DB-backed)             |
  |  - unlimited bal   - CRUD policies         |
  |  - virtual ledger  - approval workflow     |
  |  - no TigerBeetle  - spending ledger       |
  +-------------------------------------------+
              |
              v
  +-------------------------------------------+
  | PostgreSQL (:5434)                        |
  |                                           |
  |  spending_policies    — budget limits     |
  |  spending_ledger      — period counters   |
  |  spending_approvals   — approval records  |
  |  payment_transaction_meta — tx metadata   |
  +-------------------------------------------+
```

### Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `CreditsService` | `nexus.bricks.pay.credits` | Core ledger — balance, transfer, reserve/commit/release, metering |
| `SpendingPolicyService` | `nexus.bricks.pay.spending_policy_service` | Policy CRUD, approval workflow, spending tracking |
| `NexusPay` SDK | `nexus.bricks.pay.sdk` | Per-request facade wired via `get_nexuspay` dependency |
| `PolicyEnforcedPayment` | `nexus.bricks.pay.sdk` | Client-side policy enforcement wrapper (not used in REST path) |
| Pay Router | `nexus.server.api.v2.routers.pay` | 16 REST endpoints under `/api/v2/pay/` |

### Two-Phase Credit Reservation

```
  Agent creates reservation
      |
      v
  POST /reserve  ──>  reservation_id + status="reserved"
      |
      +─── commit ──>  POST /reserve/{id}/commit  (with optional partial amount)
      |                    └─> balance deducted, reservation closed
      |
      +─── release ──>  POST /reserve/{id}/release
                            └─> balance restored, reservation cancelled
```

### Disabled Mode vs Full Mode

The test launcher (`serve-pay-tests.py`) starts `CreditsService(enabled=False)`:

| Feature | Disabled Mode (tests) | Full Mode (production) |
|---------|----------------------|----------------------|
| Balance | Unlimited (999999999) | Real TigerBeetle ledger |
| Transfers | Always succeed | Debit/credit with balance check |
| Reservations | Virtual tracking | Atomic TigerBeetle holds |
| Policy enforcement | Storage only (not on transfer path) | Storage only (client-side enforcement via SDK) |
| Backend | None (in-memory) | TigerBeetle (40k+ TPS) |

## Infrastructure Dependencies

| Service | Required | Port | Purpose |
|---------|----------|------|---------|
| PostgreSQL | Yes | 5434 | Spending policies, approvals, ledger, API keys |
| Nexus Server (local) | Yes | 10100 | Primary test target |
| Nexus Server (remote) | Optional | 10101 | Second parametrized target |
| TigerBeetle | No | 3000 | Real ledger (not needed in disabled mode) |

### Port Allocation

Tests use dedicated ports to avoid conflicts with other test suites:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_TEST_PAY_LOCAL_PORT` | `10100` | Local nexus instance |
| `NEXUS_TEST_PAY_REMOTE_PORT` | `10101` | Remote nexus instance |
| `NEXUS_TEST_PAY_DB_PORT` | `10102` | Database backend (reserved) |

## Test-Side Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_TEST_URL` | `http://localhost:2026` | Must point to a running server for root conftest health check |
| `NEXUS_TEST_API_KEY` | from `.env.test` | Admin API key (must match server's DB auth) |

## Test Matrix

| ID | Test Class | Tests | Description |
|----|-----------|-------|-------------|
| pay/001 | `TestCreditBalanceQuery` | 3 | Balance structure, decimal values, agent-scoped balance |
| pay/002 | `TestDepositWithdraw` | 3 | Transfer receipt, deduction verification, idempotency |
| pay/003 | `TestSpendLimitEnforcement` | 3 | Policy creation, under-limit transfers, daily limits |
| pay/004 | `TestConcurrentTransactions` | 2 | 100 parallel transfers (no double-spend), mixed ops |
| pay/005 | `TestLedgerAuditTrail` | 3 | Budget summary structure, spending tracking, no-policy |
| pay/006 | `TestCreditReservation` | 5 | Reserve, commit, partial commit, release, balance check |
| pay/007 | `TestBatchTransfer` | 4 | Batch success, atomicity, empty batch, max size validation |
| pay/008 | `TestSpendingPolicyCRUD` | 4 | Create, list, delete, policy visible in budget |
| pay/009 | `TestSpendingApprovalWorkflow` | 4 | Request, approve, reject, list pending |
| pay/010 | `TestCanAffordCheck` | 6 | Boolean result, small/huge amounts, scoped, validation |

**Total: 37 tests x 2 modes (local + remote) = 74 items**

### Test Parametrization

Every test runs twice via the `pay` fixture:
- `[local]` — against `http://localhost:10100`
- `[remote]` — against `http://localhost:10101`

Each mode skips automatically if its server is unreachable (health check on fixture setup).

## Test Files

| File | LOC | Purpose |
|------|-----|---------|
| `tests/pay/__init__.py` | 0 | Package marker |
| `tests/pay/conftest.py` | ~330 | `PayClient` dataclass wrapping all 16 `/api/v2/pay/` endpoints; `pay` fixture (session-scoped, parametrized); `pay_agent_id`, `pay_recipient_id`, `pay_zone` fixtures |
| `tests/pay/test_pay.py` | ~900 | 10 test classes, 37 test methods |
| `scripts/serve-pay-tests.py` | ~60 | Custom launcher — monkey-patches `startup_services` to wire `CreditsService` + `SpendingPolicyService` |

## Known Limitations

### Policy Enforcement is Client-Side Only

The REST transfer endpoint (`POST /api/v2/pay/transfer`) does **not** enforce spending policies server-side. Policies are stored and queryable via `GET /budget` and `CRUD /policies`, but transfers always succeed regardless of limits.

Server-side enforcement would require wiring `SpendingPolicyService` into the `NexusPay.transfer()` call path. Currently, enforcement is available via the `PolicyEnforcedPayment` SDK wrapper (client-side).

Tests for pay/003 and pay/008 verify that policies are correctly created and visible in the budget endpoint, and document that transfers succeed despite exceeding limits.

### CreditsService Not Wired in Standard Server

The standard `nexus serve` command does not wire `CreditsService` or `SpendingPolicyService` onto `app.state`. The custom launcher (`serve-pay-tests.py`) patches the lifespan startup to inject these services. This is why the launcher script is required — without it, all pay endpoints return `503 Credits service not available`.

## Troubleshooting

### `503 Credits service not available`

The server was started without the custom launcher. Use `serve-pay-tests.py` instead of plain `nexus serve`:

```bash
uv run python ~/nexus-test/scripts/serve-pay-tests.py serve --port 10100 ...
```

### `401 Unauthorized`

API key mismatch. When using `--auth-type database --init`, the server generates a new admin key each time. Use the key printed at startup:

```
Admin API Key: sk-root_admin_xxxx_yyyyyy
```

Pass it via `NEXUS_TEST_API_KEY` when running tests.

### All 74 tests SKIPPED

Two possible causes:

1. **Root conftest health check**: The `_cluster_ready` autouse fixture in `tests/conftest.py` blocks all tests if `NEXUS_TEST_URL` doesn't respond to `/health` within 120s. Set `NEXUS_TEST_URL=http://localhost:10100`.

2. **Pay servers unreachable**: The `pay` fixture skips if health check fails on ports 10100/10101. Verify servers are running:
   ```bash
   curl http://localhost:10100/health
   curl http://localhost:10101/health
   ```

### `redb` lock error

```
Error: Database already open. Cannot acquire lock.
```

Another nexus instance is using the same `--data-dir`. Use separate directories:

```bash
--data-dir /tmp/nexus-test-10100  # server 1
--data-dir /tmp/nexus-test-10101  # server 2
```

### Second server also needs same API key

Both servers share the same PostgreSQL database (port 5434), so they share API keys. Only use `--init` on the **first** server. The second server will authenticate against the same `api_keys` table without needing `--init`.
