"""What an operator needs to see before someone tells them something is wrong.

The outbox is the one place this system can fail silently: intent is persisted, nothing
submits it, and every read model keeps answering exactly as before. Depth and age of the
oldest unsubmitted row are the two numbers that make that visible.

Counters here record committed state transitions, never attempts. A retry that rolls back
must not leave a total claiming work that did not happen.
"""

from __future__ import annotations

from collections.abc import Iterator

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

#: Requests and RPC calls are labelled by operation, which is a closed set. Never by
#: transaction hash, entity id, wallet, correlation id or exception text.
REGISTRY = CollectorRegistry()

outbox_submissions = Counter(
    "impactgraph_outbox_submissions_total",
    "Outbox rows submitted to the chain, counted once the transaction hash is committed.",
    ["topic", "outcome"],
    registry=REGISTRY,
)

chain_operations = Counter(
    "impactgraph_chain_operations_total",
    "Blockchain operations whose observed status was committed to the database.",
    ["operation_type", "status"],
    registry=REGISTRY,
)

integrity_checks = Counter(
    "impactgraph_evidence_integrity_checks_total",
    "Evidence integrity verifications, by the outcome that was persisted.",
    ["result"],
    registry=REGISTRY,
)

rpc_calls = Histogram(
    "impactgraph_rpc_call_seconds",
    "Time spent in a single JSON-RPC call to the configured registry.",
    ["operation", "outcome"],
    registry=REGISTRY,
)


class OutboxCollector(Collector):
    """Depth and age of the unprocessed outbox, read at scrape time.

    A cheap aggregate rather than a row scan, and a failure to read it must not take the
    whole scrape down: an operator investigating an incident should still get the
    counters that are held in memory.
    """

    def __init__(self, read_backlog) -> None:
        self._read_backlog = read_backlog

    def collect(self) -> Iterator[GaugeMetricFamily]:
        depth = GaugeMetricFamily(
            "impactgraph_outbox_pending",
            "Outbox rows that have not been submitted.",
        )
        age = GaugeMetricFamily(
            "impactgraph_outbox_oldest_pending_seconds",
            "Age of the oldest unsubmitted outbox row. Zero when the outbox is empty.",
        )
        try:
            pending, oldest_seconds = self._read_backlog()
        except Exception:  # noqa: BLE001 -- a scrape reports what it can, and never raises
            return
        depth.add_metric([], pending)
        age.add_metric([], oldest_seconds)
        yield depth
        yield age


def render() -> bytes:
    return generate_latest(REGISTRY)
