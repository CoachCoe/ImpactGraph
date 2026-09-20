#!/usr/bin/env bash
# Deploy ImpactRegistry to Ethereum Sepolia, configure roles, bootstrap the showcase
# entities, and record the result.
#
# This is deliberately not a Make one-liner. It performs an irreversible public action, so
# it preflights everything it can, prints exactly what it is about to do, and requires an
# explicit typed confirmation. It never runs merely because an RPC URL is present, and it
# refuses to run in CI.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO_ROOT="$PWD"
RECORD="docs/sepolia-deployment.md"
SEPOLIA_CHAIN_ID=11155111

die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[[ -n "${CI:-}" ]] && die "Refusing to deploy from CI. Run this from a workstation."

for tool in forge cast jq; do
  command -v "$tool" >/dev/null || die "$tool is required but not on PATH."
done
[[ -x apps/api/.venv/bin/python ]] || die "apps/api/.venv is missing. Run 'make install' first."

: "${SEPOLIA_RPC_URL:?SEPOLIA_RPC_URL is required}"
: "${DEPLOYER_PRIVATE_KEY:?DEPLOYER_PRIVATE_KEY is required (Sepolia-only, funded)}"
: "${OPERATOR_WALLET_ADDRESS:?OPERATOR_WALLET_ADDRESS is required}"
: "${VERIFIER_WALLET_ADDRESS:?VERIFIER_WALLET_ADDRESS is required}"

step "Preflight"

CHAIN_ID=$(cast chain-id --rpc-url "$SEPOLIA_RPC_URL")
[[ "$CHAIN_ID" == "$SEPOLIA_CHAIN_ID" ]] \
  || die "RPC reports chain $CHAIN_ID, expected $SEPOLIA_CHAIN_ID. Wrong endpoint."

# Normalise the key before anything reads it. `cast` accepts a bare hex key, but Forge's
# vm.envUint demands the 0x prefix -- so validating with cast and deploying with forge let
# a malformed key through preflight and fail after the confirmation prompt.
_key="${DEPLOYER_PRIVATE_KEY#0x}"
if [[ ! "$_key" =~ ^[0-9a-fA-F]{64}$ ]]; then
  die "DEPLOYER_PRIVATE_KEY must be 64 hex characters (a leading 0x is optional); got ${#_key}."
fi
export DEPLOYER_PRIVATE_KEY="0x${_key}"
unset _key

DEPLOYER=$(cast wallet address --private-key "$DEPLOYER_PRIVATE_KEY")
BALANCE_WEI=$(cast balance "$DEPLOYER" --rpc-url "$SEPOLIA_RPC_URL")
BALANCE_ETH=$(cast from-wei "$BALANCE_WEI")
[[ "$BALANCE_WEI" != "0" ]] || die "Deployer $DEPLOYER has no SepoliaETH."

# The whole trust model rests on these being different actors.
shopt -s nocasematch
[[ "$OPERATOR_WALLET_ADDRESS" != "$VERIFIER_WALLET_ADDRESS" ]] \
  || die "Operator and verifier must be distinct addresses."
[[ "$VERIFIER_WALLET_ADDRESS" != "$DEPLOYER" ]] \
  || die "The verifier must not be the deployer: the deployer is granted ADMIN and OPERATOR."
shopt -u nocasematch

DEMO_RUN_ID=$(cd apps/api && .venv/bin/python -m impactgraph.cli new-demo-run --network sepolia \
  | jq -r .demoRunId)

cat <<SUMMARY

  Network      Ethereum Sepolia (chain $CHAIN_ID)
  RPC          ${SEPOLIA_RPC_URL%%\?*}
  Deployer     $DEPLOYER  ($BALANCE_ETH ETH)
  Operator     $OPERATOR_WALLET_ADDRESS
  Verifier     $VERIFIER_WALLET_ADDRESS
  Demo run     $DEMO_RUN_ID

  This deploys a new immutable contract to a public network and spends real SepoliaETH.
  It cannot be undone.

SUMMARY

read -r -p 'Type "deploy to sepolia" to continue: ' CONFIRMATION
[[ "$CONFIRMATION" == "deploy to sepolia" ]] || die "Not confirmed; nothing was sent."

step "Deploying ImpactRegistry"
( cd contracts && forge script script/DeployImpactRegistry.s.sol:DeployImpactRegistry \
    --rpc-url "$SEPOLIA_RPC_URL" --broadcast )

BROADCAST="contracts/broadcast/DeployImpactRegistry.s.sol/$SEPOLIA_CHAIN_ID/run-latest.json"
[[ -f "$BROADCAST" ]] || die "No broadcast record at $BROADCAST."
IMPACT_REGISTRY_ADDRESS=$(jq -r '[.transactions[] | select(.transactionType=="CREATE")][0].contractAddress' "$BROADCAST")
DEPLOY_TX=$(jq -r '[.transactions[] | select(.transactionType=="CREATE")][0].hash' "$BROADCAST")
[[ "$IMPACT_REGISTRY_ADDRESS" != "null" ]] || die "Could not read the deployed address."
export IMPACT_REGISTRY_ADDRESS
DEPLOY_BLOCK=$(cast receipt "$DEPLOY_TX" blockNumber --rpc-url "$SEPOLIA_RPC_URL")

step "Configuring roles"
( cd contracts && forge script \
    script/ConfigureImpactRegistryRoles.s.sol:ConfigureImpactRegistryRoles \
    --rpc-url "$SEPOLIA_RPC_URL" --broadcast )

step "Bootstrapping showcase entities"
# Computed in Python: they use the canonical framed encoding Solidity cannot reproduce.
eval "$(cd apps/api && .venv/bin/python -m impactgraph.cli chain-args)"
export BOOTSTRAP_PROGRAM_ID BOOTSTRAP_PROGRAM_COMMITMENT BOOTSTRAP_CLAIM_ID BOOTSTRAP_CLAIM_COMMITMENT
( cd contracts && forge script script/BootstrapImpactRegistry.s.sol:BootstrapImpactRegistry \
    --rpc-url "$SEPOLIA_RPC_URL" --broadcast )

step "Verifying onchain state"
call() { cast call "$IMPACT_REGISTRY_ADDRESS" "$@" --rpc-url "$SEPOLIA_RPC_URL"; }
OPERATOR_ROLE=$(call "OPERATOR_ROLE()(bytes32)")
VERIFIER_ROLE=$(call "VERIFIER_ROLE()(bytes32)")

check() { # description expected actual
  if [[ "$3" == "$2" ]]; then printf '  ok    %s\n' "$1"
  else printf '  FAIL  %s (got %s)\n' "$1" "$3"; FAILED=1; fi
}
FAILED=0
check "operator holds OPERATOR_ROLE" true \
  "$(call 'hasRole(bytes32,address)(bool)' "$OPERATOR_ROLE" "$OPERATOR_WALLET_ADDRESS")"
check "verifier holds VERIFIER_ROLE" true \
  "$(call 'hasRole(bytes32,address)(bool)' "$VERIFIER_ROLE" "$VERIFIER_WALLET_ADDRESS")"
check "verifier does NOT hold OPERATOR_ROLE" false \
  "$(call 'hasRole(bytes32,address)(bool)' "$OPERATOR_ROLE" "$VERIFIER_WALLET_ADDRESS")"
check "program entity exists" true "$(call 'entityExists(bytes32)(bool)' "$BOOTSTRAP_PROGRAM_ID")"
check "claim entity exists" true "$(call 'entityExists(bytes32)(bool)' "$BOOTSTRAP_CLAIM_ID")"
[[ "$FAILED" == "0" ]] || die "Onchain verification failed. Nothing was recorded."

step "Recording the deployment"
EXPLORER="${BLOCK_EXPLORER_URL:-https://sepolia.etherscan.io}"
python3 - "$RECORD" <<PY
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
record = """\`\`\`text
Demo run ID: $DEMO_RUN_ID
Chain ID: $SEPOLIA_CHAIN_ID
Contract address: $IMPACT_REGISTRY_ADDRESS
Deployment transaction: $DEPLOY_TX
Deployment block: $DEPLOY_BLOCK
Deployer address: $DEPLOYER
Operator address: $OPERATOR_WALLET_ADDRESS
Verifier address: $VERIFIER_WALLET_ADDRESS
Role configuration: verified onchain by scripts/deploy-sepolia.sh
Program entity: $BOOTSTRAP_PROGRAM_ID
Claim entity: $BOOTSTRAP_CLAIM_ID
Evidence registration transaction: NOT YET RUN
Verifier attestation transaction: NOT YET RUN
Verification bundle hash: NOT YET RUN
Explorer: $EXPLORER/address/$IMPACT_REGISTRY_ADDRESS
\`\`\`"""
text = path.read_text()
start = text.index("\`\`\`text", text.index("## Public deployment record"))
end = text.index("\`\`\`", start + 7) + 3
path.write_text(text[:start] + record + text[end:])
PY

cat <<DONE

Deployed and verified.

  Contract   $IMPACT_REGISTRY_ADDRESS
  Explorer   $EXPLORER/address/$IMPACT_REGISTRY_ADDRESS
  Recorded   $RECORD

Next: put these in your .env and restart the API.

  IMPACT_REGISTRY_ADDRESS=$IMPACT_REGISTRY_ADDRESS
  CHAIN_ID=$SEPOLIA_CHAIN_ID
  DEMO_MODE=sepolia
  RPC_URL=<your Sepolia RPC>
  EVM_SENDER_ADDRESS=            # must stay empty: no backend signing on a public network
  BLOCK_EXPLORER_URL=$EXPLORER

Then run the golden path and record the evidence-registration and attestation transactions
in $RECORD.
DONE
