"""Mapping a bank's rows onto the provider Protocol.

No live bank has been connected. These run against recorded responses, which is what lets
CI stay green without credentials -- and is also the limit of what they prove.
"""

from __future__ import annotations

import pytest

from impactgraph.domain import Money
from impactgraph.truelayer import TrueLayerFinancialDataProvider, _minor_units

SETTLED = {
    "transaction_id": "a1b2c3",
    "amount": -92.00,
    "currency": "KES",
    "timestamp": "2026-09-14T10:32:00Z",
    "description": "CARD PAYMENT TO AQUA SYSTEM 8291",
    "merchant_name": "Aqua Systems Ltd.",
    "status": "SETTLED",
}
PENDING = {**SETTLED, "transaction_id": "d4e5f6", "status": "PENDING"}


def provider(rows, account="acc-1"):
    return TrueLayerFinancialDataProvider(
        fetch=lambda _: rows, account_for=lambda _: account
    )


def test_a_settled_payment_becomes_a_provider_transaction():
    [transaction] = provider([SETTLED]).fetch_statement("program-clean-water-kenya-2026")
    assert transaction.source_ref == "truelayer-a1b2c3"
    assert transaction.amount == Money(9200, "KES")
    assert transaction.occurred_on == "2026-09-14"
    assert transaction.payee_name == "Aqua Systems Ltd."
    assert "8291" in transaction.memo


def test_a_pending_payment_is_not_imported():
    """It has not happened yet. Importing it would let a claim rest on money that has not
    moved and might never."""
    assert provider([PENDING]).fetch_statement("program-clean-water-kenya-2026") == []
    assert len(provider([SETTLED, PENDING]).fetch_statement("p")) == 1


def test_a_programme_with_no_connected_account_reads_nothing():
    assert provider([SETTLED], account=None).fetch_statement("p") == []


def test_the_source_reference_is_the_providers_own_id():
    """Which is what makes re-importing a statement record nothing new."""
    [first] = provider([SETTLED]).fetch_statement("p")
    [again] = provider([SETTLED]).fetch_statement("p")
    assert first.source_ref == again.source_ref


def test_a_decimal_amount_never_becomes_a_float():
    """Money refuses floats because binary floating point cannot hold 10.10, and
    laundering one in on the way to it would defeat the point."""
    assert _minor_units("10.10", "GBP") == 1010
    assert _minor_units(-92.00, "KES") == -9200
    assert _minor_units("0.01", "USD") == 1
    # A currency with no minor unit is not multiplied by a hundred.
    assert _minor_units("500", "JPY") == 500


def test_an_amount_with_impossible_precision_is_refused_rather_than_rounded():
    with pytest.raises(ValueError, match="whole number of minor units"):
        _minor_units("10.005", "GBP")


def test_the_direction_is_the_payer_and_payee_rather_than_a_minus_sign():
    """Money refuses negatives, so a debit's sign has to be carried by which way round
    the parties are rather than smuggled into the amount."""
    [transaction] = provider([SETTLED]).fetch_statement("program-x")
    assert transaction.amount.amount_minor > 0
    assert transaction.payer_ref == "program-x"
    assert transaction.payee_name == "Aqua Systems Ltd."


def test_a_row_with_no_merchant_name_is_not_silently_blank():
    row = {**SETTLED, "merchant_name": None, "counter_party": {}}
    [transaction] = provider([row]).fetch_statement("p")
    assert transaction.payee_name == "Unidentified payee"


def test_a_counter_party_name_is_used_when_the_merchant_field_is_absent():
    row = {**SETTLED, "merchant_name": None, "counter_party": {"name": "Kisumu Freight Co."}}
    [transaction] = provider([row]).fetch_statement("p")
    assert transaction.payee_name == "Kisumu Freight Co."
