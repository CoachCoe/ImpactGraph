"""The connected path a claim rests on, and the edges that must not count towards it.

The policy's PROVENANCE_COMPLETE requirement was only ever exercised through a stubbed
boolean, so the walk that produces it -- the thing that decides whether a claim is
connected all the way back to a contribution -- had no test of its own.
"""
from __future__ import annotations

import uuid

import pytest

from impactgraph.persistence import ProvenanceEdgeRecord
from impactgraph.verification import claim_provenance_complete
from tests.support import memory_session

#: Funding -> Allocation -> Transaction -> Delivery -> Outcome -> Claim, with one evidence
#: object shared between the claim and the delivery. Breaking any link breaks the chain.
CHAIN = [
    ("out-1", "OUTCOME", "claim-1", "SUPPORTS"),
    ("ev-1", "EVIDENCE", "claim-1", "SUPPORTS"),
    ("del-1", "DELIVERY", "out-1", "PRODUCES"),
    ("ev-1", "EVIDENCE", "del-1", "EVIDENCES"),
    ("tx-1", "FINANCIAL_TRANSACTION", "del-1", "SUPPORTS"),
    ("alloc-1", "ALLOCATION", "tx-1", "PAYS"),
    ("fund-1", "FUNDING", "alloc-1", "FUNDS"),
]


def _graph(*, supersede: tuple[str, str] | None = None, omit: tuple[str, str] | None = None):
    session = memory_session()
    with session.begin():
        for source, source_type, target, relationship in CHAIN:
            if (source, relationship) == omit:
                continue
            session.add(
                ProvenanceEdgeRecord(
                    source_id=source,
                    source_type=source_type,
                    target_id=target,
                    target_type="CLAIM",
                    relationship=relationship,
                    superseded_by=uuid.uuid4() if (source, relationship) == supersede else None,
                )
            )
        # Another programme's records, which must never contribute to this claim's path.
        for index in range(12):
            session.add(
                ProvenanceEdgeRecord(
                    source_id=f"other-ev-{index}",
                    source_type="EVIDENCE",
                    target_id=f"other-claim-{index}",
                    target_type="CLAIM",
                    relationship="SUPPORTS",
                )
            )
    return session


def test_a_claim_connected_back_to_a_contribution_is_complete():
    session = _graph()
    with session.begin():
        assert claim_provenance_complete(session, "claim-1") is True


@pytest.mark.parametrize(
    "link",
    [
        ("out-1", "SUPPORTS"),
        ("ev-1", "SUPPORTS"),
        ("del-1", "PRODUCES"),
        ("tx-1", "SUPPORTS"),
        ("alloc-1", "PAYS"),
        ("fund-1", "FUNDS"),
    ],
)
def test_every_link_in_the_chain_is_load_bearing(link):
    """If any one of these can be removed without the claim failing, the requirement is
    not checking what it claims to check."""
    session = _graph(omit=link)
    with session.begin():
        assert claim_provenance_complete(session, "claim-1") is False


@pytest.mark.parametrize(
    "link",
    [("out-1", "SUPPORTS"), ("del-1", "PRODUCES"), ("fund-1", "FUNDS")],
)
def test_a_superseded_edge_does_not_hold_the_chain_together(link):
    """A superseded edge is a statement that has been replaced. Counting one would let a
    claim rest on a path that no longer exists."""
    session = _graph(supersede=link)
    with session.begin():
        assert claim_provenance_complete(session, "claim-1") is False


def test_another_claims_edges_do_not_complete_this_one():
    session = _graph()
    with session.begin():
        assert claim_provenance_complete(session, "other-claim-3") is False


def test_a_claim_nobody_has_filed_anything_against_is_not_complete():
    session = _graph()
    with session.begin():
        assert claim_provenance_complete(session, "claim-does-not-exist") is False


def test_the_walk_does_not_read_edges_it_does_not_need(monkeypatch):
    """The scoped walk exists because reading the whole table put the cost of a public
    route in proportion to the size of the database rather than the length of one chain.
    A regression here is invisible in behaviour and only shows up under load."""
    from impactgraph import verification

    seen: list[int] = []
    original = verification.claim_subgraph

    def counting(session, claim_id):
        reachable, edges = original(session, claim_id)
        seen.append(len(edges))
        return reachable, edges

    monkeypatch.setattr(verification, "claim_subgraph", counting)
    session = _graph()
    with session.begin():
        claim_provenance_complete(session, "claim-1")

    # Seven edges in the chain, twelve belonging to other claims.
    assert seen == [len(CHAIN)]
