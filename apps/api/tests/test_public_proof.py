"""What a stranger sees, and who decides.

The provenance graph named a private individual to anyone who asked, and proof pages
would put that in front of a much larger audience. An organisation that funded a
programme is a public fact; a person's giving is their own business, and they are the
only one who can decide to be named.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.main import app, session_factory
from impactgraph.persistence import REDACTED_FUNDER, FundingRecord

OPERATOR = "operator@globalwater.example"
FUNDING = "funding-jane-10000"
PROGRAM = "program-clean-water-kenya-2026"
CLAIM = "claim-water-12-200"


@pytest.fixture
def anonymous() -> TestClient:
    return TestClient(app)


def operator_client() -> TestClient:
    client = TestClient(app)
    assert client.post(
        "/auth/login", json={"email": OPERATOR, "password": DEMO_PASSWORD}
    ).status_code == 200
    return client


def test_a_stranger_is_not_told_which_person_gave_the_money(anonymous):
    graph = anonymous.get(f"/claims/{CLAIM}/provenance").json()
    titles = [node.get("title") for node in graph["nodes"]]
    assert "Jane Smith" not in titles
    assert REDACTED_FUNDER in titles

    trail = anonymous.get(f"/financial/programs/{PROGRAM}").json()
    assert "Jane Smith" not in str(trail)

    attribution = anonymous.get(f"/financial/funding/{FUNDING}/attribution").json()
    assert attribution["funder"] == REDACTED_FUNDER


def test_the_money_is_still_followable_without_the_name(anonymous):
    """Withholding who gave it does not withhold that it was given. The amount, the date
    and the hashes are what make the trail checkable."""
    attribution = anonymous.get(f"/financial/funding/{FUNDING}/attribution").json()
    assert attribution["funder"] == REDACTED_FUNDER
    assert "Jane Smith" not in str(attribution)
    # The money is still followable: what is withheld is which person it came from.
    assert str(attribution).count("amountMinor") > 0


def test_an_organisation_that_funded_a_programme_stays_named(anonymous):
    trail = anonymous.get(f"/financial/programs/{PROGRAM}").json()
    assert "Institutional funding pool" in str(trail)


def test_an_operator_cannot_decide_that_a_donor_is_named():
    """They know the name already. What they do not have is any route that publishes it."""
    client = operator_client()
    issued = client.post(f"/funding/{FUNDING}/name-consent")
    assert issued.status_code == 201, issued.text
    assert "token" in issued.json()

    # Asking for the link publishes nothing.
    assert (
        TestClient(app).get(f"/financial/funding/{FUNDING}/attribution").json()["funder"]
        == REDACTED_FUNDER
    )

    routes = {getattr(route, "path", "") for route in app.routes}
    assert not any(
        path.startswith("/funding/") and path.endswith("/publish-name") for path in routes
    ), "an endpoint exists that lets somebody else publish a funder's name"


def test_a_funder_can_name_themselves_and_change_their_mind():
    client = operator_client()
    token = client.post(f"/funding/{FUNDING}/name-consent").json()["token"]

    published = TestClient(app).post(
        "/funding/name-consent", json={"token": token, "publish": True}
    )
    assert published.status_code == 200, published.text
    assert published.json()["shownAs"] == "Jane Smith"
    assert (
        TestClient(app).get(f"/financial/funding/{FUNDING}/attribution").json()["funder"]
        == "Jane Smith"
    )

    withdrawn = TestClient(app).post(
        "/funding/name-consent", json={"token": token, "publish": False}
    )
    assert withdrawn.json()["shownAs"] == REDACTED_FUNDER
    assert (
        TestClient(app).get(f"/financial/funding/{FUNDING}/attribution").json()["funder"]
        == REDACTED_FUNDER
    )


def test_a_link_nobody_was_given_does_not_work():
    refused = TestClient(app).post(
        "/funding/name-consent",
        json={"token": "x" * 48, "publish": True},
    )
    assert refused.status_code == 404


def test_the_consent_record_does_not_write_the_name_down_again():
    from impactgraph.persistence import AuditLogRecord

    client = operator_client()
    token = client.post(f"/funding/{FUNDING}/name-consent").json()["token"]
    TestClient(app).post("/funding/name-consent", json={"token": token, "publish": True})

    assert session_factory is not None
    with session_factory() as session:
        entry = session.scalar(
            select(AuditLogRecord)
            .where(AuditLogRecord.action == "FUNDER_NAME_PUBLISHED")
            .order_by(AuditLogRecord.created_at.desc())
        )
        assert entry is not None
        assert "Jane Smith" not in str(entry.metadata_json)


def test_existing_funding_defaults_to_the_careful_answer():
    """A preference nobody recorded is not a preference to publish."""
    assert session_factory is not None
    with session_factory() as session:
        jane = session.scalar(
            select(FundingRecord).where(FundingRecord.external_id == FUNDING)
        )
        assert jane.funder_is_organisation is False
        assert jane.publish_funder_name is False


# --- Publishing a claim, and living with having published it ---


def test_an_unpublished_claim_has_no_public_proof(anonymous):
    """Refusing rather than rendering is what makes publication a decision the
    organisation made rather than a default it was subjected to."""
    assert anonymous.get(f"/claims/{CLAIM}/proof").status_code == 404


def test_publishing_is_opt_in_and_then_permanent():
    client = operator_client()
    published = client.post(f"/claims/{CLAIM}/publish")
    assert published.status_code == 201, published.text
    assert published.json()["alreadyPublished"] is False
    assert "no way to unpublish" in published.json()["note"]

    # Publishing again is not an error and does not move the date.
    again = client.post(f"/claims/{CLAIM}/publish")
    assert again.json()["alreadyPublished"] is True
    assert again.json()["publishedAt"] == published.json()["publishedAt"]

    # And there is no route that takes it back.
    routes = {
        (getattr(route, "path", ""), method)
        for route in app.routes
        for method in getattr(route, "methods", [])
    }
    assert not any(
        "publish" in path and method in {"DELETE", "PUT"} for path, method in routes
    ), "a route exists that could withdraw a published proof"


def test_a_published_proof_shows_the_status_it_has_now(anonymous):
    """A page that kept saying VERIFIED after the claim was challenged would be the most
    damaging thing this product could ship."""
    from sqlalchemy import select as _select

    from impactgraph.persistence import ClaimRecord

    operator_client().post(f"/claims/{CLAIM}/publish")
    assert anonymous.get(f"/claims/{CLAIM}/proof").json()["claim"]["status"] == (
        "VERIFICATION_PENDING"
    )

    assert session_factory is not None
    with session_factory.begin() as session:
        session.scalar(
            _select(ClaimRecord).where(ClaimRecord.external_id == CLAIM)
        ).status = "CHALLENGED"

    assert anonymous.get(f"/claims/{CLAIM}/proof").json()["claim"]["status"] == "CHALLENGED"


def test_the_proof_says_what_it_does_not_prove(anonymous):
    """A reader landing here cold has no other way to know, and a proof page that only
    lists what it establishes is an advertisement."""
    operator_client().post(f"/claims/{CLAIM}/publish")
    proof = anonymous.get(f"/claims/{CLAIM}/proof").json()
    assert proof["proves"] and proof["doesNotProve"]
    assert any("helped anyone" in item for item in proof["doesNotProve"])
    assert any("forged invoice" in item for item in proof["doesNotProve"])


def test_the_proof_leads_with_the_organisation_that_did_the_work(anonymous):
    operator_client().post(f"/claims/{CLAIM}/publish")
    proof = anonymous.get(f"/claims/{CLAIM}/proof").json()
    assert proof["operator"]["name"] == "Global Water Initiative"
    assert proof["operator"]["program"]


def test_a_proof_does_not_name_the_individual_who_funded_it(anonymous):
    operator_client().post(f"/claims/{CLAIM}/publish")
    assert "Jane Smith" not in str(anonymous.get(f"/claims/{CLAIM}/proof").json())


def test_an_operator_cannot_publish_another_organisations_claim():
    from tests.test_ownership_scoping import make_outsider

    make_outsider()
    outsider = TestClient(app)
    outsider.post(
        "/auth/login",
        json={"email": "operator@otherwater.example", "password": DEMO_PASSWORD},
    )
    assert outsider.post(f"/claims/{CLAIM}/publish").status_code == 403


def test_a_timestamp_reads_the_same_whether_it_was_just_written_or_read_back():
    """SQLite returns a naive datetime for a timezone-aware column, so the same field came
    back with an offset when it had just been written and without one afterwards. A client
    comparing the two got different values for one moment."""
    client = operator_client()
    first = client.post(f"/claims/{CLAIM}/publish").json()["publishedAt"]
    second = client.post(f"/claims/{CLAIM}/publish").json()["publishedAt"]
    assert first == second
    assert first.endswith("+00:00")


# --- The badge an organisation puts on its own site ---


def test_a_badge_exists_only_for_a_claim_that_was_published(anonymous):
    assert anonymous.get(f"/claims/{CLAIM}/badge.svg").status_code == 404
    operator_client().post(f"/claims/{CLAIM}/publish")
    assert anonymous.get(f"/claims/{CLAIM}/badge.svg").status_code == 200


def test_a_badge_stops_claiming_verification_the_moment_it_is_withdrawn(anonymous):
    """Every copy of this badge in the world is asserting something. When the claim is
    challenged they must all stop asserting it, or the product's central promise is a lie
    held in someone else's cache."""
    from sqlalchemy import select as _select

    from impactgraph.persistence import ClaimRecord

    operator_client().post(f"/claims/{CLAIM}/publish")
    assert session_factory is not None
    with session_factory.begin() as session:
        session.scalar(_select(ClaimRecord).where(ClaimRecord.external_id == CLAIM)).status = (
            "VERIFIED"
        )
    assert "Independently verified" in anonymous.get(f"/claims/{CLAIM}/badge.svg").text

    with session_factory.begin() as session:
        session.scalar(_select(ClaimRecord).where(ClaimRecord.external_id == CLAIM)).status = (
            "CHALLENGED"
        )
    withdrawn = anonymous.get(f"/claims/{CLAIM}/badge.svg")
    assert "Independently verified" not in withdrawn.text
    assert "Verification withdrawn" in withdrawn.text


def test_a_badge_may_not_be_cached_for_longer_than_it_can_be_trusted(anonymous):
    """A long cache on this is not a performance decision, it is how long the world is
    allowed to keep believing something that may have stopped being true."""
    from impactgraph.main import BADGE_MAX_AGE_SECONDS

    operator_client().post(f"/claims/{CLAIM}/publish")
    headers = anonymous.get(f"/claims/{CLAIM}/badge.svg").headers
    assert f"max-age={BADGE_MAX_AGE_SECONDS}" in headers["cache-control"]
    assert "must-revalidate" in headers["cache-control"]
    assert BADGE_MAX_AGE_SECONDS <= 600, "a badge believed for longer than ten minutes"


def test_a_badge_shows_its_own_age(anonymous):
    """So a copy served from somebody's stale cache says when it was rendered rather than
    presenting itself as current."""
    from datetime import UTC, datetime

    operator_client().post(f"/claims/{CLAIM}/publish")
    assert datetime.now(UTC).strftime("%d %b %Y") in anonymous.get(
        f"/claims/{CLAIM}/badge.svg"
    ).text


def test_the_badge_is_an_image_a_browser_will_render(anonymous):
    operator_client().post(f"/claims/{CLAIM}/publish")
    badge = anonymous.get(f"/claims/{CLAIM}/badge.svg")
    assert badge.headers["content-type"].startswith("image/svg+xml")
    assert badge.text.startswith("<svg")
    assert 'role="img"' in badge.text and "aria-label" in badge.text


# --- The read-only API a funder or researcher queries ---


@pytest.fixture(autouse=True)
def _forget_rate_limits():
    """Each test gets its own quota; otherwise the limiter carries between them and the
    failure looks like whichever test happened to run sixty-first."""
    from impactgraph.public_api import limiter

    limiter.forget()
    yield
    limiter.forget()


def test_the_public_api_lists_only_claims_somebody_published(anonymous):
    listed = anonymous.get("/v1/claims")
    assert listed.status_code == 200
    assert listed.json()["claims"] == []

    operator_client().post(f"/claims/{CLAIM}/publish")
    published = anonymous.get("/v1/claims").json()["claims"]
    assert [item["id"] for item in published] == [CLAIM]


def test_an_unpublished_claim_is_indistinguishable_from_one_that_does_not_exist(anonymous):
    """Whether an organisation has an unpublished claim is not a public fact, so the
    answer for a private one and an imaginary one has to be the same."""
    assert anonymous.get(f"/v1/claims/{CLAIM}").status_code == 404
    assert anonymous.get("/v1/claims/claim-does-not-exist").status_code == 404


def test_the_public_api_carries_its_version(anonymous):
    """Anything anyone automates against is an interface whether it was meant to be one."""
    from impactgraph.public_api import PUBLIC_API_VERSION

    operator_client().post(f"/claims/{CLAIM}/publish")
    assert anonymous.get("/v1/claims").json()["version"] == PUBLIC_API_VERSION
    assert anonymous.get(f"/v1/claims/{CLAIM}").json()["version"] == PUBLIC_API_VERSION


def test_a_caller_is_told_what_is_left_before_they_run_out(anonymous):
    from impactgraph.public_api import RATE_LIMIT_REQUESTS

    first = anonymous.get("/v1/claims")
    assert first.headers["X-RateLimit-Limit"] == str(RATE_LIMIT_REQUESTS)
    assert int(first.headers["X-RateLimit-Remaining"]) == RATE_LIMIT_REQUESTS - 1


def test_hammering_the_public_api_is_refused_with_a_reason_and_a_retry(anonymous):
    from impactgraph.public_api import RATE_LIMIT_REQUESTS

    for _ in range(RATE_LIMIT_REQUESTS):
        assert anonymous.get("/v1/claims").status_code == 200
    refused = anonymous.get("/v1/claims")
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "RATE_LIMITED"
    assert refused.headers["Retry-After"]


def test_a_caller_cannot_hand_themselves_a_fresh_quota(anonymous):
    """X-Forwarded-For is set by whoever is calling. Believing it unconditionally meant a
    different value per request and no limit at all -- three lines of shell proved it."""
    from impactgraph.public_api import RATE_LIMIT_REQUESTS

    for attempt in range(RATE_LIMIT_REQUESTS):
        assert anonymous.get(
            "/v1/claims", headers={"X-Forwarded-For": f"203.0.113.{attempt}"}
        ).status_code == 200
    assert anonymous.get(
        "/v1/claims", headers={"X-Forwarded-For": "198.51.100.7"}
    ).status_code == 429


def test_a_configured_proxy_is_believed_about_who_is_calling(anonymous, monkeypatch):
    """Otherwise every caller behind it counts as one, and the first exhausts the quota
    for all of them."""
    from impactgraph.public_api import RATE_LIMIT_REQUESTS, trusted_proxies

    trusted_proxies.cache_clear() if hasattr(trusted_proxies, "cache_clear") else None
    monkeypatch.setenv("TRUSTED_PROXY_ADDRESSES", "testclient")

    for _ in range(RATE_LIMIT_REQUESTS):
        anonymous.get("/v1/claims", headers={"X-Forwarded-For": "203.0.113.5"})
    assert anonymous.get(
        "/v1/claims", headers={"X-Forwarded-For": "203.0.113.5"}
    ).status_code == 429
    # A different caller behind the same proxy still has their own.
    assert anonymous.get(
        "/v1/claims", headers={"X-Forwarded-For": "198.51.100.7"}
    ).status_code == 200


def test_the_limiter_does_not_grow_for_ever():
    """Every distinct client used to live for the life of the process."""
    from impactgraph.public_api import RateLimiter

    limiter = RateLimiter(requests=2, window=0.01)
    limiter._sweep_above = 4
    for index in range(50):
        limiter.check(f"client-{index}")
    time.sleep(0.02)
    limiter.check("someone-new")
    assert len(limiter._seen) < 50, "clients that went quiet were never dropped"


def test_no_public_surface_names_a_private_individual(anonymous):
    """Enumerated rather than remembered. Four call sites were found by reading and a
    fifth was not: the CSV export, which is the one surface designed to leave the building
    and be opened in somebody else's spreadsheet.
    """
    operator_client().post(f"/claims/{CLAIM}/publish")
    surfaces = {
        "provenance": f"/claims/{CLAIM}/provenance",
        "claim": f"/claims/{CLAIM}",
        "proof": f"/claims/{CLAIM}/proof",
        "public api list": "/v1/claims",
        "public api claim": f"/v1/claims/{CLAIM}",
        "money trail": f"/financial/programs/{PROGRAM}",
        "funding attribution": f"/financial/funding/{FUNDING}/attribution",
        "money trail export": f"/export/programs/{PROGRAM}/money-trail.csv",
        "outcomes export": f"/export/programs/{PROGRAM}/outcomes.csv",
        "provenance export": f"/export/claims/{CLAIM}/provenance.csv",
        "evidence export": f"/export/claims/{CLAIM}/evidence.csv",
        "badge": f"/claims/{CLAIM}/badge.svg",
    }
    leaked = []
    for name, path in surfaces.items():
        response = anonymous.get(path)
        assert response.status_code == 200, f"{name} answered {response.status_code}"
        if "Jane Smith" in response.text:
            leaked.append(name)
    assert leaked == [], f"a private individual is named by: {', '.join(leaked)}"


def test_the_consent_link_is_never_written_into_a_url():
    """A path is recorded: the access log, the proxy log, the browser history and the
    Referer of anything the page links to. Hashing the token at rest and then handing it
    to the one component guaranteed to write it down would have been pointless."""
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/funding/name-consent" in paths
    assert not any(
        path.startswith("/funding/name-consent/") for path in paths
    ), "the consent token is in a path, and paths are logged"


def test_publishing_twice_does_not_claim_to_have_created_anything():
    """The second call is a no-op -- the timestamp does not move -- and answering
    "201 Created" to it is a small lie a client eventually depends on."""
    client = operator_client()
    first = client.post(f"/claims/{CLAIM}/publish")
    assert first.status_code in (200, 201), first.text

    again = client.post(f"/claims/{CLAIM}/publish")
    assert again.status_code == 200, again.text
    assert again.json()["alreadyPublished"] is True
    assert again.json()["publishedAt"] == first.json()["publishedAt"]
