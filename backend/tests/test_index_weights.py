"""Sanity checks on the pasted-in NIFTY 50 weight snapshot — see
app.data.index_weights's module docstring for provenance (user-supplied,
not scraped; this sandbox can't reach any finance-data site to verify or
refresh it)."""
import pytest

from app.data.index_weights import NIFTY50_WEIGHTS_PCT, SENSEX30_WEIGHTS_PCT
from app.data.instruments import STOCKS


@pytest.mark.parametrize(
    "weights,expected_min,expected_max",
    [
        (NIFTY50_WEIGHTS_PCT, 99.5, 100.5),  # 49 of 50 rows supplied
        (SENSEX30_WEIGHTS_PCT, 99.5, 100.5),  # all 30 rows supplied
    ],
)
def test_weights_sum_close_to_100_percent(weights, expected_min, expected_max):
    total = sum(weights.values())
    assert expected_min <= total <= expected_max


@pytest.mark.parametrize("weights", [NIFTY50_WEIGHTS_PCT, SENSEX30_WEIGHTS_PCT])
def test_every_weight_is_a_plausible_percentage(weights):
    for symbol, weight in weights.items():
        assert 0 < weight <= 100, f"{symbol}: implausible weight {weight}"


@pytest.mark.parametrize("weights", [NIFTY50_WEIGHTS_PCT, SENSEX30_WEIGHTS_PCT])
def test_weights_are_strictly_descending_as_listed(weights):
    # The source table is rank-ordered by weight; a mistranscribed row
    # would likely break monotonicity somewhere.
    values = list(weights.values())
    assert values == sorted(values, reverse=True)


@pytest.mark.parametrize(
    "weights,index_name",
    [(NIFTY50_WEIGHTS_PCT, "NIFTY 50"), (SENSEX30_WEIGHTS_PCT, "SENSEX 30")],
)
def test_every_tracked_stock_has_a_weight(weights, index_name):
    # All 23 curated stocks in instruments.STOCKS were confirmed members of
    # both indices when this snapshot was captured (2026-09-01).
    tracked = set(STOCKS.keys())
    missing = tracked - set(weights.keys())
    assert not missing, f"tracked stocks missing a {index_name} weight: {missing}"


def test_sensex30_has_exactly_30_entries():
    assert len(SENSEX30_WEIGHTS_PCT) == 30


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
def test_spot_check_nifty_values(symbol, expected_weight):
    assert NIFTY50_WEIGHTS_PCT[symbol] == expected_weight


@pytest.mark.parametrize(
    "symbol,expected_weight",
    [
        ("RELIANCE", 11.45),
        ("BHARTIARTL", 7.56),
        ("M&M", 2.61),
        ("TRENT", 0.98),  # last row
    ],
)
def test_spot_check_sensex_values(symbol, expected_weight):
    assert SENSEX30_WEIGHTS_PCT[symbol] == expected_weight
