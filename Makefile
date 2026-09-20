.PHONY: install dev api web db-up migrate seed bootstrap-chain chain-args worker worker-once deploy-build deploy-up deploy-release deploy-smoke deploy-down deploy-logs test test-contracts test-api test-web test-browser-e2e test-local-e2e anvil deploy-local deploy-sepolia configure-sepolia-roles demo-reset new-sepolia-demo-run reconcile-chain

install:
	cd apps/api && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
	cd apps/web && npm install

db-up:
	docker compose up -d --wait postgres

migrate:
	cd apps/api && .venv/bin/alembic upgrade head

seed:
	cd apps/api && .venv/bin/python -m impactgraph.cli seed

bootstrap-chain:
	cd apps/api && .venv/bin/python -m impactgraph.cli bootstrap-chain

chain-args:
	@cd apps/api && .venv/bin/python -m impactgraph.cli chain-args

worker:
	cd apps/api && .venv/bin/python -m impactgraph.cli worker

worker-once:
	cd apps/api && .venv/bin/python -m impactgraph.cli worker-once

api:
	cd apps/api && .venv/bin/uvicorn impactgraph.main:app --reload --port 8000

web:
	cd apps/web && npm run dev

dev:
	@echo "Run 'make api' and 'make web' in separate terminals."

anvil:
	anvil --chain-id 31337

deploy-local:
	cd contracts && forge script script/DeployImpactRegistry.s.sol:DeployImpactRegistry --rpc-url http://127.0.0.1:8545 --broadcast

deploy-sepolia:
	@test -n "$(SEPOLIA_RPC_URL)" || (echo "SEPOLIA_RPC_URL is required" && exit 1)
	@echo "Explicit Sepolia deployment: chain 11155111. Deployer address will be shown by Forge."
	cd contracts && forge script script/DeployImpactRegistry.s.sol:DeployImpactRegistry --rpc-url "$(SEPOLIA_RPC_URL)" --broadcast

configure-sepolia-roles:
	@test -n "$(SEPOLIA_RPC_URL)" || (echo "SEPOLIA_RPC_URL is required" && exit 1)
	@test -n "$(IMPACT_REGISTRY_ADDRESS)" || (echo "IMPACT_REGISTRY_ADDRESS is required" && exit 1)
	@test -n "$(OPERATOR_WALLET_ADDRESS)" || (echo "OPERATOR_WALLET_ADDRESS is required" && exit 1)
	@test -n "$(VERIFIER_WALLET_ADDRESS)" || (echo "VERIFIER_WALLET_ADDRESS is required" && exit 1)
	@echo "Configuring ImpactRegistry roles on explicit Sepolia target (chain 11155111)."
	cd contracts && forge script script/ConfigureImpactRegistryRoles.s.sol:ConfigureImpactRegistryRoles --rpc-url "$(SEPOLIA_RPC_URL)" --broadcast

test: test-contracts test-api test-web

test-contracts:
	cd contracts && forge test

test-api:
	cd apps/api && .venv/bin/pytest

test-web:
	cd apps/web && npm test -- --run && npm run typecheck

test-browser-e2e:
	cd apps/web && npm run test:e2e

test-local-e2e:
	cd apps/api && .venv/bin/python scripts/local_evidence_e2e.py

demo-reset:
	@test "$(DEMO_MODE)" != "sepolia" || (echo "Refusing to reset Sepolia; create a new demo run." && exit 1)
	cd apps/api && .venv/bin/python -m impactgraph.cli demo-reset --local-only

new-sepolia-demo-run:
	cd apps/api && .venv/bin/python -m impactgraph.cli new-demo-run --network sepolia

reconcile-chain:
	cd apps/api && .venv/bin/python -m impactgraph.cli reconcile-chain

# Container deployment. Configuration comes from .env.production; see
# .env.production.example. Sepolia is deployed separately by scripts/deploy-sepolia.sh,
# which requires an explicit typed confirmation.
deploy-build:
	./scripts/deploy.sh build

deploy-up:
	./scripts/deploy.sh up

deploy-release:
	./scripts/deploy.sh release

deploy-smoke:
	./scripts/deploy.sh smoke

deploy-down:
	./scripts/deploy.sh down

deploy-logs:
	./scripts/deploy.sh logs
