# Payment E2E Test Setup Guide

## Quick Start (Static Auth — Recommended)

```bash
# 1. Ensure PostgreSQL is running (port 5434 per .env.test)
pg_isready -p 5434

# 2. Start two nexus servers with static auth (simpler — fixed API key)
cd ~/nexus

# Server 1 (local — port 10100)
NEXUS_DATABASE_URL=postgresql://postgres:nexus@localhost:5434/nexus \
  uv run nexus serve \
  --port 10100 --profile lite \
  --auth-type static --api-key "sk-test-pay-e2e-key" \
  --host 0.0.0.0 --data-dir /tmp/nexus-test-10100

# Server 2 (remote — port 10101)
NEXUS_DATABASE_URL=postgresql://postgres:nexus@localhost:5434/nexus \
  uv run nexus serve \
  --port 10101 --profile lite \
  --auth-type static --api-key "sk-test-pay-e2e-key" \
  --host 0.0.0.0 --data-dir /tmp/nexus-test-10101

# 3. Run payment E2E tests
cd ~/nexus-test
NEXUS_TEST_URL=http://localhost:10100 \
NEXUS_TEST_API_KEY=sk-test-pay-e2e-key \
NEXUS_TEST_ZONE=corp \
uv run pytest tests/pay/ -v -o "addopts="
```

## Quick Start (Database Auth — Alternative)

```bash
# 1. Ensure PostgreSQL is running (port 5434 per .env.test)
pg_isready -p 5434

# 2. Start two nexus servers with database auth:
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
  |  | Auth Middleware                      |  |
  |  | static: Bearer token → admin user   |  |
  |  | database: API key → AuthResult      |  |
  |  |   (subject_id, zone_id, is_admin)   |  |
  |  +-------------------------------------+  |
  |              |                             |
  |  +-------------------------------------+  |
  |  | X-Agent-ID + X-Nexus-Zone-ID        |  |
  |  | Headers (per-agent + zone scoping)  |  |
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
| pay/011 | `TestMeteringBasics` | 4 | Meter success, balance deduction, custom event_type, rapid meters |
| pay/012 | `TestMeteringEdgeCases` | 4 | Smallest unit, negative/zero/precision validation |
| pay/013 | `TestNegativeAmountValidation` | 3 | Transfer, reserve, batch reject negative amounts |
| pay/014 | `TestZeroAmountValidation` | 3 | Transfer, reserve, can-afford reject zero amounts |
| pay/015 | `TestReservationErrorPaths` | 4 | Non-existent ID, double-commit, commit-after-release |
| pay/016 | `TestTransferMethodRouting` | 3 | Credits/auto methods, single-item batch |

**Total: 58 tests x 2 modes (local + remote) = 116 items**

### Test Parametrization

Every test runs twice via the `pay` fixture:
- `[local]` — against `http://localhost:10100`
- `[remote]` — against `http://localhost:10101`

Each mode skips automatically if its server is unreachable (health check on fixture setup).

## Test Files

| File | LOC | Purpose |
|------|-----|---------|
| `tests/pay/__init__.py` | 0 | Package marker |
| `tests/pay/conftest.py` | ~340 | `PayClient` dataclass wrapping all 16 `/api/v2/pay/` endpoints; `pay` fixture (session-scoped, parametrized); `pay_agent_id`, `pay_recipient_id`, `pay_zone` fixtures |
| `tests/pay/test_pay.py` | ~1100 | 16 test classes, 58 test methods |
| `scripts/serve-pay-tests.py` | ~60 | Custom launcher — monkey-patches `startup_services` to wire `CreditsService` + `SpendingPolicyService` |

## Known Limitations

### X-Nexus-Zone-ID Header Required for Zone-Scoped Endpoints

With static auth (`--auth-type static`), the server's `require_auth` dependency reads `zone_id` from the `X-Nexus-Zone-ID` request header. Without this header, zone-scoped operations (policies, approvals) will fail with 500 errors because `zone_id` defaults to `None`.

The `PayClient` in `conftest.py` includes `X-Nexus-Zone-ID: corp` on all requests via the `_agent_header()` helper. If you add new endpoints, ensure they also send this header.

### Reservation Error Handling in Disabled Mode

In disabled mode (no TigerBeetle), `CreditsService` silently succeeds on all commit/release operations regardless of reservation state. Tests in pay/015 (`TestReservationErrorPaths`) accept both success (204) and error (404/409) responses to work in both modes. In production with TigerBeetle enabled, stricter error codes would apply.

### Policy Enforcement is Client-Side Only

The REST transfer endpoint (`POST /api/v2/pay/transfer`) does **not** enforce spending policies server-side. Policies are stored and queryable via `GET /budget` and `CRUD /policies`, but transfers always succeed regardless of limits.

Server-side enforcement would require wiring `SpendingPolicyService` into the `NexusPay.transfer()` call path. Currently, enforcement is available via the `PolicyEnforcedPayment` SDK wrapper (client-side).

Tests for pay/003 and pay/008 verify that policies are correctly created and visible in the budget endpoint, and document that transfers succeed despite exceeding limits.

### CreditsService Not Wired in Standard Server

The standard `nexus serve` command does not wire `CreditsService` or `SpendingPolicyService` onto `app.state`. The custom launcher (`serve-pay-tests.py`) patches the lifespan startup to inject these services. When using `--profile lite` with static auth, the server does wire these services automatically.

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

### All 116 tests SKIPPED

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

### `500 Internal Server Error` on policy/approval endpoints

Most likely cause: missing `X-Nexus-Zone-ID` header. The server reads `zone_id` from this header via `require_auth`. Without it, the `zone_id` is `None`, causing null constraint violations on DB inserts.

The `PayClient` adds this header automatically via `_agent_header()`. If you call the API directly (e.g., with `curl`), include it:

```bash
curl -X POST http://localhost:10100/api/v2/pay/policies \
  -H "Authorization: Bearer sk-test-pay-e2e-key" \
  -H "X-Nexus-Zone-ID: corp" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "test", "daily_limit": "100.000000", "enabled": true}'
```

### Second server also needs same API key

Both servers share the same PostgreSQL database (port 5434), so they share API keys. Only use `--init` on the **first** server. The second server will authenticate against the same `api_keys` table without needing `--init`.

### `--init requires --auth-type database`

When using `--auth-type static --api-key "..."`, the `--init` flag is not allowed (static auth doesn't use the database for API keys). Drop `--init` when using static auth.
