from datetime import date, timedelta

import pytest

from app.analytics import technicals as t


def _bar(day_offset: int, o: float, h: float, low: float, c: float, v: int = 1000) -> t.DailyBar:
    return t.DailyBar(date(2024, 1, 1) + timedelta(days=day_offset), o, h, low, c, v)


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


def test_rsi_alternating_equal_moves_settles_at_fifty():
    # 14 diffs alternating +1/-1 -> equal avg gain/loss -> RS=1 -> RSI=50.
    closes = [100.0]
    for i in range(14):
        closes.append(closes[-1] + (1.0 if i % 2 == 0 else -1.0))
    series = t.rsi_series(closes, period=14)
    assert series[:14] == [None] * 14
    assert series[14] == pytest.approx(50.0)


def test_rsi_all_gains_is_100_all_losses_is_zero():
    up = [100.0 + i for i in range(20)]
    assert t.rsi(up, period=14) == pytest.approx(100.0)
    down = [100.0 - i for i in range(20)]
    assert t.rsi(down, period=14) == pytest.approx(0.0)


def test_rsi_insufficient_history_returns_none():
    assert t.rsi([100.0, 101.0], period=14) is None


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------


def test_bollinger_bands_zero_variance_collapses_to_sma():
    closes = [100.0] * 25
    bands = t.bollinger_bands(closes, period=20, num_std=2.0)
    assert bands[:19] == [None] * 19
    assert bands[19] == pytest.approx((100.0, 100.0, 100.0))
    assert bands[-1] == pytest.approx((100.0, 100.0, 100.0))


def test_bollinger_bands_known_values():
    closes = [100.0] * 19 + [110.0]
    sma, upper, lower = t.bollinger_bands(closes, period=20, num_std=2.0)[-1]
    assert sma == pytest.approx(100.5)
    assert upper == pytest.approx(100.5 + 2 * 2.1794494717703367)
    assert lower == pytest.approx(100.5 - 2 * 2.1794494717703367)


def test_bollinger_squeeze_detects_recent_compression():
    # Wide swings for a while, then compress hard right at the end.
    wide = [100.0 + (10 if i % 2 == 0 else -10) for i in range(140)]
    tight = [100.0 + (0.05 if i % 2 == 0 else -0.05) for i in range(20)]
    closes = wide + tight
    assert t.bollinger_squeeze(closes, period=20, num_std=2.0, lookback=126) is True


def test_bollinger_squeeze_false_when_bandwidth_is_typical():
    closes = [100.0 + (2 if i % 2 == 0 else -2) for i in range(150)]
    assert t.bollinger_squeeze(closes, period=20, num_std=2.0, lookback=126) is False


# ---------------------------------------------------------------------------
# ADX
# ---------------------------------------------------------------------------


def test_adx_series_aligns_first_value_at_two_periods_minus_one():
    bars = []
    price = 100.0
    for i in range(60):
        bars.append(_bar(i, price, price + 1.0, price - 0.2, price + 0.8))
        price += 0.8
    series = t.adx_series(bars, period=14)
    first_idx = next(i for i, v in enumerate(series) if v is not None)
    assert first_idx == 2 * 14 - 1
    assert all(v is None for v in series[:first_idx])


def test_adx_strong_uptrend_reads_high():
    bars = []
    price = 100.0
    for i in range(60):
        bars.append(_bar(i, price, price + 1.0, price - 0.2, price + 0.8))
        price += 0.8
    assert t.adx(bars, period=14) > 50.0


def test_adx_choppy_sideways_reads_low():
    bars = []
    price = 100.0
    for i in range(60):
        move = 1.0 if i % 2 == 0 else -1.0
        bars.append(_bar(i, price, price + abs(move) + 0.1, price - abs(move) - 0.1, price + move))
        price += move
    assert t.adx(bars, period=14) < 30.0


def test_adx_insufficient_history_returns_none():
    bars = [_bar(i, 100, 101, 99, 100.5) for i in range(10)]
    assert t.adx(bars, period=14) is None


# ---------------------------------------------------------------------------
# Pivots / support-resistance
# ---------------------------------------------------------------------------


def test_pivot_lows_and_highs_find_the_obvious_extremes():
    # A clean V: descending lows into index 5, then rising.
    bars = []
    for i in range(5):
        bars.append(_bar(i, 100 - i, 100 - i + 1, 100 - i - 0.5, 100 - i))
    for i in range(5, 10):
        bars.append(_bar(i, 95 + (i - 5), 95 + (i - 5) + 1.5, 95 + (i - 5), 95 + (i - 5) + 1))

    lows = t.pivot_lows(bars, order=2)
    assert any(idx == 5 for idx, _ in lows)

    highs = t.pivot_highs(bars, order=2)
    assert any(idx == 9 or idx == 8 for idx, _ in highs) or highs == []  # rising into the edge may have no interior pivot high


def test_nearest_support_resistance_falls_back_to_window_extremes_without_pivots():
    # Monotonic rise -> no interior swing high/low -> falls back to min/max.
    bars = [_bar(i, 100 + i, 100 + i + 0.5, 100 + i - 0.5, 100 + i + 0.2) for i in range(10)]
    spot = bars[-1].close
    support, resistance = t.nearest_support_resistance(bars, spot, order=2)
    assert support == min(b.low for b in bars)
    assert resistance == max(b.high for b in bars)


# ---------------------------------------------------------------------------
# RSI divergence — constructed directly against bars + rsi_values, so the
# detection logic is isolated from RSI's own computation.
# ---------------------------------------------------------------------------


def test_bullish_divergence_detected_when_price_lower_low_but_rsi_higher_low():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(20)]
    # Two pivot lows: at index 5 (low=90, weaker RSI dip won't matter here,
    # RSI directly supplied) and index 15 (lower low=85 but higher RSI).
    bars[5] = _bar(5, 92, 93, 90, 91)
    bars[15] = _bar(15, 87, 88, 85, 86)
    rsi_values = [50.0] * 20
    rsi_values[5] = 25.0  # deep RSI low alongside the first price low
    rsi_values[15] = 35.0  # shallower RSI low alongside the deeper price low

    divergence = t.detect_rsi_divergence(bars, rsi_values, lookback=20, order=2)
    assert divergence == "bullish"


def test_bearish_divergence_detected_when_price_higher_high_but_rsi_lower_high():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(20)]
    bars[5] = _bar(5, 108, 110, 107, 109)
    bars[15] = _bar(15, 112, 115, 111, 113)
    rsi_values = [50.0] * 20
    rsi_values[5] = 75.0
    rsi_values[15] = 65.0

    divergence = t.detect_rsi_divergence(bars, rsi_values, lookback=20, order=2)
    assert divergence == "bearish"


def test_no_divergence_when_price_and_rsi_agree():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(20)]
    bars[5] = _bar(5, 92, 93, 90, 91)
    bars[15] = _bar(15, 87, 88, 85, 86)
    rsi_values = [50.0] * 20
    rsi_values[5] = 35.0
    rsi_values[15] = 25.0  # lower low, lower RSI too -> confirms, no divergence

    assert t.detect_rsi_divergence(bars, rsi_values, lookback=20, order=2) is None


# ---------------------------------------------------------------------------
# Candlestick patterns
# ---------------------------------------------------------------------------


def test_bullish_engulfing_detected():
    prev = _bar(0, 100, 101, 98, 99)  # red: close < open
    cur = _bar(1, 98.5, 103, 98, 102)  # green, fully engulfs prev's body
    assert t.detect_candlestick_pattern([prev, cur]) == ("Bullish Engulfing", "bullish")


def test_bearish_engulfing_detected():
    prev = _bar(0, 99, 101, 98, 100)  # green
    cur = _bar(1, 100.5, 101, 96, 97)  # red, fully engulfs prev's body
    assert t.detect_candlestick_pattern([prev, cur]) == ("Bearish Engulfing", "bearish")


def test_hammer_detected():
    bar = _bar(0, 100, 100.5, 90, 100.3)  # small body up top, long lower wick
    assert t.detect_candlestick_pattern([bar]) == ("Hammer", "bullish")


def test_shooting_star_detected():
    bar = _bar(0, 100, 110, 99.5, 100.2)  # small body at bottom, long upper wick
    assert t.detect_candlestick_pattern([bar]) == ("Shooting Star", "bearish")


def test_morning_star_detected():
    a = _bar(0, 100, 100.5, 90, 91)  # long red
    b = _bar(1, 90.5, 91.5, 89.5, 90.8)  # small indecision body
    c = _bar(2, 91, 98, 90.5, 97)  # strong green closing above midpoint of `a`
    assert t.detect_candlestick_pattern([a, b, c]) == ("Morning Star", "bullish")


def test_evening_star_detected():
    a = _bar(0, 90, 100, 89.5, 99)  # long green
    b = _bar(1, 99.2, 100, 98.5, 99.0)  # small indecision body
    c = _bar(2, 98.5, 99, 91, 92)  # strong red closing below midpoint of `a`
    assert t.detect_candlestick_pattern([a, b, c]) == ("Evening Star", "bearish")


def test_no_pattern_on_ordinary_bars():
    bars = [_bar(i, 100, 100.6, 99.6, 100.1) for i in range(5)]
    assert t.detect_candlestick_pattern(bars) is None


# ---------------------------------------------------------------------------
# Weekly / monthly resampling
# ---------------------------------------------------------------------------


def test_resample_weekly_groups_by_iso_week():
    # Mon 2024-01-01 .. Sun 2024-01-14 spans two ISO weeks (1 and 2).
    bars = [_bar(i, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i, 10) for i in range(14)]
    weekly = t.resample_weekly(bars)
    assert len(weekly) == 2
    assert weekly[0].open == bars[0].open
    assert weekly[0].close == bars[6].close
    assert weekly[0].high == max(b.high for b in bars[:7])
    assert weekly[0].low == min(b.low for b in bars[:7])
    assert weekly[0].volume == sum(b.volume for b in bars[:7])


def test_resample_monthly_groups_by_calendar_month():
    jan_bars = [_bar(i, 100, 101, 99, 100, 5) for i in range(31)]  # Jan 1-31
    feb_bars = [t.DailyBar(date(2024, 2, i + 1), 100, 101, 99, 100, 5) for i in range(10)]
    monthly = t.resample_monthly(jan_bars + feb_bars)
    assert len(monthly) == 2
    assert monthly[0].period.month == 1
    assert monthly[1].period.month == 2
    assert monthly[1].volume == 50
