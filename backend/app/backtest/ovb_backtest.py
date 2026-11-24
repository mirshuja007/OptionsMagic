"""Multi-day systematic backtester for Opening Volume Breakout (System 1).

Everything in ``app.analytics.opening_volume_breakout`` only ever answers
"what does today's setup look like" — the Streamlit page evaluates it live,
once, against whatever today happens to be. Nothing in the platform runs
that same rule set across many historical days and tallies how it would
have done: win rate, total/average P&L, drawdown. This module is that
missing piece, built entirely from the already-confirmed rules in that
module — no new thresholds invented here, only backtest-specific
mechanics the live page never had to decide because it only ever looks at
*right now*:

  * Same-bar stop-and-target: if a single minute bar's range contains both
    the stop and the target, the stop is assumed to hit first (the
    conservative assumption — a backtest that resolves ties in its own
    favor is worse than useless).
  * Entry fills exactly at the opening candle's own trigger level (the
    candle's high for a long, its low for a short) — the same convention
    ``trade_levels`` already documents, not wherever a bar's price
    happens to land.
  * A trade still open at end of day (shouldn't normally happen, since the
    3:15pm flat-by rule closes it first) is force-closed at the last
    available bar's close, so no trade is ever left dangling.
  * Up to ``DAILY_TRADE_LIMIT`` completed trades per symbol per day,
    continuing to scan for a fresh signal after each exit — same limit
    the live page tracks, applied here across a whole backtest run
    instead of one day at a time.

The date range is expressed as "the last N completed trading days before
today", not an arbitrary start/end pair — ``daily_series``/
``minute_ohlc_series`` are both anchored to "now" looking backward (mock
mode rescales its simulated walk to land on today's live spot; Kite mode
fetches real history ending today), so a free-form historical start date
has no natural meaning here without first fixing a reference "today".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime

from app.analytics.opening_volume_breakout import (
    BIAS_SYMBOL,
    DAILY_TRADE_LIMIT,
    FLAT_BY,
    NO_FRESH_TRADES_AFTER,
    OPENING_CANDLE_MINUTES,
    Bias,
    Direction,
    OpeningCandle,
    TradeLevels,
    aggregate_opening_candle,
    direction_allowed,
    opening_bias,
    position_size,
    prior_trading_days,
    trade_levels,
    volume_filter_passes,
)

MinuteBar = tuple[datetime, float, float, float, float, int]
TargetChoice = str  # "min" (1:2) | "ideal" (1:3)


@dataclass(frozen=True)
class TradeResult:
    day: date
    symbol: str
    direction: Direction
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str  # "target" | "stop" | "flat" | "eod_force_close"
    quantity: int
    pnl_points: float
    pnl_rupees: float


@dataclass(frozen=True)
class DaySkip:
    day: date
    reason: str  # "insufficient_bars" | "insufficient_prior_volume_data" | "volume_filter_failed" | "no_signal"


@dataclass(frozen=True)
class BacktestSummary:
    symbol: str
    trades: list[TradeResult]
    skipped_days: list[DaySkip] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_rupees > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl_rupees <= 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades else 0.0

    @property
    def total_pnl(self) -> float:
        return round(sum(t.pnl_rupees for t in self.trades), 2)

    @property
    def avg_pnl(self) -> float:
        return round(self.total_pnl / self.total_trades, 2) if self.total_trades else 0.0

    @property
    def equity_curve(self) -> list[float]:
        curve = []
        running = 0.0
        for t in self.trades:
            running += t.pnl_rupees
            curve.append(round(running, 2))
        return curve

    @property
    def max_drawdown(self) -> float:
        curve = self.equity_curve
        if not curve:
            return 0.0
        peak = float("-inf")
        worst = 0.0
        for v in curve:
            peak = max(peak, v)
            worst = min(worst, v - peak)
        return round(worst, 2)


def simulate_day(
    bars: list[MinuteBar],
    candle: OpeningCandle,
    bias: Bias,
    day: date,
    symbol: str,
    capital: float,
    risk_pct: float,
    target_choice: TargetChoice,
) -> list[TradeResult]:
    """Replay one day's post-opening-candle minute bars against the
    Opening Volume Breakout rules, returning every completed trade (up to
    ``DAILY_TRADE_LIMIT``). Pure function of its inputs — no feed calls —
    so it's directly unit-testable with hand-built bar lists.
    """
    results: list[TradeResult] = []
    active: dict | None = None
    i = OPENING_CANDLE_MINUTES

    while len(results) < DAILY_TRADE_LIMIT and i < len(bars):
        ts, o, h, l, c, v = bars[i]

        if active is None:
            if ts.time() >= NO_FRESH_TRADES_AFTER:
                break
            direction = _breakout_direction(h, l, c, candle)
            if direction is not None and direction_allowed(direction, bias):
                levels = trade_levels(candle, direction)
                size = position_size(capital, risk_pct, levels.entry, levels.stop)
                if size.quantity > 0:
                    active = {"levels": levels, "quantity": size.quantity, "entry_time": ts}
            i += 1
            continue

        levels: TradeLevels = active["levels"]
        target_price = levels.target_min if target_choice == "min" else levels.target_ideal
        is_long = levels.direction == "long"
        hit_stop = l <= levels.stop if is_long else h >= levels.stop
        hit_target = h >= target_price if is_long else l <= target_price

        exit_reason = exit_price = None
        if hit_stop:  # conservative same-bar tie-break: stop wins if both are touched
            exit_reason, exit_price = "stop", levels.stop
        elif hit_target:
            exit_reason, exit_price = "target", target_price
        elif ts.time() >= FLAT_BY:
            exit_reason, exit_price = "flat", c

        if exit_reason is not None:
            results.append(_close_trade(day, symbol, active, ts, exit_price, exit_reason))
            active = None
        i += 1

    if active is not None and bars:
        last_ts, _, _, _, last_close, _ = bars[-1]
        results.append(_close_trade(day, symbol, active, last_ts, last_close, "eod_force_close"))

    return results


def _breakout_direction(high: float, low: float, close: float, candle: OpeningCandle) -> Direction | None:
    broke_up, broke_down = high > candle.high, low < candle.low
    if broke_up and broke_down:  # gap bar spanning both levels — resolve by where it closed
        if close > candle.high:
            return "long"
        if close < candle.low:
            return "short"
        return None
    if broke_up:
        return "long"
    if broke_down:
        return "short"
    return None


def _close_trade(day: date, symbol: str, active: dict, exit_time: datetime, exit_price: float, reason: str) -> TradeResult:
    levels: TradeLevels = active["levels"]
    is_long = levels.direction == "long"
    pnl_points = (exit_price - levels.entry) if is_long else (levels.entry - exit_price)
    pnl_rupees = pnl_points * active["quantity"]
    return TradeResult(
        day=day, symbol=symbol, direction=levels.direction,
        entry_time=active["entry_time"], entry_price=levels.entry,
        exit_time=exit_time, exit_price=exit_price, exit_reason=reason,
        quantity=active["quantity"], pnl_points=round(pnl_points, 2), pnl_rupees=round(pnl_rupees, 2),
    )


def run_backtest(
    symbol: str,
    lookback_trading_days: int = 30,
    capital: float = 100_000.0,
    risk_pct: float = 0.01,
    target_choice: TargetChoice = "min",
    as_of: date | None = None,
) -> BacktestSummary:
    """Run Opening Volume Breakout over the last ``lookback_trading_days``
    completed trading days before ``as_of`` (default: today — so the range
    never includes today's possibly-still-open session).
    """
    from app.data.feed import daily_series, minute_ohlc_series

    as_of = as_of or date.today()
    days = prior_trading_days(as_of, n=lookback_trading_days)

    # One bar cache per (symbol, day) for this run — each day is fetched once
    # as "today" and again as a "prior day" for up to 3 later days, and Kite's
    # Historical Data API is both rate-limited and billed per call.
    bar_cache: dict[tuple[str, date], list[MinuteBar]] = {}

    def bars_for(sym: str, d: date) -> list[MinuteBar] | None:
        key = (sym, d)
        if key not in bar_cache:
            try:
                bar_cache[key] = minute_ohlc_series(sym, session_date=d)
            except Exception:  # noqa: BLE001 — a missing/failed day is a skip, not a crash
                bar_cache[key] = []
        return bar_cache[key] or None

    daily_bars, daily_err = None, None
    try:
        daily_bars = daily_series(BIAS_SYMBOL, days=lookback_trading_days + 10)
    except Exception as exc:  # noqa: BLE001
        daily_err = exc
    close_by_date = {d: c for d, _o, _h, _l, c, _v in (daily_bars or [])}

    trades: list[TradeResult] = []
    skipped: list[DaySkip] = []

    for day in days:
        bars = bars_for(symbol, day)
        if not bars or len(bars) < OPENING_CANDLE_MINUTES:
            skipped.append(DaySkip(day, "insufficient_bars"))
            continue
        candle = aggregate_opening_candle(bars)

        prior_volumes = []
        prior_ok = True
        for p in prior_trading_days(day):
            p_bars = bars_for(symbol, p)
            if not p_bars or len(p_bars) < OPENING_CANDLE_MINUTES:
                prior_ok = False
                break
            prior_volumes.append(aggregate_opening_candle(p_bars).volume)
        if not prior_ok:
            skipped.append(DaySkip(day, "insufficient_prior_volume_data"))
            continue
        if not volume_filter_passes(candle.volume, prior_volumes):
            skipped.append(DaySkip(day, "volume_filter_failed"))
            continue

        bias_candle = candle if symbol == BIAS_SYMBOL else None
        if bias_candle is None:
            bias_bars = bars_for(BIAS_SYMBOL, day)
            if not bias_bars or len(bias_bars) < OPENING_CANDLE_MINUTES:
                skipped.append(DaySkip(day, "insufficient_bars"))
                continue
            bias_candle = aggregate_opening_candle(bias_bars)

        prev_trading_day = prior_trading_days(day, n=1)[0]
        prev_close = close_by_date.get(prev_trading_day)
        if prev_close is None or daily_err is not None:
            skipped.append(DaySkip(day, "insufficient_prior_volume_data"))
            continue
        change_pct = (bias_candle.close - prev_close) / prev_close * 100.0
        bias = opening_bias(change_pct)

        day_trades = simulate_day(bars, candle, bias, day, symbol, capital, risk_pct, target_choice)
        if day_trades:
            trades.extend(day_trades)
        else:
            skipped.append(DaySkip(day, "no_signal"))

    return BacktestSummary(symbol=symbol, trades=trades, skipped_days=skipped)
