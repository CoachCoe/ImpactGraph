"""What an anonymous caller can make this server do, and how often.

These routes are public on purpose -- checking the record without an account is the
product -- but public and expensive and unbounded is a different thing from public.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from impactgraph.main import app
from impactgraph.public_api import RATE_LIMIT_REQUESTS, limiter

EVIDENCE = "ev-inv-8291"
PROGRAM = "program-clean-water-kenya-2026"
CLAIM = "claim-water-12-200"


@pytest.fixture
def caller() -> TestClient:
    limiter.forget()
    return TestClient(app, client=("198.51.100.4", 5555))


def _exhaust(client: TestClient, method: str, path: str) -> int:
    """Call until refused, and return how many were allowed."""
    for attempt in range(RATE_LIMIT_REQUESTS + 5):
        response = getattr(client, method)(path)
        if response.status_code == 429:
            return attempt
    return -1


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", f"/evidence/{EVIDENCE}/verify-integrity"),
        ("get", f"/export/programs/{PROGRAM}/money-trail.csv"),
        ("get", f"/export/programs/{PROGRAM}/outcomes.csv"),
        ("get", f"/export/claims/{CLAIM}/provenance.csv"),
    ],
)
def test_an_anonymous_caller_cannot_hammer_an_expensive_route(caller, method, path):
    """verify-integrity reads an object out of storage and hashes it; an export
    serialises a whole programme. Both answered anybody, as often as they asked."""
    allowed = _exhaust(caller, method, path)
    assert allowed == RATE_LIMIT_REQUESTS, f"{path} allowed {allowed}"


def test_the_refusal_says_how_long_to_wait(caller):
    path = f"/export/programs/{PROGRAM}/money-trail.csv"
    _exhaust(caller, "get", path)
    refused = caller.get(path)

    assert refused.status_code == 429
    assert refused.headers["Retry-After"]
    assert refused.json()["detail"]["code"] == "RATE_LIMITED"


def test_the_allowance_is_per_caller_not_global(caller):
    """A shared counter would let one noisy client lock everyone else out, which is a
    denial of service implemented on purpose."""
    path = f"/export/programs/{PROGRAM}/outcomes.csv"
    _exhaust(caller, "get", path)
    assert caller.get(path).status_code == 429

    somebody_else = TestClient(app, client=("203.0.113.77", 5555))
    assert somebody_else.get(path).status_code == 200


def test_a_normal_reader_is_never_refused(caller):
    """Sixty in a minute has to be comfortably above what following one claim through
    its records costs, or the limit is a bug report waiting to happen."""
    for path in (
        f"/claims/{CLAIM}",
        f"/claims/{CLAIM}/provenance",
        f"/claims/{CLAIM}/verification",
        f"/financial/programs/{PROGRAM}",
        f"/export/programs/{PROGRAM}/money-trail.csv",
        f"/evidence/{EVIDENCE}/verify-integrity",
    ):
        method = caller.post if "verify-integrity" in path else caller.get
        assert method(path).status_code == 200, path
