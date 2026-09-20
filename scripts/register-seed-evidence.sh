#!/usr/bin/env bash
# Register the seeded invoice's commitment on the configured registry, signed by the
# operator's wallet, then record the verified receipt.
#
# On a public network the backend holds no key, so the API cannot do this itself. Foundry
# signs; the backend then reads the receipt back and records it only if it really
# registered this evidence with this commitment.
#
#   IMPACT_REGISTRY_ADDRESS=0x... RPC_URL=https://... DEPLOYER_PRIVATE_KEY=0x... \
#     ./scripts/register-seed-evidence.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

for tool in forge cast jq; do command -v "$tool" >/dev/null || die "$tool is required."; done
[[ -x apps/api/.venv/bin/python ]] || die "apps/api/.venv is missing. Run 'make install' first."

PY=apps/api/.venv/bin/python
: "${IMPACT_REGISTRY_ADDRESS:?IMPACT_REGISTRY_ADDRESS is required}"
: "${DEPLOYER_PRIVATE_KEY:?DEPLOYER_PRIVATE_KEY is required (the operator wallet)}"
RPC_URL="${RPC_URL:-${SEPOLIA_RPC_URL:-}}"
[[ -n "$RPC_URL" ]] || die "RPC_URL (or SEPOLIA_RPC_URL) is required"
export IMPACT_REGISTRY_ADDRESS RPC_URL

# Same normalisation as the deployment script: cast takes a bare hex key, forge does not.
_key="${DEPLOYER_PRIVATE_KEY#0x}"
[[ "$_key" =~ ^[0-9a-fA-F]{64}$ ]] \
  || die "DEPLOYER_PRIVATE_KEY must be 64 hex characters (a leading 0x is optional); got ${#_key}."
export DEPLOYER_PRIVATE_KEY="0x${_key}"
unset _key

step "Preflight"
CHAIN_ID=$(cast chain-id --rpc-url "$RPC_URL")
OPERATOR=$(cast wallet address --private-key "$DEPLOYER_PRIVATE_KEY")
# The values come from the same place the backend derives them, never retyped here.
eval "$("$PY" -m impactgraph.cli chain-args)"
OPERATOR_ROLE=$(cast call "$IMPACT_REGISTRY_ADDRESS" "OPERATOR_ROLE()(bytes32)" --rpc-url "$RPC_URL")
HAS_ROLE=$(cast call "$IMPACT_REGISTRY_ADDRESS" "hasRole(bytes32,address)(bool)" \
  "$OPERATOR_ROLE" "$OPERATOR" --rpc-url "$RPC_URL")
[[ "$HAS_ROLE" == "true" ]] \
  || die "$OPERATOR does not hold OPERATOR_ROLE on $IMPACT_REGISTRY_ADDRESS; it cannot register evidence."

printf '  chain      %s\n  registry   %s\n  operator   %s\n  evidence   %s\n' \
  "$CHAIN_ID" "$IMPACT_REGISTRY_ADDRESS" "$OPERATOR" "$BOOTSTRAP_EVIDENCE_ID"

if [[ "$(cast call "$IMPACT_REGISTRY_ADDRESS" "entityExists(bytes32)(bool)" \
        "$BOOTSTRAP_EVIDENCE_ID" --rpc-url "$RPC_URL")" == "true" ]]; then
  die "This evidence is already registered on $IMPACT_REGISTRY_ADDRESS. Registrations are
       immutable, so re-registering is impossible by design. Record the original with:
       $PY -m impactgraph.cli record-evidence-registration --transaction-hash 0x..."
fi

step "Registering, signed by the operator"
( cd contracts && forge script script/RegisterSeedEvidence.s.sol:RegisterSeedEvidence \
    --rpc-url "$RPC_URL" --broadcast )

BROADCAST="contracts/broadcast/RegisterSeedEvidence.s.sol/$CHAIN_ID/run-latest.json"
[[ -f "$BROADCAST" ]] || die "No broadcast record at $BROADCAST."
TX=$(jq -r '[.transactions[]? | select(.transactionType=="CALL")][0].hash // empty' "$BROADCAST")
[[ -n "$TX" ]] || die "The run broadcast no transaction. Nothing to record."

step "Verifying the receipt and recording it"
# The backend refuses unless the receipt really carries this registration.
"$PY" -m impactgraph.cli record-evidence-registration --transaction-hash "$TX"

EXPLORER="${BLOCK_EXPLORER_URL:-https://sepolia.etherscan.io}"
printf '\nRegistered and recorded.\n  %s/tx/%s\n' "$EXPLORER" "$TX"
