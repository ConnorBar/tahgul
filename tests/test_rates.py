"""Rate resolution: the effective rate is right in every override case (milestone 2 done-when)."""

import itertools
from decimal import Decimal

import pytest

from tahgul.rates import LEVELS, resolve_rate

VALUES = {
    "entry": Decimal("250"),
    "activity": Decimal("200"),
    "scope": Decimal("175"),
    "project": Decimal("150"),
    "client": Decimal("125"),
    "workspace": Decimal("100"),
}


@pytest.mark.parametrize(
    "present",
    [set(c) for n in range(len(LEVELS) + 1) for c in itertools.combinations(LEVELS, n)],
    ids=lambda s: "+".join(sorted(s)) or "none",
)
def test_most_specific_level_wins_in_every_combination(present):
    """All 64 combinations of which levels set a rate."""
    resolved = resolve_rate(**{lvl: VALUES[lvl] for lvl in present})
    expected = next((lvl for lvl in LEVELS if lvl in present), None)
    assert resolved.source == expected
    assert resolved.rate == (VALUES[expected] if expected else None)


def test_zero_is_a_real_override():
    # Pro bono project for a client who normally pays 125.
    r = resolve_rate(workspace=Decimal("100"), client=Decimal("125"), project=Decimal("0"))
    assert (r.rate, r.source) == (Decimal("0"), "project")


def test_order_is_as_specified():
    assert LEVELS == ("entry", "activity", "scope", "project", "client", "workspace")
