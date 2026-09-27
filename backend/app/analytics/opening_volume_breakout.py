"""System 1 — Opening Volume Breakout, from the same rule-based-trading
webinar as ``app.analytics.strangle_system`` (Darshan Rathod / Multyfi):
trade only when the first 5-minute candle's volume beats each of the last
3 days' opening candles, then trade the break of its high or low.

Confirmed interpretations (asked about explicitly, not assumed):
  * The opening-bias gap: the source material defines "market below
    -0.4%" (short trades only) and "market positive" (both directions),
    but leaves the -0.4%-to-0% band undefined. Resolved as: fold that gap
    into the "positive" zone — both directions allowed at or above -0.4%.
  * The daily 2-trades limit applies PER SYMBOL, not across the whole
    watchlist combined — one stock hitting its limit doesn't stop others
    (tracked in the Streamlit page's session state, not here).
  * Watchlist: index (NIFTY) plus a default liquid F&O stock list, not
    the deck's unspecified full universe.

One reading not explicitly asked about, flagged here rather than buried:
"market" for the bias filter is read as the broad index (NIFTY) — a
single daily bias applied to every symbol's allowed trade direction that
day — distinct from the per-symbol volume filter and per-symbol candle
trigger levels, which are each computed on that symbol's own price.

What this module deliberately leaves unquantified: the deck's "large stop
loss" / "poor risk-reward" skip conditions ("first candle range is
excessively wide", "target is less than 1:2 from entry") give no numeric
threshold for "excessively wide" or "significantly broken out" — this
module surfaces the stop distance and the risk-reward achievable at the
current price so a human can judge it, rather than inventing a threshold
the source material never gave.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time as dtime, timedelta
from typing import Literal

WATCHLIST: list[str] = [
    "NIFTY",
    "RELIANCE",
    "HDFCBANK",
    "ICICIBANK",
    "TCS",
    "INFY",
    "SBIN",
    "AXISBANK",
    "KOTAKBANK",
    "LT",
    "BHARTIARTL",
]
BIAS_SYMBOL = "NIFTY"  # the broad index driving the opening-bias filter for every symbol

OPENING_CANDLE_MINUTES = 5  # 9:15-9:20 AM
OPENING_CANDLE_START = dtime(9, 15)
OPENING_CANDLE_END = dtime(9, 20)
FLAT_BY = dtime(15, 15)
NO_FRESH_TRADES_AFTER = dtime(15, 15)  # same as FLAT_BY here — the deck ties both to 3:15pm
PRIOR_DAYS_FOR_VOLUME_FILTER = 3
DAILY_TRADE_LIMIT = 2
MIN_RR = 2.0
IDEAL_RR = 3.0
BIAS_THRESHOLD_PCT = -0.4  # below this: short only; at/above: both directions

Direction = Literal["long", "short"]
Bias = Literal["short_only", "both"]


@dataclass(frozen=True)
class OpeningCandle:
    open: float
    high: float
    low: float
    close: float
    volume: int


def aggregate_opening_candle(minute_bars: list[tuple]) -> OpeningCandle:
    """Aggregate the first ``OPENING_CANDLE_MINUTES`` 1-minute OHLC bars
    (as returned by ``app.data.feed.minute_ohlc_series``) into the single
    9:15-9:20 opening candle.
    """
    window = minute_bars[:OPENING_CANDLE_MINUTES]
    if not window:
        raise ValueError("No minute bars to aggregate into an opening candle")
    return OpeningCandle(
        open=window[0][1],
        high=max(b[2] for b in window),
        low=min(b[3] for b in window),
        close=window[-1][4],
        volume=sum(b[5] for b in window),
    )


def volume_filter_passes(today_volume: int, prior_volumes: list[int]) -> bool:
    """Today's opening volume must exceed EACH of the prior days'
    individually — not the average, not a majority, not "beats 2 of 3".
    """
    if not prior_volumes:
        return False
    return all(today_volume > v for v in prior_volumes)


def prior_trading_days(today: date, n: int = PRIOR_DAYS_FOR_VOLUME_FILTER) -> list[date]:
    """The ``n`` most recent trading days strictly before ``today``,
    ascending (oldest first), skipping weekends only — same
    holiday-calendar simplification as the rest of this platform (see
    ``app.analytics.strangle_system``'s module docstring for why).
    """
    days: list[date] = []
    cursor = today
    while len(days) < n:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    days.reverse()
    return days


def opening_bias(market_change_pct: float) -> Bias:
    """``market_change_pct``: the broad index's (NIFTY's) opening-candle
    close vs. previous close, as a percent. Below -0.4% -> short trades
    only; at or above -0.4% -> both directions (folds the source
    material's undefined -0.4%-to-0% gap into the "positive" zone, per
    the confirmed resolution above).
    """
    return "short_only" if market_change_pct < BIAS_THRESHOLD_PCT else "both"


def direction_allowed(direction: Direction, bias: Bias) -> bool:
    return bias == "both" or direction == "short"


def trigger_status(price: float, candle: OpeningCandle) -> Direction | None:
    if price > candle.high:
        return "long"
    if price < candle.low:
        return "short"
    return None


@dataclass(frozen=True)
class TradeLevels:
    direction: Direction
    entry: float
    stop: float
    stop_distance: float
    target_min: float  # 1:2
    target_ideal: float  # 1:3


def trade_levels(candle: OpeningCandle, direction: Direction) -> TradeLevels:
    """Entry = the candle's own trigger level (high for long, low for
    short) — the deck's own worked description measures the target off
    this level, not off wherever price actually fills.
    """
    if direction == "long":
        entry, stop, sign = candle.high, candle.low, 1
    else:
        entry, stop, sign = candle.low, candle.high, -1
    distance = abs(entry - stop)
    return TradeLevels(
        direction=direction,
        entry=entry,
        stop=stop,
        stop_distance=distance,
        target_min=entry + sign * MIN_RR * distance,
        target_ideal=entry + sign * IDEAL_RR * distance,
    )


def achievable_rr(current_price: float, stop: float, target: float) -> float:
    """Risk-reward if entering right now at ``current_price``, against
    the original (fixed) stop and 1:2 target — surfaces the deck's
    "missed move" / "poor risk-reward" skip conditions as a number to
    judge, rather than a numeric threshold the source material never gave
    for "excessively wide" or "significantly broken out already".
    """
    risk = abs(current_price - stop)
    reward = abs(target - current_price)
    return reward / risk if risk > 0 else 0.0


@dataclass(frozen=True)
class PositionSize:
    risk_amount: float
    risk_per_share: float
    quantity: int
    capital_deployed: float


def position_size(capital: float, risk_pct: float, entry: float, stop: float) -> PositionSize:
    """Risk a fixed % of capital, never a fixed quantity — the position
    sizing rule shared by both equity systems in the source material
    (this one and Inside Bar Coil Breakout), worked example verified:
    capital 1,00,000, risk 1%, entry 100, stop 98 -> risk_amount 1,000,
    risk_per_share 2, quantity 500, capital_deployed 50,000.
    """
    risk_amount = capital * risk_pct
    risk_per_share = abs(entry - stop)
    quantity = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
    return PositionSize(
        risk_amount=risk_amount,
        risk_per_share=risk_per_share,
        quantity=quantity,
        capital_deployed=quantity * entry,
    )
