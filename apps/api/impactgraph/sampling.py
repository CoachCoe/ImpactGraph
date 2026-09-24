"""Choosing who to ask, in a way an operator cannot steer.

If the organisation being checked picks the households, the answers are worth nothing. So
the sample is drawn by the platform from a recorded seed, which makes it reproducible: a
third party holding the seed and the population can re-derive exactly the same selection
and see that it was not curated.

The method and the confidence are stored alongside the result, because a statistical
claim should be auditable in the same way everything else here is.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

#: Below this, an aggregate can be unpicked. Four confirmations out of five identifies the
#: dissenter to anyone who knows who was asked, which in a small village is everyone.
MINIMUM_REPORTABLE_SAMPLE = 8

#: z for a 95% two-sided interval. Named rather than inlined because the number means
#: something and a reader should be able to check it.
Z_95 = 1.96


@dataclass(frozen=True)
class Sample:
    """Who was selected, and enough about how to re-derive it."""

    seed: str
    method: str
    population: int
    selected: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.selected)


def required_size(population: int, *, margin: float = 0.1, confidence_z: float = Z_95) -> int:
    """How many responses are needed for a margin of error at a given confidence.

    The finite population correction matters here: a programme reaching 200 households
    needs far fewer than the textbook infinite-population figure, and asking more people
    than necessary is a cost borne by people who did not volunteer for it.
    """
    if population <= 0:
        return 0
    # Worst-case proportion of 0.5, which is the conservative assumption when nothing is
    # known about how the answers will split.
    numerator = (confidence_z**2) * 0.25
    infinite = numerator / (margin**2)
    corrected = infinite / (1 + (infinite - 1) / population)
    return min(population, math.ceil(corrected))


def draw(population: list[str], *, seed: str, size: int) -> Sample:
    """Select `size` members deterministically from `seed`.

    Ordering by a keyed hash rather than shuffling with a seeded generator: the result
    then depends only on the seed and the member identifiers, not on the language's
    random implementation, which a third party re-deriving this years later would have to
    match exactly.
    """
    ordered = sorted(
        population,
        key=lambda member: hashlib.sha256(f"{seed}:{member}".encode()).hexdigest(),
    )
    return Sample(
        seed=seed,
        method=(
            "sha256(seed:member) ascending, first n. Deterministic from the seed, so the "
            "selection can be re-derived and shown not to have been curated."
        ),
        population=len(population),
        selected=tuple(ordered[: max(0, min(size, len(population)))]),
    )


def confirmation_rate(confirmed: int, responded: int) -> tuple[float, float]:
    """The proportion confirming, and the half-width of its 95% interval.

    Reported as an interval because a bare percentage from a sample invites being read as
    a count of the whole population, which is the error this exists to avoid.
    """
    if responded <= 0:
        return 0.0, 0.0
    proportion = confirmed / responded
    half_width = Z_95 * math.sqrt(max(proportion * (1 - proportion), 0.0) / responded)
    return proportion, half_width
