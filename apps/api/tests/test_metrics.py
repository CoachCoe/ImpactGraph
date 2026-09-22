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


def test_the_metrics_endpoint_serves_the_prometheus_exposition_format(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "impactgraph_outbox_pending" in response.text


def test_an_integrity_check_counts_the_outcome_it_persisted(client):
    from impactgraph.metrics import integrity_checks

    before = integrity_checks.labels(result="MATCH")._value.get()
    assert client.post("/evidence/ev-inv-8291/verify-integrity").status_code == 200
    assert integrity_checks.labels(result="MATCH")._value.get() == before + 1
