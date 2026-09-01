"""Sanity checks on the pasted-in NIFTY 50 weight snapshot — see
app.data.index_weights's module docstring for provenance (user-supplied,
not scraped; this sandbox can't reach any finance-data site to verify or
refresh it)."""
import pytest

from app.data.index_weights import NIFTY50_WEIGHTS_PCT
from app.data.instruments import STOCKS


def test_weights_sum_close_to_100_percent():
    # 49 of NIFTY 50's nominal 50 rows were supplied; allow a small margin
    # for rounding and whatever sliver of weight the missing row carries.
    total = sum(NIFTY50_WEIGHTS_PCT.values())
    assert 99.5 <= total <= 100.5


def test_every_weight_is_a_plausible_percentage():
    for symbol, weight in NIFTY50_WEIGHTS_PCT.items():
        assert 0 < weight <= 100, f"{symbol}: implausible weight {weight}"


def test_weights_are_strictly_descending_as_listed():
    # The source table is rank-ordered by weight; a mistranscribed row
    # would likely break monotonicity somewhere.
    values = list(NIFTY50_WEIGHTS_PCT.values())
    assert values == sorted(values, reverse=True)


def test_every_tracked_stock_has_a_nifty_weight():
    # All 23 curated stocks in instruments.STOCKS were confirmed NIFTY 50
    # members when this snapshot was captured (2026-09-01).
    tracked = set(STOCKS.keys())
    missing = tracked - set(NIFTY50_WEIGHTS_PCT.keys())
    assert not missing, f"tracked stocks missing a NIFTY weight: {missing}"


@pytest.mark.parametrize(
    "symbol,expected_weight",
    [
        ("RELIANCE", 9.19),
        ("BHARTIARTL", 6.07),
        ("M&M", 2.10),
        ("BAJAJ-AUTO", 1.77),  # hyphenated symbol, easy to typo
        ("DRREDDY", 0.51),  # last row
    ],
)
def test_spot_check_known_values(symbol, expected_weight):
    assert NIFTY50_WEIGHTS_PCT[symbol] == expected_weight
