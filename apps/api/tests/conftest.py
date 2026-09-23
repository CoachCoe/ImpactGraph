"""Test configuration.

The suite previously forced PERSISTENCE_MODE=memory, so every API test exercised the
in-process demo store and none of them touched the persistence path the application
actually runs on. It now runs against a throwaway SQLite database, which also makes
authentication testable: sessions, users and organisations are database-backed.
"""

import base64
import os
import tempfile
from pathlib import Path

# pytest loads this file as a plugin, and a test module that imports from it would load
# it a second time under another name. Creating the sandbox only once keeps both copies
# pointing at the same database and evidence directory.
if "IMPACTGRAPH_TEST_ROOT" not in os.environ:
    os.environ["IMPACTGRAPH_TEST_ROOT"] = tempfile.mkdtemp(prefix="impactgraph-tests-")
_TMP = Path(os.environ["IMPACTGRAPH_TEST_ROOT"])

os.environ["PERSISTENCE_MODE"] = "postgres"
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_TMP / 'test.db'}"
os.environ["EVIDENCE_STORAGE_PATH"] = str(_TMP / "evidence")
os.environ["AI_PROVIDER"] = "mock"
# Generated per run, and thrown away with the sandbox. Evidence is encrypted at rest, so
# every test that stores or reads an object goes through the same path production does.
os.environ.setdefault(
    "EVIDENCE_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode()
)
TEST_ENCRYPTION_KEY = base64.b64decode(os.environ["EVIDENCE_ENCRYPTION_KEY"])
# The repository .env is loaded by config.py when running from a checkout. Clearing the
# chain settings keeps the suite hermetic: otherwise a developer's configured registry
# would make the outbox worker submit real transactions during tests.
os.environ["IMPACT_REGISTRY_ADDRESS"] = ""
os.environ["EVM_SENDER_ADDRESS"] = ""
os.environ["DEMO_MODE"] = "local"

import pytest

from impactgraph.auth import DEMO_PASSWORD, seed_demo_accounts
from impactgraph.database import create_session_factory
from impactgraph.evidence import FileEvidenceStorage
from impactgraph.persistence import Base
from impactgraph.read_model import reset_read_model, seed_read_model

_factory = create_session_factory(os.environ["DATABASE_URL"])
Base.metadata.create_all(_factory.kw["bind"])
_storage = FileEvidenceStorage(Path(os.environ["EVIDENCE_STORAGE_PATH"]), TEST_ENCRYPTION_KEY)


@pytest.fixture(autouse=True)
def fresh_database():
    """Reseed before every test so order cannot leak state between them."""
    with _factory.begin() as session:
        seed_read_model(session, _storage)
        seed_demo_accounts(session)
    yield
    with _factory.begin() as session:
        reset_read_model(session, _storage)


@pytest.fixture
def sign_in():
    """Return a helper that authenticates a TestClient as one of the demo personas."""

    def _sign_in(client, email: str, password: str = DEMO_PASSWORD):
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response.json()

    return _sign_in


OPERATOR = "operator@globalwater.example"
VERIFIER = "verifier@impactverify.example"
ADMIN = "admin@impactgraph.example"
