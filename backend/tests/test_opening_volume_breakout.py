from datetime import date, datetime, timedelta

import pytest

from app.analytics import opening_volume_breakout as ovb


def _minute_bar(ts: datetime, o: float, h: float, low: float, c: float, v: int = 1000):
    return (ts, o, h, low, c, v)


# ---------------------------------------------------------------------------
# aggregate_opening_candle
# ---------------------------------------------------------------------------


def test_aggregate_opening_candle_combines_first_five_bars():
    start = datetime(2026, 9, 15, 9, 15)
    bars = [
        _minute_bar(start, 100, 101, 99, 100.5, 1000),
        _minute_bar(start + timedelta(minutes=1), 100.5, 103, 100, 102, 1200),
        _minute_bar(start + timedelta(minutes=2), 102, 102.5, 98, 99, 900),
        _minute_bar(start + timedelta(minutes=3), 99, 100, 97, 99.5, 800),
        _minute_bar(start + timedelta(minutes=4), 99.5, 101, 99, 100.8, 1100),
        _minute_bar(start + timedelta(minutes=5), 100.8, 110, 100, 105, 5000),  # 6th bar, excluded
    ]
    candle = ovb.aggregate_opening_candle(bars)
    assert candle.open == 100
    assert candle.high == 103
    assert candle.low == 97
    assert candle.close == 100.8
    assert candle.volume == 1000 + 1200 + 900 + 800 + 1100


def test_aggregate_opening_candle_raises_on_empty_input():
    with pytest.raises(ValueError):
        ovb.aggregate_opening_candle([])


# ---------------------------------------------------------------------------
# volume_filter_passes
# ---------------------------------------------------------------------------


def test_volume_filter_passes_when_today_beats_each_prior_day():
    assert ovb.volume_filter_passes(1000, [500, 600, 700]) is True


def test_volume_filter_fails_when_today_beats_only_two_of_three():
    # Beats the average (600) but not the highest of the three (900) —
    # "beats each," not "beats the average."
    assert ovb.volume_filter_passes(700, [500, 600, 900]) is False


def test_volume_filter_fails_on_exact_tie():
    assert ovb.volume_filter_passes(500, [500, 400, 300]) is False


def test_volume_filter_fails_with_no_prior_history():
    assert ovb.volume_filter_passes(1000, []) is False


# ---------------------------------------------------------------------------
# opening_bias / direction_allowed
# ---------------------------------------------------------------------------


def test_opening_bias_short_only_below_threshold():
    assert ovb.opening_bias(-0.5) == "short_only"


def test_opening_bias_both_at_and_above_threshold():
    assert ovb.opening_bias(-0.4) == "both"
    assert ovb.opening_bias(0.0) == "both"
    assert ovb.opening_bias(1.2) == "both"


def test_opening_bias_gap_resolved_as_positive_zone():
    # The confirmed resolution: the -0.4%-to-0% gap the source material
    # never defines folds into "both directions," not "short only."
    assert ovb.opening_bias(-0.2) == "both"
    assert ovb.opening_bias(-0.01) == "both"


def test_direction_allowed_matches_bias():
    assert ovb.direction_allowed("short", "short_only") is True
    assert ovb.direction_allowed("long", "short_only") is False
    assert ovb.direction_allowed("long", "both") is True
    assert ovb.direction_allowed("short", "both") is True


# ---------------------------------------------------------------------------
# trigger_status
# ---------------------------------------------------------------------------


def test_trigger_status_long_short_and_none():
    candle = ovb.OpeningCandle(open=100, high=105, low=95, close=101, volume=1000)
    assert ovb.trigger_status(106, candle) == "long"
    assert ovb.trigger_status(94, candle) == "short"
    assert ovb.trigger_status(100, candle) is None
    # Exactly at the boundary doesn't count as a break either side.
    assert ovb.trigger_status(105, candle) is None
    assert ovb.trigger_status(95, candle) is None


# ---------------------------------------------------------------------------
# trade_levels
# ---------------------------------------------------------------------------


def test_trade_levels_long():
    candle = ovb.OpeningCandle(open=100, high=105, low=95, close=101, volume=1000)
    levels = ovb.trade_levels(candle, "long")
    assert levels.entry == 105
    assert levels.stop == 95
    assert levels.stop_distance == 10
    assert levels.target_min == 125  # 105 + 2*10
    assert levels.target_ideal == 135  # 105 + 3*10


def test_trade_levels_short():
    candle = ovb.OpeningCandle(open=100, high=105, low=95, close=101, volume=1000)
    levels = ovb.trade_levels(candle, "short")
    assert levels.entry == 95
    assert levels.stop == 105
    assert levels.stop_distance == 10
    assert levels.target_min == 75  # 95 - 2*10
    assert levels.target_ideal == 65  # 95 - 3*10


# ---------------------------------------------------------------------------
# achievable_rr
# ---------------------------------------------------------------------------


def test_achievable_rr_at_original_entry_is_min_rr():
    candle = ovb.OpeningCandle(open=100, high=105, low=95, close=101, volume=1000)
    levels = ovb.trade_levels(candle, "long")
    # Entering exactly at the trigger level should reproduce the 1:2 ratio.
    rr = ovb.achievable_rr(levels.entry, levels.stop, levels.target_min)
    assert rr == pytest.approx(ovb.MIN_RR)


def test_achievable_rr_degrades_after_chasing_the_move():
    candle = ovb.OpeningCandle(open=100, high=105, low=95, close=101, volume=1000)
    levels = ovb.trade_levels(candle, "long")
    # Price has already run most of the way to target — reward shrinks,
    # risk (distance to the same stop) grows: RR should be well below 1:2.
    chased_rr = ovb.achievable_rr(122, levels.stop, levels.target_min)
    assert chased_rr < ovb.MIN_RR


def test_achievable_rr_zero_when_stop_already_breached():
    assert ovb.achievable_rr(95, 95, 125) == 0.0


# ---------------------------------------------------------------------------
# position_size — worked example from the source material
# ---------------------------------------------------------------------------


def test_position_size_matches_worked_example():
    size = ovb.position_size(capital=100_000, risk_pct=0.01, entry=100, stop=98)
    assert size.risk_amount == pytest.approx(1000)
    assert size.risk_per_share == pytest.approx(2)
    assert size.quantity == 500
    assert size.capital_deployed == pytest.approx(50_000)


def test_position_size_zero_quantity_when_stop_equals_entry():
    size = ovb.position_size(capital=100_000, risk_pct=0.01, entry=100, stop=100)
    assert size.quantity == 0


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------


def test_watchlist_includes_index_and_confirmed_stock_list():
    assert ovb.BIAS_SYMBOL == "NIFTY"
    assert "NIFTY" in ovb.WATCHLIST
    for symbol in ("RELIANCE", "HDFCBANK", "ICICIBANK", "TCS", "INFY", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "BHARTIARTL"):
        assert symbol in ovb.WATCHLIST
    assert len(ovb.WATCHLIST) == 11


# ---------------------------------------------------------------------------
# prior_trading_days
# ---------------------------------------------------------------------------


def test_prior_trading_days_skips_weekend():
    # Monday -> the 3 trading days before it are Fri, Thu, Wed of the prior week.
    monday = date(2026, 9, 28)
    assert ovb.prior_trading_days(monday, 3) == [date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]


def test_prior_trading_days_midweek_is_consecutive_weekdays():
    thursday = date(2026, 10, 1)
    assert ovb.prior_trading_days(thursday, 3) == [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]


def test_prior_trading_days_returns_ascending_order():
    days = ovb.prior_trading_days(date(2026, 9, 28), 3)
    assert days == sorted(days)
