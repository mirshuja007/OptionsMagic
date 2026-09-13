"""Classic price-action technical analysis: RSI, Bollinger Bands, ADX,
swing-pivot support/resistance, RSI divergence, and candlestick reversal
patterns — the building blocks behind the DSRD framework (see
``app.analytics.dsrd``).

Every function here is a pure transform over a list of ``DailyBar`` (or plain
close prices for the simpler indicators) — no I/O, no provider awareness, so
it's identical whether the bars came from a live Kite Historical Data pull
or a simulated series. All formulas are the standard textbook definitions
(Wilder's RSI/ADX smoothing, a 20/2 Bollinger Band, N-bar fractal pivots) —
deliberately nothing novel or tuned, since the whole point of this module is
numbers a trader can independently verify, not a black box.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DailyBar:
    period: date  # the bar's date (daily) or the first date of the period (weekly/monthly)
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


def resample_weekly(bars: list[DailyBar]) -> list[DailyBar]:
    """Daily bars -> one bar per ISO week (Mon-Sun)."""
    return _resample(bars, key=lambda b: b.period.isocalendar()[:2])


def resample_monthly(bars: list[DailyBar]) -> list[DailyBar]:
    """Daily bars -> one bar per calendar month."""
    return _resample(bars, key=lambda b: (b.period.year, b.period.month))


def _resample(bars: list[DailyBar], key) -> list[DailyBar]:
    if not bars:
        return []
    groups: dict[tuple, list[DailyBar]] = {}
    for bar in bars:
        groups.setdefault(key(bar), []).append(bar)
    out = []
    for group_key in sorted(groups):
        group = groups[group_key]
        out.append(
            DailyBar(
                period=group[0].period,
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=sum(b.volume for b in group),
            )
        )
    return out


def rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI. Returns a list the same length as ``closes``; the first
    ``period`` entries are ``None`` (not enough data yet). Index ``i`` here
    lines up with ``closes[i]``, which matters for divergence detection
    (comparing price and RSI at the *same* bar).
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out

    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_from_avgs(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_avgs(avg_gain, avg_loss)

    return out


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Latest RSI reading, or ``None`` if there isn't enough history."""
    series = rsi_series(closes, period)
    return series[-1] if series else None


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> list[tuple[float, float, float] | None]:
    """(sma, upper, lower) per bar, ``None`` before ``period`` bars exist."""
    n = len(closes)
    out: list[tuple[float, float, float] | None] = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        sma = sum(window) / period
        std = statistics.pstdev(window)
        out[i] = (sma, sma + num_std * std, sma - num_std * std)
    return out


def bollinger_squeeze(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
    lookback: int = 126,
    compression_ratio: float = 0.5,
) -> bool:
    """True if the current Bollinger bandwidth has compressed to less than
    ``compression_ratio`` of its recent median — "pressure building before
    a move," relative to the instrument's own typical bandwidth rather
    than a fixed absolute threshold (bandwidth is scale-dependent).
    ``lookback`` defaults to ~6 months of daily bars.
    """
    bands = bollinger_bands(closes, period, num_std)
    widths = [(b[1] - b[2]) / b[0] for b in bands if b is not None and b[0]]
    if len(widths) < period + 1:
        return False
    recent = widths[-lookback:]
    current = recent[-1]
    baseline = statistics.median(recent[:-1])
    return baseline > 0 and current <= compression_ratio * baseline


def adx_series(bars: list[DailyBar], period: int = 14) -> list[float | None]:
    """Wilder's ADX (trend strength only, no direction). ``None`` until
    ``2 * period`` bars exist — ADX needs a full period of Wilder-smoothed
    +DI/-DI/TR before its own averaging window can start.
    """
    n = len(bars)
    out: list[float | None] = [None] * n
    if n <= 2 * period:
        return out

    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, n):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        prev_high, prev_low = bars[i - 1].high, bars[i - 1].low
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    smoothed_tr = sum(trs[:period])
    smoothed_plus = sum(plus_dms[:period])
    smoothed_minus = sum(minus_dms[:period])
    dxs: list[float] = [_dx(smoothed_plus, smoothed_minus, smoothed_tr)]

    for i in range(period, len(trs)):
        smoothed_tr = smoothed_tr - smoothed_tr / period + trs[i]
        smoothed_plus = smoothed_plus - smoothed_plus / period + plus_dms[i]
        smoothed_minus = smoothed_minus - smoothed_minus / period + minus_dms[i]
        dxs.append(_dx(smoothed_plus, smoothed_minus, smoothed_tr))

    # dxs[k] lines up with bars index (period + k) — trs[j] belongs to
    # bars[j + 1] (it's a bar-to-bar diff starting at bars[1]), and dxs[0]
    # is seeded from trs[0:period], i.e. up through trs[period - 1] -> bar
    # index period.
    adx = sum(dxs[:period]) / period
    out[2 * period - 1] = adx
    for i in range(period, len(dxs)):
        adx = (adx * (period - 1) + dxs[i]) / period
        bar_index = period + i
        if bar_index < n:
            out[bar_index] = adx

    return out


def _dx(smoothed_plus: float, smoothed_minus: float, smoothed_tr: float) -> float:
    if smoothed_tr == 0:
        return 0.0
    plus_di = 100.0 * smoothed_plus / smoothed_tr
    minus_di = 100.0 * smoothed_minus / smoothed_tr
    denom = plus_di + minus_di
    if denom == 0:
        return 0.0
    return 100.0 * abs(plus_di - minus_di) / denom


def adx(bars: list[DailyBar], period: int = 14) -> float | None:
    series = adx_series(bars, period)
    return series[-1] if series else None


def pivot_lows(bars: list[DailyBar], order: int = 2) -> list[tuple[int, float]]:
    """Fractal swing lows: bars whose low is <= every low within ``order``
    bars on both sides, and not part of a flat plateau (a window of
    entirely equal lows isn't a swing point). Classic, parameter-light
    swing-point detection — the basis for support/resistance and
    divergence detection below.
    """
    out = []
    for i in range(order, len(bars) - order):
        window_lows = [b.low for b in bars[i - order : i + order + 1]]
        if bars[i].low == min(window_lows) and window_lows.count(bars[i].low) < len(window_lows):
            out.append((i, bars[i].low))
    return out


def pivot_highs(bars: list[DailyBar], order: int = 2) -> list[tuple[int, float]]:
    out = []
    for i in range(order, len(bars) - order):
        window_highs = [b.high for b in bars[i - order : i + order + 1]]
        if bars[i].high == max(window_highs) and window_highs.count(bars[i].high) < len(window_highs):
            out.append((i, bars[i].high))
    return out


def nearest_support_resistance(bars: list[DailyBar], spot: float, order: int = 2) -> tuple[float, float]:
    """Nearest support (below spot) and resistance (above spot) within
    ``bars``, from swing pivots. Falls back to the window's own low/high
    when no pivot exists on that side (e.g. the window shows a clean
    uptrend with no swing high yet above the current price).
    """
    lows = pivot_lows(bars, order)
    highs = pivot_highs(bars, order)

    below = [lvl for _, lvl in lows if lvl <= spot]
    above = [lvl for _, lvl in highs if lvl >= spot]

    support = max(below) if below else min(b.low for b in bars)
    resistance = min(above) if above else max(b.high for b in bars)
    return support, resistance


def detect_rsi_divergence(
    bars: list[DailyBar], rsi_values: list[float | None], lookback: int = 40, order: int = 2
) -> str | None:
    """"Price makes a new extreme, momentum doesn't confirm it" — the
    classic leading reversal signal. Compares the two most recent swing
    lows (for bullish divergence) or swing highs (bearish) within the last
    ``lookback`` bars. Returns "bullish", "bearish", or ``None``.
    """
    window_start = max(0, len(bars) - lookback)
    window = bars[window_start:]
    window_rsi = rsi_values[window_start:]

    lows = pivot_lows(window, order)
    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        r1, r2 = window_rsi[i1], window_rsi[i2]
        if r1 is not None and r2 is not None and p2 < p1 and r2 > r1:
            return "bullish"

    highs = pivot_highs(window, order)
    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2], highs[-1]
        r1, r2 = window_rsi[i1], window_rsi[i2]
        if r1 is not None and r2 is not None and p2 > p1 and r2 < r1:
            return "bearish"

    return None


def _body(bar: DailyBar) -> float:
    return abs(bar.close - bar.open)


def _range(bar: DailyBar) -> float:
    return bar.high - bar.low


def _is_bullish_engulfing(prev: DailyBar, cur: DailyBar) -> bool:
    return prev.close < prev.open and cur.close > cur.open and cur.open <= prev.close and cur.close >= prev.open


def _is_bearish_engulfing(prev: DailyBar, cur: DailyBar) -> bool:
    return prev.close > prev.open and cur.close < cur.open and cur.open >= prev.close and cur.close <= prev.open


def _is_hammer(bar: DailyBar) -> bool:
    rng = _range(bar)
    if rng <= 0:
        return False
    body = _body(bar)
    lower_wick = min(bar.open, bar.close) - bar.low
    upper_wick = bar.high - max(bar.open, bar.close)
    return body <= 0.35 * rng and lower_wick >= 2 * body and upper_wick <= 0.15 * rng


def _is_shooting_star(bar: DailyBar) -> bool:
    rng = _range(bar)
    if rng <= 0:
        return False
    body = _body(bar)
    upper_wick = bar.high - max(bar.open, bar.close)
    lower_wick = min(bar.open, bar.close) - bar.low
    return body <= 0.35 * rng and upper_wick >= 2 * body and lower_wick <= 0.15 * rng


def _is_morning_star(a: DailyBar, b: DailyBar, c: DailyBar) -> bool:
    a_bearish = a.close < a.open and _body(a) >= 0.5 * _range(a) if _range(a) else False
    b_small = _range(b) > 0 and _body(b) <= 0.35 * _range(b)
    c_bullish = c.close > c.open and c.close >= (a.open + a.close) / 2
    return a_bearish and b_small and c_bullish


def _is_evening_star(a: DailyBar, b: DailyBar, c: DailyBar) -> bool:
    a_bullish = a.close > a.open and _body(a) >= 0.5 * _range(a) if _range(a) else False
    b_small = _range(b) > 0 and _body(b) <= 0.35 * _range(b)
    c_bearish = c.close < c.open and c.close <= (a.open + a.close) / 2
    return a_bullish and b_small and c_bearish


def detect_candlestick_pattern(bars: list[DailyBar]) -> tuple[str, str] | None:
    """Checks the most recent bar(s) for one of the six DSRD reversal
    shapes. Returns (pattern_name, "bullish" | "bearish"), checked in order
    3-candle patterns (strongest signal) -> engulfing -> single-candle wick
    patterns, first match wins. ``None`` if nothing matches. Whether the
    pattern is high- or low-confidence (i.e. whether it printed at an
    identified support/resistance level) is the caller's job — see
    ``app.analytics.dsrd`` rule #10.
    """
    if len(bars) >= 3 and _is_morning_star(bars[-3], bars[-2], bars[-1]):
        return "Morning Star", "bullish"
    if len(bars) >= 3 and _is_evening_star(bars[-3], bars[-2], bars[-1]):
        return "Evening Star", "bearish"
    if len(bars) >= 2 and _is_bullish_engulfing(bars[-2], bars[-1]):
        return "Bullish Engulfing", "bullish"
    if len(bars) >= 2 and _is_bearish_engulfing(bars[-2], bars[-1]):
        return "Bearish Engulfing", "bearish"
    if bars and _is_hammer(bars[-1]):
        return "Hammer", "bullish"
    if bars and _is_shooting_star(bars[-1]):
        return "Shooting Star", "bearish"
    return None
