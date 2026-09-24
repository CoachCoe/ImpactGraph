"""The outbox is the one place this system fails silently.

Intent is persisted, nothing submits it, and every read model keeps answering exactly as
before. Depth and age of the oldest unsubmitted row are what make that visible, so they
have to be right when the outbox is empty, when it is backed up, and when the database
cannot be read at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest

from impactgraph import main
from impactgraph.main import app
from impactgraph.metrics import OutboxCollector
from impactgraph.persistence import OutboxRecord


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def scrape(read_backlog) -> str:
    registry = CollectorRegistry()
    registry.register(OutboxCollector(read_backlog))
    return generate_latest(registry).decode()


def sample(rendered: str, name: str) -> float:
    for line in rendered.splitlines():
        if line.startswith(f"{name} "):
            return float(line.split(" ", 1)[1])
    raise AssertionError(f"{name} was not exposed:\n{rendered}")


def test_an_empty_outbox_reports_zero_rather_than_nothing():
    """Absent and empty read identically on a graph, and only one of them is true."""
    rendered = scrape(lambda: (0, 0.0))
    assert sample(rendered, "impactgraph_outbox_pending") == 0.0
    assert sample(rendered, "impactgraph_outbox_oldest_pending_seconds") == 0.0


def test_a_backed_up_outbox_reports_its_depth_and_age():
    rendered = scrape(lambda: (7, 412.5))
    assert sample(rendered, "impactgraph_outbox_pending") == 7.0
    assert sample(rendered, "impactgraph_outbox_oldest_pending_seconds") == 412.5


def test_an_unreadable_database_does_not_take_the_whole_scrape_down():
    """An operator mid-incident still needs the counters held in memory."""

    def unreadable():
        raise ConnectionError("database is gone")

    assert "impactgraph_outbox_pending" not in scrape(unreadable)


def test_the_backlog_reader_measures_the_oldest_unsubmitted_row():
    factory = main.session_factory
    assert factory is not None
    with factory.begin() as session:
        session.add(
            OutboxRecord(
                topic="blockchain.register_evidence",
                payload={},
                correlation_id="corr-metrics",
                created_at=datetime.now(UTC) - timedelta(seconds=90),
            )
        )
        session.add(
            OutboxRecord(
                topic="blockchain.register_evidence",
                payload={},
                correlation_id="corr-metrics",
                processed_at=datetime.now(UTC),
                created_at=datetime.now(UTC) - timedelta(seconds=3600),
            )
        )
    try:
        pending, oldest_seconds = main._outbox_backlog()
        # The processed row is an hour old and must not be counted or timed.
        assert pending == 1
        assert 60 < oldest_seconds < 600
    finally:
        with factory.begin() as session:
            for record in session.query(OutboxRecord).filter(
                OutboxRecord.correlation_id == "corr-metrics"
            ):
                session.delete(record)


def _scraper() -> TestClient:
    """A caller on the network the API is deployed to, which is where a scraper lives."""
    return TestClient(app, client=("10.0.0.7", 51234))


def test_the_metrics_endpoint_serves_the_prometheus_exposition_format():
    response = _scraper().get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "impactgraph_outbox_pending" in response.text


def test_metrics_are_not_served_to_the_open_internet(client):
    """Backlog depth, error rates and per-route latency are an operational map: they say
    which part of this is struggling and when a queue is worth flooding. This was served
    to anyone who asked for it."""
    refused = client.get("/metrics")
    assert refused.status_code == 403
    assert "impactgraph_outbox_pending" not in refused.text


def test_a_public_address_near_the_private_range_is_still_public():
    """172.2.x.x is not RFC1918 -- the block starts at 172.16 -- and the prefix list this
    replaced accepted it. One typo away from the intended range and the metrics were
    readable from the internet."""
    assert TestClient(app, client=("172.2.3.4", 5555)).get("/metrics").status_code == 403
    assert TestClient(app, client=("172.20.1.1", 5555)).get("/metrics").status_code == 200


def test_a_caller_without_an_address_is_treated_as_external():
    """A scraper has an IP. Something arriving without one is not a case to hold the door
    open for -- and the default TestClient host is exactly that shape."""
    assert TestClient(app).get("/metrics").status_code == 403


def test_a_configured_token_lets_a_scraper_in_from_anywhere(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "scrape-me-6f2b")
    remote = TestClient(app, client=("8.8.8.8", 4444))

    assert remote.get("/metrics").status_code == 401
    assert remote.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401

    allowed = remote.get("/metrics", headers={"Authorization": "Bearer scrape-me-6f2b"})
    assert allowed.status_code == 200
    assert "impactgraph_outbox_pending" in allowed.text


def test_a_token_replaces_the_network_check_rather_than_adding_to_it(monkeypatch):
    """Otherwise setting a token to scrape remotely would silently keep the endpoint open
    to everything on the local network that does not present one."""
    monkeypatch.setenv("METRICS_TOKEN", "scrape-me-6f2b")
    assert _scraper().get("/metrics").status_code == 401


def test_an_integrity_check_counts_the_outcome_it_persisted(client):
    from impactgraph.metrics import integrity_checks

    before = integrity_checks.labels(result="MATCH")._value.get()
    assert client.post("/evidence/ev-inv-8291/verify-integrity").status_code == 200
    assert integrity_checks.labels(result="MATCH")._value.get() == before + 1


def test_an_operation_awaiting_confirmations_is_not_counted_on_every_poll():
    """A total that grows because the worker looked is a measure of the polling interval.

    An operation stays SUBMITTED until it reaches the required confirmation depth, and
    `observe_submitted` re-reads it on every tick. Only the transition counts.
    """

    from impactgraph.blockchain import MockBlockchainService
    from impactgraph.domain import Role
    from impactgraph.metrics import chain_operations
    from impactgraph.services import (
        ApplicationActor,
        EvidenceApplicationService,
        mark_evidence_reviewed,
    )
    from impactgraph.worker import BlockchainOutboxWorker
    from tests.support import memory_factory

    factory = memory_factory()

    service = EvidenceApplicationService(chain_id=31337)
    actor = ApplicationActor("operator-1", Role.OPERATOR)
    with factory.begin() as db:
        service.create_uploaded(
            db, actor=actor, evidence_id="ev-metrics", project_ref="project-water-12",
            evidence_type="INVOICE", storage_uri="file:///safe/ev-metrics",
            content_hash="sha256:" + "a" * 64, mime_type="application/pdf",
            visibility="RESTRICTED", correlation_id="c", idempotency_key="k1",
        )
        from sqlalchemy import select

        from impactgraph.persistence import EvidenceRecord
        record = db.scalar(select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-metrics"))
        record.metadata_json = {"programId": "program-clean-water-kenya-2026"}
        mark_evidence_reviewed(db, "ev-metrics")
    with factory.begin() as db:
        service.request_registration(
            db, actor=actor, evidence_id="ev-metrics", correlation_id="c", idempotency_key="k2"
        )

    # Two confirmations required, mock reports one, so the operation never leaves SUBMITTED.
    worker = BlockchainOutboxWorker(
        session_factory=factory, blockchain=MockBlockchainService(), confirmations_required=2
    )
    worker.submit_pending()
    before = chain_operations.labels(operation_type="REGISTER_EVIDENCE", status="SUBMITTED")._value.get()
    for _ in range(3):
        worker.observe_submitted()
    after = chain_operations.labels(operation_type="REGISTER_EVIDENCE", status="SUBMITTED")._value.get()
    assert after == before, f"three polls added {after - before} to a counter of transitions"


def test_a_taken_metrics_port_does_not_stop_the_worker(monkeypatch):
    """Telemetry must never be able to stop the thing it observes."""
    from impactgraph import cli

    def port_in_use(*args, **kwargs):
        raise OSError(48, "Address already in use")

    monkeypatch.setattr(cli, "start_http_server", port_in_use)
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls: cls(registry_address="0x1")))

    started = {}

    class StopAfterFirstTick(Exception):
        pass

    def fake_worker(**kwargs):
        started["yes"] = True
        raise StopAfterFirstTick

    monkeypatch.setattr(cli, "BlockchainOutboxWorker", fake_worker)
    monkeypatch.setattr(cli, "create_session_factory", lambda url: None)
    monkeypatch.setattr(
        cli.EvmBlockchainService, "from_foundry_artifact", classmethod(lambda cls, **kw: None)
    )

    with pytest.raises(StopAfterFirstTick):
        cli.worker_loop(1.0)
    assert started, "the worker never got past the metrics server"
