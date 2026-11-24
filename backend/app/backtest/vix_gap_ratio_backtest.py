"""Backtest *and* live paper-trading engine for the VIX + opening-gap
put/call ratio strategy (``app.analytics.vix_gap_ratio``).

The same ``simulate_day`` function drives both: fed a full historical
day's bars it always resolves to a real exit by end of day (a backtest);
fed *today's bars so far*, re-run on every refresh, it reports whichever
legs haven't resolved yet as still "open" — that's the live paper-tracker.
One rule engine, so backtest and live can never quietly drift apart.

Known simplifications, documented rather than hidden:
  * Option premiums are never fetched as real historical quotes — Kite's
    instrument dump only lists currently-listed contracts, so an expired
    weekly option's historical premium generally isn't resolvable even in
    live mode. Every premium here (entry and every intraday mark) is a
    Black-Scholes reprice of the underlying's real/simulated price path,
    using the realized volatility this strategy already computes as a
    stand-in IV — there's no historical IV surface to replay instead, the
    same simplification ``app.backtest.replay`` already documents for its
    own (different) backtest.
  * Stops/target are checked at each minute bar's CLOSE price only, not
    intrabar high/low extremes — a true "worst case within one bar" for a
    3-leg combo would mean checking combinations of three separate highs
    and lows at once, not one number; close-based checking across ~375
    bars/day is already fine-grained for how this strategy trades.
  * The 30-day SD lookback uses the 30 completed daily closes strictly
    before the trade day — never today's own still-forming price, same
    convention every other filter in this app follows.
  * The naked leg and the covered group are tracked as two *independent*
    positions that can close at different times (each has its own stop);
    the 30%-of-total-premium profit target watches their combined
    P&L — realized for whichever leg has already closed, mark-to-market
    for whichever hasn't — and closes whatever's still open the moment it
    fires. Same-day only: anything still open at the 3:15pm flat-by time
    is closed at that bar's price.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.analytics.opening_volume_breakout import FLAT_BY, OPENING_CANDLE_MINUTES, aggregate_opening_candle
from app.analytics.vix_gap_ratio import (
    GapDirection,
    covered_group_current_net_cost,
    covered_group_entry_net_credit,
    covered_group_stopped,
    covered_option_type,
    entry_direction,
    gap_pct,
    historical_expected_move_pct,
    naked_leg_stopped,
    naked_option_type,
    profit_target_hit,
    strike_levels,
)
from app.core.black_scholes import OptionType, intrinsic_value, price as bs_price

MinuteBar = tuple[datetime, float, float, float, float, int]
LegExitReason = str  # "stop" | "profit_target" | "eod" | "open"

NIFTY_LOT_SIZE = 75
NIFTY_STRIKE_STEP = 50.0
RISK_FREE_RATE = 0.065


def _leg_premium(spot: float, strike: float, t: float, iv: float, option_type: OptionType) -> float:
    if t <= 0:
        return intrinsic_value(spot, strike, option_type)
    return bs_price(spot, strike, t, RISK_FREE_RATE, iv, option_type)


@dataclass(frozen=True)
class LegState:
    strike: float
    option_type: OptionType
    entry_premium: float
    current_premium: float  # live mark-to-market while open; frozen at the exit price once closed
    exit_time: datetime | None = None
    exit_reason: LegExitReason = "open"

    @property
    def is_open(self) -> bool:
        return self.exit_reason == "open"

    @property
    def exit_premium(self) -> float | None:
        """Alias for ``current_premium`` once closed — ``None`` while
        still open, matching the name a caller expects for a realized fill.
        """
        return None if self.is_open else self.current_premium


@dataclass(frozen=True)
class TradeResult:
    day: date
    direction: GapDirection
    vix: float
    gap_pct: float
    expected_move_pct: float
    iv_used: float
    naked: LegState
    covered_short: LegState
    covered_long: LegState
    lot_multiplier: int
    pnl_rupees: float  # realized if both legs closed; mark-to-market if either is still "open"

    @property
    def is_open(self) -> bool:
        return self.naked.is_open or self.covered_short.is_open or self.covered_long.is_open


@dataclass(frozen=True)
class DaySkip:
    day: date
    reason: str  # "insufficient_bars" | "insufficient_history" | "no_signal"


@dataclass(frozen=True)
class BacktestSummary:
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
        curve, running = [], 0.0
        for t in self.trades:
            running += t.pnl_rupees
            curve.append(round(running, 2))
        return curve

    @property
    def max_drawdown(self) -> float:
        curve = self.equity_curve
        if not curve:
            return 0.0
        peak, worst = float("-inf"), 0.0
        for v in curve:
            peak = max(peak, v)
            worst = min(worst, v - peak)
        return round(worst, 2)


def simulate_day(
    bars: list[MinuteBar],
    prev_close: float,
    vix: float,
    prior_30_closes: list[float],
    expiry_dt: datetime,
    trading_days_to_expiry: int,
    day: date,
    lot_multiplier: int = 1,
    lot_size: int = NIFTY_LOT_SIZE,
    strike_step: float = NIFTY_STRIKE_STEP,
) -> TradeResult | None:
    """Pure function: no feed calls. Fed a full day's bars this always
    resolves to a closed trade (or ``None`` if no entry signal fired at
    all); fed only *part* of a day's bars (today, so far), it returns a
    ``TradeResult`` whose legs may still be ``is_open`` — the live
    paper-tracker's exact state at the moment the bars run out.
    """
    if len(bars) < OPENING_CANDLE_MINUTES:
        return None
    candle = aggregate_opening_candle(bars)
    change_pct = gap_pct(candle.close, prev_close)
    direction = entry_direction(vix, change_pct)
    if direction is None:
        return None

    expected_move_pct = historical_expected_move_pct(prior_30_closes, trading_days_to_expiry)
    if expected_move_pct <= 0 or trading_days_to_expiry <= 0:
        return None
    daily_sd = expected_move_pct / math.sqrt(trading_days_to_expiry)
    iv = daily_sd * math.sqrt(252)
    if iv <= 0:
        return None

    spot_at_entry = candle.close
    entry_ts = bars[OPENING_CANDLE_MINUTES - 1][0]
    t_entry = max((expiry_dt - entry_ts).total_seconds() / (365.0 * 24 * 3600), 1e-6)

    levels = strike_levels(spot_at_entry, expected_move_pct, strike_step, direction)
    naked_type = naked_option_type(direction)
    covered_type = covered_option_type(direction)

    naked_entry_premium = _leg_premium(spot_at_entry, levels.naked_strike, t_entry, iv, naked_type)
    covered_short_entry_premium = _leg_premium(spot_at_entry, levels.covered_short_strike, t_entry, iv, covered_type)
    covered_long_entry_premium = _leg_premium(spot_at_entry, levels.covered_long_strike, t_entry, iv, covered_type)

    naked = LegState(levels.naked_strike, naked_type, naked_entry_premium, naked_entry_premium)
    covered_short = LegState(levels.covered_short_strike, covered_type, covered_short_entry_premium, covered_short_entry_premium)
    covered_long = LegState(levels.covered_long_strike, covered_type, covered_long_entry_premium, covered_long_entry_premium)
    covered_entry_credit = covered_group_entry_net_credit(covered_short.entry_premium, covered_long.entry_premium)
    total_entry_credit = naked.entry_premium + covered_entry_credit

    # Current mark for each leg, updated every bar; used both for the live
    # (still-open) state after the loop and for every stop/target check.
    naked_mark, short_mark, long_mark = naked.entry_premium, covered_short.entry_premium, covered_long.entry_premium

    for ts, _o, _h, _l, c, _v in bars[OPENING_CANDLE_MINUTES:]:
        if not naked.is_open and not covered_short.is_open:
            break  # both sides already closed — nothing left to track today

        t_now = max((expiry_dt - ts).total_seconds() / (365.0 * 24 * 3600), 1e-6)
        if naked.is_open:
            naked_mark = _leg_premium(c, naked.strike, t_now, iv, naked.option_type)
        if covered_short.is_open:
            short_mark = _leg_premium(c, covered_short.strike, t_now, iv, covered_type)
            long_mark = _leg_premium(c, covered_long.strike, t_now, iv, covered_type)

        if naked.is_open and naked_leg_stopped(naked.entry_premium, naked_mark):
            naked = _close_leg(naked, ts, naked_mark, "stop")
        if covered_short.is_open and covered_group_stopped(covered_entry_credit, short_mark, long_mark):
            covered_short = _close_leg(covered_short, ts, short_mark, "stop")
            covered_long = _close_leg(covered_long, ts, long_mark, "stop")

        current_total_cost = naked_mark + covered_group_current_net_cost(short_mark, long_mark)
        if (naked.is_open or covered_short.is_open) and profit_target_hit(total_entry_credit, current_total_cost):
            if naked.is_open:
                naked = _close_leg(naked, ts, naked_mark, "profit_target")
            if covered_short.is_open:
                covered_short = _close_leg(covered_short, ts, short_mark, "profit_target")
                covered_long = _close_leg(covered_long, ts, long_mark, "profit_target")

        if ts.time() >= FLAT_BY:
            if naked.is_open:
                naked = _close_leg(naked, ts, naked_mark, "eod")
            if covered_short.is_open:
                covered_short = _close_leg(covered_short, ts, short_mark, "eod")
                covered_long = _close_leg(covered_long, ts, long_mark, "eod")
            break

    # Still-open legs carry the last bar's live mark; closed legs already
    # have their current_premium frozen at the exit price by _close_leg.
    naked = _mark_open(naked, naked_mark)
    covered_short = _mark_open(covered_short, short_mark)
    covered_long = _mark_open(covered_long, long_mark)

    pnl_rupees = _combined_pnl(naked, covered_short, covered_long, lot_size, lot_multiplier)
    return TradeResult(
        day=day, direction=direction, vix=vix, gap_pct=round(change_pct, 4), expected_move_pct=round(expected_move_pct, 4),
        iv_used=iv,  # full precision — rounding this would make it impossible to exactly reproduce entry_premium from it
        naked=naked, covered_short=covered_short, covered_long=covered_long,
        lot_multiplier=lot_multiplier, pnl_rupees=round(pnl_rupees, 2),
    )


def _close_leg(leg: LegState, ts: datetime, premium: float, reason: LegExitReason) -> LegState:
    return LegState(leg.strike, leg.option_type, leg.entry_premium, premium, exit_time=ts, exit_reason=reason)


def _mark_open(leg: LegState, latest_mark: float) -> LegState:
    """Refresh a still-open leg's ``current_premium`` to the latest
    computed mark; a already-closed leg is returned unchanged (its
    current_premium is already frozen at the exit price).
    """
    if not leg.is_open:
        return leg
    return LegState(leg.strike, leg.option_type, leg.entry_premium, latest_mark, exit_reason="open")


def _short_leg_pnl(leg: LegState, lots: int, lot_size: int, lot_multiplier: int) -> float:
    """P&L for one *short* leg: entry premium received minus whatever it
    costs to close — ``current_premium`` is either the realized exit price
    (already closed) or the live mark-to-market (still open).
    """
    return (leg.entry_premium - leg.current_premium) * lots * lot_size * lot_multiplier


def _combined_pnl(naked: LegState, covered_short: LegState, covered_long: LegState, lot_size: int, lot_multiplier: int) -> float:
    naked_pnl = _short_leg_pnl(naked, 1, lot_size, lot_multiplier)
    short_pnl = _short_leg_pnl(covered_short, 3, lot_size, lot_multiplier)
    # the hedge is LONG: it profits when its premium rises — the mirror sign of a short leg's P&L.
    long_pnl = (covered_long.current_premium - covered_long.entry_premium) * 1 * lot_size * lot_multiplier
    return naked_pnl + short_pnl + long_pnl


def _trading_days_to_expiry(day: date, expiry_date: date) -> int:
    count, cursor = 0, day
    while cursor < expiry_date:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return count


def _resolve_day_inputs(day: date, close_by_date: dict[date, float]):
    """Shared setup for one day: prior-30-closes, previous close, and the
    nearest weekly expiry's datetime + trading-days-to-expiry. Returns
    ``None`` if there isn't enough prior daily history yet.
    """
    from app.data.instruments import get_instrument
    from app.data.mock_feed import next_weekly_expiry

    prior_dates = sorted(d for d in close_by_date if d < day)
    if len(prior_dates) < 3:
        return None

    instrument = get_instrument("NIFTY")
    expiry_date = next_weekly_expiry(day, instrument.expiry_weekday)
    expiry_dt = datetime.combine(expiry_date, instrument.session_end)
    return {
        "prev_close": close_by_date[prior_dates[-1]],
        "prior_30_closes": [close_by_date[d] for d in prior_dates[-30:]],
        "expiry_dt": expiry_dt,
        "trading_days_to_expiry": _trading_days_to_expiry(day, expiry_date),
    }


def run_backtest(lookback_trading_days: int = 20, lot_multiplier: int = 1, as_of: date | None = None) -> BacktestSummary:
    """Backtest over the last ``lookback_trading_days`` completed trading
    days before ``as_of`` (default today). Always uses the calendar-rule
    nearest weekly expiry (``next_weekly_expiry``), not a live listed
    expiry — Kite's instrument dump only lists currently-listed contracts,
    so a past day's actual expiry can't be looked up after the fact even
    in live mode.
    """
    from app.analytics.opening_volume_breakout import prior_trading_days
    from app.data.feed import daily_series, india_vix, minute_ohlc_series

    as_of = as_of or date.today()
    days = prior_trading_days(as_of, n=lookback_trading_days)

    try:
        daily_bars = daily_series("NIFTY", days=lookback_trading_days + 40)
        close_by_date = {d: c for d, _o, _h, _l, c, _v in daily_bars}
    except Exception:  # noqa: BLE001 — a failed daily-history fetch skips every day, not a crash
        close_by_date = {}

    trades: list[TradeResult] = []
    skipped: list[DaySkip] = []

    for day in days:
        try:
            bars = minute_ohlc_series("NIFTY", session_date=day)
        except Exception:  # noqa: BLE001
            bars = []
        if not bars or len(bars) < OPENING_CANDLE_MINUTES:
            skipped.append(DaySkip(day, "insufficient_bars"))
            continue

        inputs = _resolve_day_inputs(day, close_by_date)
        if inputs is None:
            skipped.append(DaySkip(day, "insufficient_history"))
            continue

        try:
            vix = india_vix(session_date=day)
        except Exception:  # noqa: BLE001
            skipped.append(DaySkip(day, "insufficient_history"))
            continue

        trade = simulate_day(
            bars, inputs["prev_close"], vix, inputs["prior_30_closes"], inputs["expiry_dt"],
            inputs["trading_days_to_expiry"], day, lot_multiplier=lot_multiplier,
        )
        (trades if trade is not None else skipped).append(trade if trade is not None else DaySkip(day, "no_signal"))

    return BacktestSummary(trades=trades, skipped_days=skipped)


def live_status(as_of: date | None = None) -> tuple[TradeResult | None, str | None]:
    """Today's status, freshly re-evaluated every call: ``(trade, reason)``.
    ``trade`` reflects today's position so far when one has triggered —
    legs still ``is_open`` are live, closed legs are already resolved —
    fed through the exact same ``simulate_day`` a backtest uses, just with
    today's bars so far instead of a complete historical day. ``reason``
    explains why there's no trade yet (or won't be, today) when ``trade``
    is ``None``.
    """
    from app.data.feed import daily_series, india_vix, minute_ohlc_series

    today = as_of or date.today()

    try:
        bars = minute_ohlc_series("NIFTY", session_date=today)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    if not bars or len(bars) < OPENING_CANDLE_MINUTES:
        return None, "waiting for the 9:15-9:20 opening candle to close"

    try:
        daily_bars = daily_series("NIFTY", days=40)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    close_by_date = {d: c for d, _o, _h, _l, c, _v in daily_bars}

    inputs = _resolve_day_inputs(today, close_by_date)
    if inputs is None:
        return None, "not enough prior daily history yet"

    try:
        vix = india_vix(session_date=today)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    trade = simulate_day(
        bars, inputs["prev_close"], vix, inputs["prior_30_closes"], inputs["expiry_dt"],
        inputs["trading_days_to_expiry"], today,
    )
    return (trade, None) if trade is not None else (None, "no entry signal today (VIX/gap filter not met)")
