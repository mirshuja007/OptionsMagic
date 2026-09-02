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


def test_every_tracked_stock_has_a_nifty_weight():
    # instruments.STOCKS now covers the full deduplicated NIFTY 50 + SENSEX
    # 30 union (49 names) — and that union turned out to equal NIFTY 50's
    # own membership exactly (every SENSEX 30 name is also a NIFTY 50 one),
    # so every tracked stock has a NIFTY 50 weight.
    tracked = set(STOCKS.keys())
    missing = tracked - set(NIFTY50_WEIGHTS_PCT.keys())
    assert not missing, f"tracked stocks missing a NIFTY 50 weight: {missing}"


def test_sensex_weighted_symbols_are_all_tracked_stocks():
    # The reverse containment does NOT hold for SENSEX 30 — it's a strict
    # subset of the 49 tracked stocks (30 of them), not all of them. What
    # must still hold: every SENSEX 30 symbol is a real tracked stock (no
    # typoed/unknown symbol snuck into the weight table).
    tracked = set(STOCKS.keys())
    missing = set(SENSEX30_WEIGHTS_PCT.keys()) - tracked
    assert not missing, f"SENSEX 30 weight entries not found in tracked stocks: {missing}"


def test_sensex30_has_exactly_30_entries():
    assert len(SENSEX30_WEIGHTS_PCT) == 30


def test_sensex30_is_a_strict_subset_of_nifty50():
    # Documented in index_weights.py's module docstring as an observed fact
    # about this snapshot, not an assumption — pin it with a test so a
    # future data refresh can't silently invalidate code that relies on it
    # (e.g. cas.py's SENSEX-weighted bias signal, which must filter to this
    # subset rather than assume full 49-stock coverage).
    assert set(SENSEX30_WEIGHTS_PCT.keys()) < set(NIFTY50_WEIGHTS_PCT.keys())


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
