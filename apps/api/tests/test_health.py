"""Liveness and readiness answer different questions and must not be conflated.

The container probe polls liveness: a transient database or RPC fault should not get a
working process replaced. The compose dependency gates poll readiness, because what they
actually mean is that the API can serve.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from impactgraph import main
from impactgraph.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_liveness_answers_without_touching_any_dependency(client, monkeypatch):
    def explode() -> None:
        raise AssertionError("liveness must not probe dependencies")

    monkeypatch.setattr(main, "READINESS_PROBES", {"database": explode})
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_the_original_health_path_still_answers(client):
    """Deployed containers and older scripts poll /health; it stays a liveness alias."""
    assert client.get("/health").status_code == 200


def test_readiness_is_ready_when_every_probe_passes(client, monkeypatch):
    monkeypatch.setattr(main, "READINESS_PROBES", {"database": lambda: None})
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["components"]["database"]["status"] == "ok"


def test_readiness_reports_503_and_names_the_failed_component(client, monkeypatch):
    def unavailable() -> None:
        raise ConnectionError("chain unreachable")

    monkeypatch.setattr(
        main, "READINESS_PROBES", {"database": lambda: None, "chain": unavailable}
    )
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["components"]["database"]["status"] == "ok"
    assert body["components"]["chain"]["status"] == "unavailable"


def test_a_failing_probe_never_leaks_its_message(client, monkeypatch):
    """A database or RPC URL in an exception may carry credentials."""

    def unavailable() -> None:
        raise ConnectionError("postgresql://user:secret@db:5432/impactgraph unreachable")

    monkeypatch.setattr(main, "READINESS_PROBES", {"database": unavailable})
    body = json.dumps(client.get("/health/ready").json())
    assert "secret" not in body
    assert "ConnectionError" in body


def test_a_probe_that_raises_does_not_produce_a_500(client, monkeypatch):
    def broken() -> None:
        raise RuntimeError("probe itself is broken")

    monkeypatch.setattr(main, "READINESS_PROBES", {"database": broken})
    assert client.get("/health/ready").status_code == 503


def test_the_chain_probe_refuses_when_no_registry_is_configured():
    """The suite runs with IMPACT_REGISTRY_ADDRESS empty, so this needs no network."""
    with pytest.raises(RuntimeError, match="registry address"):
        main._probe_chain()


def test_the_database_and_storage_probes_pass_against_the_test_sandbox():
    main._probe_database()
    main._probe_evidence_storage()
