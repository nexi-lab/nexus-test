#!/usr/bin/env bash
# Start nexus server configured for E2E testing.
#
# Usage:
#   ./scripts/serve-for-tests.sh
#   ./scripts/serve-for-tests.sh --port 3000
#
# Environment variables (override as needed):
#   NEXUS_ENFORCE_PERMISSIONS  — enable ReBAC permission checks (default: true)
#   NEXUS_RATE_LIMIT_ENABLED   — enable rate limiting (default: true)
#   NEXUS_TEST_HOOKS           — register test hook endpoints (default: true)
#   NEXUS_RATE_LIMIT_ANONYMOUS — anonymous tier limit/min (default: 60)
#   NEXUS_RATE_LIMIT_AUTHENTICATED — authenticated tier limit/min (default: 300)

set -euo pipefail

export NEXUS_ENFORCE_PERMISSIONS="${NEXUS_ENFORCE_PERMISSIONS:-true}"
export NEXUS_RATE_LIMIT_ENABLED="${NEXUS_RATE_LIMIT_ENABLED:-true}"
export NEXUS_TEST_HOOKS="${NEXUS_TEST_HOOKS:-true}"
export NEXUS_RATE_LIMIT_ANONYMOUS="${NEXUS_RATE_LIMIT_ANONYMOUS:-60}"
export NEXUS_RATE_LIMIT_AUTHENTICATED="${NEXUS_RATE_LIMIT_AUTHENTICATED:-300}"

exec nexus serve "$@"
