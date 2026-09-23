from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Networks where a mistake is irreversible and public. Anything gated on "is this a
# real network" keys on this rather than on DEMO_MODE, which is a caller-supplied
# string that no one is obliged to set truthfully.
PUBLIC_CHAIN_IDS = frozenset({1, 11155111})

ARTIFACT_RELATIVE_PATH = Path("contracts/out/ImpactRegistry.sol/ImpactRegistry.json")


def _resolve_artifact_path(configured: str | None) -> Path:
    """Locate the compiled contract artifact without depending on the working directory.

    The default was "../../contracts/out/...", which only resolved when the process
    happened to start in apps/api -- and the same value is pinned in checked-out .env
    files, so honouring an override verbatim does not escape the problem. A relative
    setting is therefore tried against the working directory and then against the
    repository root, found by walking up from this module as the .env lookup does. An
    absolute path is always taken as given.
    """
    candidates: list[Path] = []
    if configured:
        given = Path(configured)
        if given.is_absolute():
            return given
        candidates.append(given)
        candidates.extend(parent / given for parent in Path(__file__).resolve().parents)
    candidates.extend(
        parent / ARTIFACT_RELATIVE_PATH for parent in Path(__file__).resolve().parents
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Nothing found: name the conventional location so the error is actionable.
    return Path(configured) if configured else ARTIFACT_RELATIVE_PATH


def _load_repository_dotenv() -> None:
    """Load the repository .env when running from a checkout.

    Deployed images have no .env and a shallower path, so the repository root may not
    exist above this module. Configuration then comes from the environment alone.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        dotenv = candidate / ".env"
        if dotenv.is_file():
            load_dotenv(dotenv)
            return


_load_repository_dotenv()


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    demo_mode: str = "local"
    persistence_mode: str = "postgres"
    database_url: str = "postgresql+psycopg://impactgraph:impactgraph@localhost:55432/impactgraph"
    chain_id: int = 31337
    chain_name: str = "Anvil Local EVM"
    rpc_url: str = "http://127.0.0.1:8545"
    registry_address: str = ""
    evm_sender_address: str = ""
    verifier_wallet_address: str = ""
    contract_artifact_path: Path = ARTIFACT_RELATIVE_PATH
    explorer_url: str = ""
    confirmations_required: int = 1
    evidence_storage_path: Path = Path("./var/evidence")
    max_upload_bytes: int = 10_485_760
    allowed_upload_types: tuple[str, ...] = (
        "application/pdf",
        "image/jpeg",
        "image/png",
        "text/plain",
    )
    ai_provider: str = "mock"
    extraction_model: str = "thinkingmachines/Inkling-Small"
    worker_metrics_port: int = 9100

    @classmethod
    def from_env(cls) -> Settings:
        value = cls(
            app_env=os.getenv("APP_ENV", "development"),
            demo_mode=os.getenv("DEMO_MODE", "local"),
            persistence_mode=os.getenv("PERSISTENCE_MODE", "postgres"),
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            chain_id=int(os.getenv("CHAIN_ID", "31337")),
            chain_name=os.getenv("CHAIN_NAME", "Anvil Local EVM"),
            rpc_url=os.getenv("RPC_URL", "http://127.0.0.1:8545"),
            registry_address=os.getenv("IMPACT_REGISTRY_ADDRESS", ""),
            evm_sender_address=os.getenv("EVM_SENDER_ADDRESS", ""),
            verifier_wallet_address=os.getenv("VERIFIER_WALLET_ADDRESS", ""),
            contract_artifact_path=_resolve_artifact_path(
                os.getenv("CONTRACT_ARTIFACT_PATH")
            ),
            explorer_url=os.getenv("BLOCK_EXPLORER_URL", ""),
            confirmations_required=int(os.getenv("BLOCKCHAIN_CONFIRMATIONS_REQUIRED", "1")),
            evidence_storage_path=Path(os.getenv("EVIDENCE_STORAGE_PATH", "./var/evidence")),
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", "10485760")),
            allowed_upload_types=tuple(
                item.strip()
                for item in os.getenv(
                    "ALLOWED_UPLOAD_TYPES",
                    "application/pdf,image/jpeg,image/png,text/plain",
                ).split(",")
                if item.strip()
            ),
            ai_provider=os.getenv("AI_PROVIDER", "mock"),
            extraction_model=os.getenv(
                "EXTRACTION_MODEL", "thinkingmachines/Inkling-Small"
            ),
            worker_metrics_port=int(os.getenv("WORKER_METRICS_PORT", "9100")),
        )
        if value.demo_mode == "sepolia" and value.chain_id != 11155111:
            raise ValueError("DEMO_MODE=sepolia requires CHAIN_ID=11155111")
        if value.demo_mode == "sepolia" and (not value.rpc_url or not value.registry_address):
            raise ValueError("Sepolia requires RPC_URL and IMPACT_REGISTRY_ADDRESS")
        if value.confirmations_required < 1:
            raise ValueError("BLOCKCHAIN_CONFIRMATIONS_REQUIRED must be positive")
        if value.ai_provider not in {"mock", "tinker"}:
            raise ValueError("AI_PROVIDER must be mock or tinker")
        if value.ai_provider == "tinker" and not os.getenv("TINKER_API_KEY"):
            # Refuse at startup rather than on the first upload, which would fail in
            # front of an operator with a document they had already waited to hash.
            raise ValueError("AI_PROVIDER=tinker requires TINKER_API_KEY")
        if value.persistence_mode not in {"postgres", "memory"}:
            raise ValueError("PERSISTENCE_MODE must be postgres or memory")
        return value
