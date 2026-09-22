#!/usr/bin/env bash
# Build, verify and run the ImpactGraph stack from docker-compose.prod.yml.
#
# Platform-neutral: it produces ordinary images and drives ordinary Compose, so it works
# on a laptop, a VM, or any host that speaks Docker. Pushing to a registry is opt-in.
#
#   ./scripts/deploy.sh build          build both images
#   ./scripts/deploy.sh push           push them to $IMAGE_PREFIX
#   ./scripts/deploy.sh migrate        run Alembic against the configured database
#   ./scripts/deploy.sh seed           seed the deterministic showcase
#   ./scripts/deploy.sh up             start the stack
#   ./scripts/deploy.sh down           stop it (volumes are kept)
#   ./scripts/deploy.sh logs [service]
#   ./scripts/deploy.sh smoke          check the running stack answers correctly
#   ./scripts/deploy.sh release        build -> up -> migrate -> seed -> smoke
#
# Configuration comes from --env-file (default .env.production); see
# .env.production.example.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE="${ENV_FILE:-.env.production}"
COMPOSE_FILE="docker-compose.prod.yml"
ARTIFACT="contracts/out/ImpactRegistry.sol/ImpactRegistry.json"

die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

command -v docker >/dev/null || die "docker is required."
docker compose version >/dev/null 2>&1 || die "docker compose v2 is required."
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found. Copy .env.production.example and fill it in."

compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

# Read the env file the way Compose does, rather than sourcing it: Compose permits
# unquoted values containing spaces, which `source` would try to execute.
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  [[ "$line" == *=* ]] || continue
  key="${line%%=*}"; value="${line#*=}"
  key="${key//[[:space:]]/}"
  value="${value%$'\r'}"
  if [[ ${#value} -ge 2 && ( ( "$value" == \"*\" ) || ( "$value" == \'*\' ) ) ]]; then
    value="${value:1:${#value}-2}"
  fi
  export "$key=$value"
done < "$ENV_FILE"

preflight() {
  # The API image embeds the contract ABI to decode registry events; without it the
  # adapter cannot start, and the failure would only surface at runtime.
  [[ -f "$ARTIFACT" ]] || die "$ARTIFACT is missing. Run 'cd contracts && forge build' first."

  if [[ "${DEMO_MODE:-}" == "sepolia" ]]; then
    [[ "${CHAIN_ID:-}" == "11155111" ]] || die "DEMO_MODE=sepolia requires CHAIN_ID=11155111."
    [[ -z "${EVM_SENDER_ADDRESS:-}" ]] \
      || die "EVM_SENDER_ADDRESS must be empty on a public network: the backend must not sign."
    [[ -n "${IMPACT_REGISTRY_ADDRESS:-}" ]] \
      || die "IMPACT_REGISTRY_ADDRESS is required. Deploy first with scripts/deploy-sepolia.sh."
  fi
  [[ "${AI_PROVIDER:-mock}" == "mock" ]] \
    || die "AI_PROVIDER=${AI_PROVIDER} is not implemented; the API will refuse to start."
}

api_run() { compose run --rm --no-deps -T api "$@"; }

case "${1:-release}" in
  build)
    preflight
    step "Building images"
    compose build
    ;;
  push)
    [[ "${IMAGE_PREFIX:-impactgraph}" == "impactgraph" ]] \
      && die "Set IMAGE_PREFIX to a real registry before pushing."
    step "Pushing images to ${IMAGE_PREFIX}"
    compose push
    ;;
  migrate)
    step "Running migrations"
    compose up -d postgres
    api_run alembic upgrade head
    ;;
  seed)
    step "Seeding the deterministic showcase"
    api_run python -m impactgraph.cli seed
    ;;
  up)
    preflight
    step "Starting the stack"
    compose up -d --wait
    compose ps
    ;;
  down)
    step "Stopping the stack (volumes kept)"
    compose down
    ;;
  logs)
    compose logs -f "${2:-}"
    ;;
  smoke)
    step "Smoke-testing the running stack"
    api="http://localhost:${API_PORT:-8000}"
    web="http://localhost:${WEB_PORT:-3000}"
    fail=0
    check() {
      local label="$1" url="$2" expected="${3:-200}"
      local code
      code=$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null || true)
      if [[ "$code" == "$expected" ]]; then printf '  ok    %s\n' "$label"
      else printf '  FAIL  %s (HTTP %s)\n' "$label" "${code:-none}"; fail=1; fi
    }
    check "api readiness"       "$api/health/ready"
    check "program read model"  "$api/programs/program-clean-water-kenya-2026"
    check "claim read model"    "$api/claims/claim-water-12-200"
    check "provenance graph"    "$api/claims/claim-water-12-200/provenance"
    check "web app"             "$web/"
    # Integrity must resolve the stored object, not report an unreadable one.
    status=$(curl -fsS --max-time 10 -X POST "$api/evidence/ev-inv-8291/verify-integrity" \
      2>/dev/null | sed -n 's/.*"status":"\([A-Z]*\)".*/\1/p' || true)
    if [[ "$status" == "MATCH" ]]; then printf '  ok    evidence integrity (MATCH)\n'
    else printf '  FAIL  evidence integrity (got %s)\n' "${status:-no response}"; fail=1; fi
    [[ "$fail" == "0" ]] || die "Smoke test failed."
    printf '\nStack is healthy: %s\n' "$web"
    ;;
  release)
    preflight
    "$0" build
    "$0" up
    "$0" migrate
    "$0" seed
    compose restart api worker
    compose up -d --wait
    "$0" smoke
    ;;
  *)
    die "Unknown command '${1}'. Run with no argument for 'release', or see the header."
    ;;
esac
