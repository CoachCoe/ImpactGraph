"""An open-banking adapter behind the provider Protocol ADR-007 defined.

**No live bank has been connected.** This is written against TrueLayer's documented
responses and tested against recorded ones. That is an honest gap, not a completed item,
and it is stated here as well as in ADR-013 so nobody reads the presence of this file as
evidence the connection works.

Read-only by construction: no method here initiates anything, and a credential granting a
payment scope is refused before it is ever stored (`credentials.store`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .domain import Money
from .financial import ProviderTransaction

#: TrueLayer reports a transaction before it settles. Only a settled one is a fact about
#: the world, and a pending one must not support a claim -- so the adapter reports the
#: status and the ingestion decides, rather than the adapter quietly dropping rows.
SETTLED_STATUSES = frozenset({"BOOKED", "SETTLED"})


@dataclass(frozen=True)
class TrueLayerTransaction:
    """One row as the provider reports it, before it becomes a ProviderTransaction."""

    transaction_id: str
    amount_minor: int
    currency: str
    timestamp: str
    description: str
    merchant_name: str
    status: str

    @property
    def settled(self) -> bool:
        return self.status.upper() in SETTLED_STATUSES


def _minor_units(amount: float | str, currency: str) -> int:
    """Convert a provider's decimal amount to integer minor units.

    Providers report money as a JSON number. Binary floating point cannot hold 10.10, so
    the string form is parsed and scaled by the currency's exponent rather than multiplied
    as a float -- `Money` refuses floats for exactly this reason and it would be strange
    to launder one in on the way to it.
    """
    from decimal import Decimal

    exponent = 0 if currency.upper() in {"JPY", "KRW", "VND"} else 2
    scaled = Decimal(str(amount)).scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} {currency} is not a whole number of minor units")
    return int(scaled)


class TrueLayerFinancialDataProvider:
    """Reads a statement for one programme's connected account.

    The HTTP call is injected rather than built here, so the mapping this class exists for
    is testable without a network and without credentials -- which is also what lets CI
    run green, as ADR-013 requires.
    """

    name = "truelayer"

    def __init__(
        self,
        *,
        fetch: Callable[[str], list[dict[str, Any]]],
        account_for: Callable[[str], str | None],
    ) -> None:
        self._fetch = fetch
        self._account_for = account_for

    def fetch_statement(self, program_ref: str) -> list[ProviderTransaction]:
        account = self._account_for(program_ref)
        if account is None:
            return []
        return [
            self._to_provider_transaction(row, program_ref)
            for row in (self._parse(item) for item in self._fetch(account))
            # A pending transaction has not happened yet. Importing it would let a claim
            # rest on money that has not moved and might never.
            if row.settled
        ]

    @staticmethod
    def _parse(item: dict[str, Any]) -> TrueLayerTransaction:
        merchant = item.get("merchant_name") or item.get("counter_party", {}).get("name") or ""
        return TrueLayerTransaction(
            transaction_id=str(item["transaction_id"]),
            amount_minor=_minor_units(item["amount"], item["currency"]),
            currency=str(item["currency"]).upper(),
            timestamp=str(item["timestamp"]),
            description=str(item.get("description") or ""),
            merchant_name=str(merchant),
            status=str(item.get("status") or "BOOKED"),
        )

    def _to_provider_transaction(
        self, row: TrueLayerTransaction, program_ref: str
    ) -> ProviderTransaction:
        return ProviderTransaction(
            # The provider's own identifier, so re-importing a statement records nothing
            # new. Prefixed with the provider because two feeds can use the same id space.
            source_ref=f"{self.name}-{row.transaction_id}",
            payer_ref=program_ref,
            payee_ref=row.merchant_name or "unknown-payee",
            payee_name=row.merchant_name or "Unidentified payee",
            # TrueLayer reports a debit as a negative amount. The sign says which
            # direction it went, and Money refuses negatives, so it is taken here and the
            # direction is the payer/payee pair rather than a minus sign.
            amount=Money(abs(row.amount_minor), row.currency),
            occurred_on=row.timestamp[:10],
            memo=row.description,
        )
