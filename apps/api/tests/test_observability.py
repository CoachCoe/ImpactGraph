"""Correlation context must not outlive, or erase, the work it describes.

A leaked context variable attributes one caller's log lines to another, which is worse
than having no correlation at all in a system whose product claim is auditability.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest
import structlog
from fastapi.testclient import TestClient

from impactgraph.main import app
from impactgraph.observability import configure_logging, correlation_context


@pytest.fixture(autouse=True)
def _clean_context():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def json_logs():
    """Read back exactly what a deployment would emit, then restore the handlers.

    The stream is an explicit buffer rather than pytest's captured stdout: pytest's own
    logging plugin installs root handlers per test phase, so a handler bound to
    `sys.stdout` races with it. Restoring the previous handlers matters because
    `configure_logging` replaces them, and a test that left its own in place would send
    every later test's records into a closed stream.

    Real output rather than `structlog.testing.capture_logs`, which replaces the whole
    processor chain: `merge_contextvars` never runs under it, so the correlation ID is
    absent from what it captures.
    """
    root = logging.getLogger()
    previous = root.handlers[:]
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    def read() -> list[dict]:
        entries = []
        for line in buffer.getvalue().strip().splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    yield read
    root.handlers = previous


def bound() -> dict:
    return structlog.contextvars.get_contextvars()


def test_context_is_bound_inside_and_released_after():
    with correlation_context("abc-123"):
        assert bound()["correlation_id"] == "abc-123"
    assert "correlation_id" not in bound()


def test_context_is_released_when_the_work_raises():
    with pytest.raises(RuntimeError), correlation_context("abc-123"):
        raise RuntimeError("submission failed")
    assert "correlation_id" not in bound()


def test_nested_context_restores_the_outer_identifier():
    with correlation_context("outer"):
        with correlation_context("inner"):
            assert bound()["correlation_id"] == "inner"
        assert bound()["correlation_id"] == "outer"


def test_consecutive_units_of_work_do_not_inherit_the_previous_identifier():
    with correlation_context("first"):
        pass
    with correlation_context("second"):
        assert bound()["correlation_id"] == "second"


def test_concurrent_tasks_do_not_share_correlation_context():
    async def unit(value: str, hold: float) -> str:
        with correlation_context(value):
            await asyncio.sleep(hold)
            return bound()["correlation_id"]

    async def both() -> list[str]:
        # The first sleeps longer, so the second binds and completes while the first is
        # suspended inside its own context.
        return await asyncio.gather(unit("first", 0.02), unit("second", 0.0))

    assert asyncio.run(both()) == ["first", "second"]


def test_request_correlation_id_is_echoed_and_honoured():
    response = TestClient(app).get("/health", headers={"x-correlation-id": "supplied-id"})
    assert response.headers["x-correlation-id"] == "supplied-id"


def test_each_request_gets_its_own_correlation_id():
    client = TestClient(app)
    first = client.get("/health").headers["x-correlation-id"]
    second = client.get("/health").headers["x-correlation-id"]
    assert first != second


def test_request_does_not_leave_context_bound_afterwards():
    TestClient(app).get("/health")
    assert "correlation_id" not in bound()


def test_log_lines_inside_a_request_carry_the_correlation_id(json_logs):
    TestClient(app).post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "wrong"},
        headers={"x-correlation-id": "trace-me"},
    )
    failures = [item for item in json_logs() if item.get("event") == "auth.login_failed"]
    assert failures, "the failed login should be logged"
    assert all(item["correlation_id"] == "trace-me" for item in failures)


def test_failed_login_log_carries_no_account_identifier(json_logs):
    TestClient(app).post(
        "/auth/login",
        json={"email": "someone@example.com", "password": "wrong"},
    )
    rendered = json.dumps(json_logs())
    assert "someone@example.com" not in rendered
    assert "wrong" not in rendered


def test_uvicorn_records_render_in_the_same_json_shape(json_logs):
    logging.getLogger("uvicorn.error").warning("uvicorn speaks the same shape")
    assert any(item.get("event") == "uvicorn speaks the same shape" for item in json_logs())
