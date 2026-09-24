"""Choosing who to ask, in a way the organisation being checked cannot steer.

If the operator picks the households, the answers are worth nothing. These pin the
properties that make the selection auditable rather than merely random.
"""

from __future__ import annotations

import pytest

from impactgraph.sampling import (
    MINIMUM_REPORTABLE_SAMPLE,
    confirmation_rate,
    draw,
    required_size,
)

HOUSEHOLDS = [f"household-{index}" for index in range(200)]


def test_the_same_seed_always_draws_the_same_people():
    """Which is what lets a third party re-derive the selection and see it was not
    curated. Without it, "we sampled randomly" is an assertion."""
    first = draw(HOUSEHOLDS, seed="delivery-water-12", size=20)
    second = draw(HOUSEHOLDS, seed="delivery-water-12", size=20)
    assert first.selected == second.selected


def test_a_different_seed_draws_different_people():
    assert (
        draw(HOUSEHOLDS, seed="a", size=20).selected
        != draw(HOUSEHOLDS, seed="b", size=20).selected
    )


def test_the_selection_does_not_depend_on_the_order_it_was_given():
    """An operator handing over the population in a favourable order must not change who
    is asked."""
    forwards = draw(HOUSEHOLDS, seed="s", size=20).selected
    backwards = draw(list(reversed(HOUSEHOLDS)), seed="s", size=20).selected
    assert set(forwards) == set(backwards)


def test_the_method_is_recorded_alongside_the_result():
    sample = draw(HOUSEHOLDS, seed="s", size=10)
    assert "sha256" in sample.method
    assert sample.seed == "s"
    assert sample.population == 200
    assert sample.size == 10


def test_asking_for_more_people_than_exist_asks_everyone_once():
    sample = draw(HOUSEHOLDS[:5], seed="s", size=50)
    assert sample.size == 5
    assert len(set(sample.selected)) == 5


def test_an_empty_population_is_not_an_error():
    assert draw([], seed="s", size=10).selected == ()
    assert required_size(0) == 0


def test_a_smaller_programme_needs_proportionally_fewer_responses():
    """Asking more people than necessary is a cost borne by people who did not volunteer
    for it, so the finite population correction is not an optimisation."""
    assert required_size(40) < required_size(200) < required_size(100_000)
    assert required_size(40) <= 40


def test_a_rate_is_reported_with_its_interval():
    """A bare percentage from a sample invites being read as a count of the whole
    population, which is the error this exists to avoid."""
    proportion, half_width = confirmation_rate(37, 40)
    assert proportion == pytest.approx(0.925, abs=0.001)
    assert half_width > 0

    # Fewer responses, wider interval, for the same proportion.
    _, wide = confirmation_rate(9, 10)
    assert wide > half_width


def test_no_responses_is_not_a_hundred_percent_anything():
    assert confirmation_rate(0, 0) == (0.0, 0.0)


def test_a_sample_too_small_to_hide_a_dissenter_is_named_as_such():
    """Four confirmations out of five identifies the fifth person to anyone who knows who
    was asked, which in a small village is everyone."""
    assert MINIMUM_REPORTABLE_SAMPLE >= 8
