#!/usr/bin/env bash
# The self-contained demo: chain, database, API, worker and web in one command.
#
#   ./scripts/demo.sh up       build and start, then smoke-test
#   ./scripts/demo.sh down     stop and remove the disposable chain and data
#   ./scripts/demo.sh reset    put the showcase back to VERIFICATION_PENDING
#   ./scripts/demo.sh logs [service]
#
# On a host, set PUBLIC_API_URL and PUBLIC_RPC_URL to the addresses a visitor's browser
# will use; they are inlined into the web bundle at build time.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE_FILE="docker-compose.demo.yml"
ARTIFACT="contracts/out/ImpactRegistry.sol/ImpactRegistry.json"

die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

command -v docker >/dev/null || die "docker is required."
docker compose version >/dev/null 2>&1 || die "docker compose v2 is required."

case "${1:-up}" in
  up)
    # The API image deploys the registry from this artifact, so it has to be built.
    [[ -f "$ARTIFACT" ]] || die "$ARTIFACT is missing. Run 'cd contracts && forge build' first."
    step "Building"
    compose build
    step "Starting (the chain is deployed and seeded on first start)"
    compose up -d --wait
    "$0" smoke
    ;;
  smoke)
    step "Smoke-testing"
    api="http://localhost:${API_PORT:-8000}"
    web="http://localhost:${WEB_PORT:-3000}"
    fail=0
    check() {
      local code
      code=$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 10 "$2" 2>/dev/null || true)
      if [[ "$code" == "200" ]]; then printf '  ok    %s\n' "$1"
      else printf '  FAIL  %s (HTTP %s)\n' "$1" "${code:-none}"; fail=1; fi
    }
    check "api readiness"      "$api/health/ready"
    check "claim read model"   "$api/claims/claim-water-12-200"
    check "provenance graph"   "$api/claims/claim-water-12-200/provenance"
    check "money trail"        "$api/financial/programs/program-clean-water-kenya-2026"
    check "web app"            "$web/"
    # Integrity resolves the commitment from the chain, so this exercises the registry.
    status=$(curl -fsS --max-time 10 -X POST "$api/evidence/ev-inv-8291/verify-integrity" \
      2>/dev/null | sed -n 's/.*"status":"\([A-Z]*\)".*/\1/p' || true)
    if [[ "$status" == "MATCH" ]]; then printf '  ok    evidence integrity, read from the registry\n'
    else printf '  FAIL  evidence integrity (got %s)\n' "${status:-no response}"; fail=1; fi
    [[ "$fail" == "0" ]] || die "Smoke test failed. 'docker compose -f $COMPOSE_FILE logs' has the detail."
    cat <<DONE

Demo is up.

  Web        $web
  API        $api
  Chain      http://localhost:${CHAIN_PORT:-8545}  (chain id 31337)

Sign in with the accounts printed by the seed; the shared password is in
apps/api/impactgraph/auth.py. The showcase resets itself every
${RESET_INTERVAL_SECONDS:-1800}s.
DONE
    ;;
  reset)
    compose exec -T api python -m impactgraph.cli demo-reset --local-only
    ;;
  down)
    step "Stopping and discarding the disposable chain and data"
    compose down -v
    ;;
  logs) compose logs -f "${2:-}" ;;
  *) die "Unknown command '${1}'. See the header." ;;
esac
