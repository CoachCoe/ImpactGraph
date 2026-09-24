#!/usr/bin/env bash
# Provision the hosted showcase personas with a private password. This is intentionally
# separate from `seed`: production seeding must never create users with published credentials.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE="${ENV_FILE:-.env.production}"
COMPOSE_FILE="docker-compose.prod.yml"
[[ -f "$ENV_FILE" ]] || { printf 'error: %s not found\n' "$ENV_FILE" >&2; exit 1; }

read -r -s -p "Private password for the hosted demo users: " IMPACTGRAPH_PROVISIONING_PASSWORD
printf '\n'
read -r -s -p "Confirm password: " confirmation
printf '\n'
[[ "$IMPACTGRAPH_PROVISIONING_PASSWORD" == "$confirmation" ]] \
  || { printf 'error: passwords do not match\n' >&2; exit 1; }
[[ ${#IMPACTGRAPH_PROVISIONING_PASSWORD} -ge 12 ]] \
  || { printf 'error: password must contain at least 12 characters\n' >&2; exit 1; }
[[ "$IMPACTGRAPH_PROVISIONING_PASSWORD" != "impactgraph-demo" ]] \
  || { printf 'error: the published local demo password is forbidden\n' >&2; exit 1; }

export IMPACTGRAPH_PROVISIONING_PASSWORD
trap 'unset IMPACTGRAPH_PROVISIONING_PASSWORD confirmation' EXIT

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm --no-deps -T \
  -e IMPACTGRAPH_PROVISIONING_PASSWORD api \
  python -m impactgraph.cli provision-demo-users

printf '\nHosted demo users are ready. Existing accounts, if any, were not changed.\n'
