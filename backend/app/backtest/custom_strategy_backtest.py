"""Backtest *and* live paper-trading engine for user-assembled custom
strategies: one ``LegAction`` (``app.analytics.custom_strategy``), gated
by an arbitrary entry ``Condition`` tree and closed by an arbitrary exit
``Condition`` tree (``app.analytics.conditions``) — both built from the
same toolkit, evaluated against the same ``MarketSnapshot`` shape, so
"exit when premium doubles" is just
``PremiumChangeCondition("entry", ">=", 100)``, not a separate mechanism
from the entry screener that uses the same condition type with a
different reference.

Same dual-mode design as ``app.backtest.vix_gap_ratio_backtest``: one
``simulate_day`` function, fed a complete historical day for a backtest,
or *today's bars so far* (re-run on refresh) for the live page — one rule
engine, so backtest and live can't quietly drift apart.

Known simplifications, documented rather than hidden:
  * No real historical option premiums (Kite's instrument dump only lists
    currently-listed contracts) — every backtest premium is a Black-
    Scholes reprice of the underlying's real/simulated price path.
  * IV proxy differs by mode, and deliberately so: backtesting has no
    historical IV surface to replay, so it uses the traded instrument's
    static ``base_iv`` as a stand-in "implied vol" — compared against the
    day's own realized vol (from the last 30 daily closes) for
    ``IvRegimeCondition``, which only means something if the two numbers
    are independently sourced, not the same figure twice. Live mode uses
    the real current chain's solved IV at the selected strike instead,
    since that genuinely exists and is a better number to use.
  * Checked at each minute bar's close only, not intrabar extremes.
  * Same-day only: whatever's still open at the 3:15pm flat-by time is
    closed at that bar's price regardless of the exit condition — no
    overnight carry, matching every other same-day strategy in this app
    unless told otherwise.
  * The entry condition is evaluated once, at the moment the opening
    candle (9:15-9:20) closes, against the single option the action's
    strike selection resolves to at that spot. It is not re-evaluated
    later in the day if it was false at that moment — this is an entry
    *screener* checked once per day, not a continuously-armed trigger.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.analytics.conditions import Condition, MarketSnapshot
from app.analytics.custom_strategy import LegAction, select_strike
from app.analytics.opening_volume_breakout import FLAT_BY, OPENING_CANDLE_MINUTES, aggregate_opening_candle
from app.analytics.vix_gap_ratio import historical_expected_move_pct
from app.core.black_scholes import Greeks, OptionType, greeks as bs_greeks, intrinsic_value, price as bs_price
from app.strategy.legs import Side

MinuteBar = tuple[datetime, float, float, float, float, int]
RISK_FREE_RATE = 0.065


def _leg_premium(spot: float, strike: float, t: float, iv: float, option_type: OptionType) -> float:
    if t <= 0:
        return intrinsic_value(spot, strike, option_type)
    return bs_price(spot, strike, t, RISK_FREE_RATE, iv, option_type)


def _leg_greeks(spot: float, strike: float, t: float, iv: float, option_type: OptionType) -> Greeks:
    if t <= 0:
        return Greeks(0.0, 0.0, 0.0, 0.0, 0.0)
    return bs_greeks(spot, strike, t, RISK_FREE_RATE, iv, option_type)


@dataclass(frozen=True)
class TradeResult:
    day: date
    option_type: OptionType
    side: Side
    strike: float
    lots: int
    entry_time: datetime
    entry_premium: float
    exit_time: datetime | None
    current_premium: float  # live mark while open; frozen at the exit price once closed
    exit_reason: str  # "exit_condition" | "eod" | "open"
    pnl_rupees: float

    @property
    def is_open(self) -> bool:
        return self.exit_reason == "open"


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
    prior_daily_closes: list[float],
    iv_proxy: float,
    expiry_dt: datetime,
    trading_days_to_expiry: int,
    leg_action: LegAction,
    entry_condition: Condition,
    exit_condition: Condition,
    day: date,
    lot_size: int,
    strike_step: float,
) -> TradeResult | None:
    """Pure function: no feed calls. Fed a full day's bars this always
    resolves to a closed trade (or ``None`` if the entry condition never
    fired); fed only *part* of a day's bars (today, so far), the returned
    ``TradeResult`` may still be ``is_open`` — the live paper-tracker's
    exact state at the moment the bars run out.
    """
    if len(bars) < OPENING_CANDLE_MINUTES:
        return None

    candle = aggregate_opening_candle(bars)
    spot_at_entry = candle.close
    entry_ts = bars[OPENING_CANDLE_MINUTES - 1][0]
    day_open_ts, day_open_spot = bars[0][0], bars[0][1]

    expected_move_pct = historical_expected_move_pct(prior_daily_closes, trading_days_to_expiry)
    daily_sd = (expected_move_pct / math.sqrt(trading_days_to_expiry)) if trading_days_to_expiry > 0 and expected_move_pct > 0 else 0.0
    realized_vol_annualized = daily_sd * math.sqrt(252)

    strike = select_strike(
        spot_at_entry, leg_action.option_type, leg_action.strike_selection, strike_step,
        expected_move_pct if expected_move_pct > 0 else None,
    )
    option_type = leg_action.option_type

    t_entry = max((expiry_dt - entry_ts).total_seconds() / (365.0 * 24 * 3600), 1e-6)
    entry_premium = _leg_premium(spot_at_entry, strike, t_entry, iv_proxy, option_type)

    t_day_open = max((expiry_dt - day_open_ts).total_seconds() / (365.0 * 24 * 3600), 1e-6)
    day_open_premium = _leg_premium(day_open_spot, strike, t_day_open, iv_proxy, option_type)

    # Yesterday's close, same strike: approximate "same time of day, one day earlier"
    # by adding a day's worth of time-to-expiry rather than tracking a real
    # intraday timestamp for a session that isn't in these bars at all.
    t_prev_close = t_entry + 1.0 / 365.0
    prev_close_premium = _leg_premium(prev_close, strike, t_prev_close, iv_proxy, option_type)

    atm_strike = round(spot_at_entry / strike_step) * strike_step
    atm_call_premium = _leg_premium(spot_at_entry, atm_strike, t_entry, iv_proxy, OptionType.CALL)
    atm_put_premium = _leg_premium(spot_at_entry, atm_strike, t_entry, iv_proxy, OptionType.PUT)
    g = _leg_greeks(spot_at_entry, strike, t_entry, iv_proxy, option_type)

    entry_snap = MarketSnapshot(
        spot=spot_at_entry, prev_close=prev_close, vix=vix,
        atm_call_premium=atm_call_premium, atm_put_premium=atm_put_premium,
        option_premium=entry_premium, option_entry_premium=entry_premium,
        option_day_open_premium=day_open_premium, option_prev_close_premium=prev_close_premium,
        delta=g.delta, gamma=g.gamma, theta=g.theta, vega=g.vega,
        iv=iv_proxy, realized_vol=realized_vol_annualized,
    )
    if not entry_condition.evaluate(entry_snap):
        return None

    lots = leg_action.lots
    sign = 1 if leg_action.side == Side.LONG else -1  # long profits when premium rises, short when it falls

    current_premium = entry_premium
    exit_time: datetime | None = None
    exit_reason = "open"

    for ts, _o, _h, _l, c, _v in bars[OPENING_CANDLE_MINUTES:]:
        t_now = max((expiry_dt - ts).total_seconds() / (365.0 * 24 * 3600), 1e-6)
        current_premium = _leg_premium(c, strike, t_now, iv_proxy, option_type)
        g_now = _leg_greeks(c, strike, t_now, iv_proxy, option_type)
        snap = MarketSnapshot(
            spot=c, prev_close=prev_close, vix=vix,
            atm_call_premium=atm_call_premium, atm_put_premium=atm_put_premium,
            option_premium=current_premium, option_entry_premium=entry_premium,
            option_day_open_premium=day_open_premium, option_prev_close_premium=prev_close_premium,
            delta=g_now.delta, gamma=g_now.gamma, theta=g_now.theta, vega=g_now.vega,
            iv=iv_proxy, realized_vol=realized_vol_annualized,
        )
        if exit_condition.evaluate(snap):
            exit_time, exit_reason = ts, "exit_condition"
            break
        if ts.time() >= FLAT_BY:
            exit_time, exit_reason = ts, "eod"
            break

    pnl_rupees = round(sign * (current_premium - entry_premium) * lots * lot_size, 2)
    return TradeResult(
        day=day, option_type=option_type, side=leg_action.side, strike=strike, lots=lots,
        entry_time=entry_ts, entry_premium=round(entry_premium, 2),
        exit_time=exit_time, current_premium=round(current_premium, 2), exit_reason=exit_reason,
        pnl_rupees=pnl_rupees,
    )


def _trading_days_to_expiry(day: date, expiry_date: date) -> int:
    count, cursor = 0, day
    while cursor < expiry_date:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return count


def _resolve_day_inputs(symbol: str, day: date, close_by_date: dict[date, float]):
    from app.data.instruments import get_instrument
    from app.data.mock_feed import next_weekly_expiry

    prior_dates = sorted(d for d in close_by_date if d < day)
    if len(prior_dates) < 3:
        return None

    instrument = get_instrument(symbol)
    expiry_date = next_weekly_expiry(day, instrument.expiry_weekday)
    expiry_dt = datetime.combine(expiry_date, instrument.session_end)
    return {
        "prev_close": close_by_date[prior_dates[-1]],
        "prior_daily_closes": [close_by_date[d] for d in prior_dates],
        "expiry_dt": expiry_dt,
        "trading_days_to_expiry": _trading_days_to_expiry(day, expiry_date),
    }


def run_backtest(
    symbol: str,
    leg_action: LegAction,
    entry_condition: Condition,
    exit_condition: Condition,
    lookback_trading_days: int = 20,
    as_of: date | None = None,
) -> BacktestSummary:
    """Backtest over the last ``lookback_trading_days`` completed trading
    days before ``as_of`` (default today). Uses the instrument's static
    ``base_iv`` as the IV proxy (see module docstring) — there's no
    historical IV surface to replay instead.
    """
    from app.analytics.opening_volume_breakout import prior_trading_days
    from app.data.feed import daily_series, india_vix, minute_ohlc_series
    from app.data.instruments import get_instrument

    instrument = get_instrument(symbol)
    as_of = as_of or date.today()
    days = prior_trading_days(as_of, n=lookback_trading_days)

    try:
        daily_bars = daily_series(symbol, days=lookback_trading_days + 40)
        close_by_date = {d: c for d, _o, _h, _l, c, _v in daily_bars}
    except Exception:  # noqa: BLE001 — a failed daily-history fetch skips every day, not a crash
        close_by_date = {}

    trades: list[TradeResult] = []
    skipped: list[DaySkip] = []

    for day in days:
        try:
            bars = minute_ohlc_series(symbol, session_date=day)
        except Exception:  # noqa: BLE001
            bars = []
        if not bars or len(bars) < OPENING_CANDLE_MINUTES:
            skipped.append(DaySkip(day, "insufficient_bars"))
            continue

        inputs = _resolve_day_inputs(symbol, day, close_by_date)
        if inputs is None:
            skipped.append(DaySkip(day, "insufficient_history"))
            continue

        try:
            vix = india_vix(session_date=day)
        except Exception:  # noqa: BLE001
            skipped.append(DaySkip(day, "insufficient_history"))
            continue

        trade = simulate_day(
            bars, inputs["prev_close"], vix, inputs["prior_daily_closes"], instrument.base_iv,
            inputs["expiry_dt"], inputs["trading_days_to_expiry"], leg_action, entry_condition,
            exit_condition, day, instrument.lot_size, instrument.strike_step,
        )
        (trades if trade is not None else skipped).append(trade if trade is not None else DaySkip(day, "no_signal"))

    return BacktestSummary(trades=trades, skipped_days=skipped)


def live_status(
    symbol: str,
    leg_action: LegAction,
    entry_condition: Condition,
    exit_condition: Condition,
    as_of: date | None = None,
) -> tuple[TradeResult | None, str | None]:
    """Today's status, freshly re-evaluated every call: ``(trade, reason)``.
    Fed through the exact same ``simulate_day`` a backtest uses, just with
    today's bars so far. Uses the real live chain's solved IV at the
    selected strike (not the backtest's ``base_iv`` proxy), since a real
    number genuinely exists here.
    """
    from app.data.feed import daily_series, generate_option_chain, india_vix, minute_ohlc_series
    from app.data.instruments import get_instrument

    today = as_of or date.today()
    instrument = get_instrument(symbol)

    try:
        bars = minute_ohlc_series(symbol, session_date=today)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    if not bars or len(bars) < OPENING_CANDLE_MINUTES:
        return None, "waiting for the 9:15-9:20 opening candle to close"

    try:
        daily_bars = daily_series(symbol, days=40)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    close_by_date = {d: c for d, _o, _h, _l, c, _v in daily_bars}

    inputs = _resolve_day_inputs(symbol, today, close_by_date)
    if inputs is None:
        return None, "not enough prior daily history yet"

    try:
        vix = india_vix(session_date=today)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    candle = aggregate_opening_candle(bars)
    strike_step = instrument.strike_step
    expected_move_pct = historical_expected_move_pct(inputs["prior_daily_closes"], inputs["trading_days_to_expiry"])
    strike = select_strike(
        candle.close, leg_action.option_type, leg_action.strike_selection, strike_step,
        expected_move_pct if expected_move_pct > 0 else None,
    )
    try:
        chain, chain_err = generate_option_chain(symbol), None
    except Exception as exc:  # noqa: BLE001
        chain, chain_err = None, str(exc)
    if chain_err or chain is None:
        iv_proxy = instrument.base_iv  # fall back to the backtest proxy if the live chain isn't available
    else:
        row = min(chain.rows, key=lambda r: abs(r.strike - strike))
        leg = row.call if leg_action.option_type.value == "CE" else row.put
        iv_proxy = leg.iv

    trade = simulate_day(
        bars, inputs["prev_close"], vix, inputs["prior_daily_closes"], iv_proxy,
        inputs["expiry_dt"], inputs["trading_days_to_expiry"], leg_action, entry_condition,
        exit_condition, today, instrument.lot_size, strike_step,
    )
    return (trade, None) if trade is not None else (None, "entry condition not met today")
