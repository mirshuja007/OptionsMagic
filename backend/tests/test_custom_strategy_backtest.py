import math
from datetime import date, datetime

import pytest

from app.analytics.conditions import AlwaysFalse, AlwaysTrue, GapCondition, IvRegimeCondition, MarketSnapshot, Or, PremiumChangeCondition, VixCondition
from app.analytics.custom_strategy import LegAction, StrikeSelection
from app.backtest.custom_strategy_backtest import BacktestSummary, TradeResult, run_backtest, simulate_day
from app.core.black_scholes import OptionType, price as bs_price
from app.strategy.legs import Side

DAY = date(2026, 1, 5)
SPOT = 24000.0
STEP = 50.0
LONG_EXPIRY_DT = datetime(2026, 2, 5, 15, 30)  # ~31 days out -- negligible single-day theta decay


def _bar(hour, minute, close, o=None, h=None, l=None, v=1000):
    ts = datetime(2026, 1, 5, hour, minute)
    o = o if o is not None else close
    h = h if h is not None else close
    l = l if l is not None else close
    return (ts, o, h, l, close, v)


def _alternating_closes(n=40, base=100.0, step_pct=0.01):
    closes = [base]
    for i in range(n - 1):
        closes.append(closes[-1] * (1 + step_pct if i % 2 == 0 else 1 - step_pct))
    return closes


def _candle(close_mult=1.0):
    return [_bar(9, 15 + i, SPOT * close_mult) for i in range(5)]


ALWAYS_TRUE = AlwaysTrue()
NEVER_TRUE = AlwaysFalse()
SELL_ATM_CALL = LegAction(OptionType.CALL, Side.SHORT, StrikeSelection("atm"), lots=1)
BUY_ATM_PUT = LegAction(OptionType.PUT, Side.LONG, StrikeSelection("atm"), lots=1)


def test_simulate_day_returns_none_when_entry_condition_never_passes():
    bars = _candle()
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=NEVER_TRUE, exit_condition=ALWAYS_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade is None


def test_simulate_day_too_few_bars_returns_none():
    trade = simulate_day(
        _candle()[:3], prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=ALWAYS_TRUE, exit_condition=ALWAYS_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade is None


def test_simulate_day_enters_when_condition_passes_and_exits_immediately_when_exit_always_true():
    bars = _candle() + [_bar(9, 21, SPOT)]
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=ALWAYS_TRUE, exit_condition=ALWAYS_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade is not None
    assert trade.exit_reason == "exit_condition"
    assert trade.strike == round(SPOT / STEP) * STEP
    assert trade.option_type == OptionType.CALL


def test_simulate_day_entry_premium_matches_black_scholes():
    bars = _candle()
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=ALWAYS_TRUE, exit_condition=NEVER_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    entry_ts = datetime(2026, 1, 5, 9, 19)
    t = (LONG_EXPIRY_DT - entry_ts).total_seconds() / (365.0 * 24 * 3600)
    expected = bs_price(SPOT, trade.strike, t, 0.065, 0.14, OptionType.CALL)
    assert trade.entry_premium == pytest.approx(expected, abs=0.01)  # entry_premium is stored rounded to 2dp


def test_simulate_day_short_call_loses_when_premium_rises():
    bars = _candle() + [_bar(9, 21, SPOT * 1.05)]  # rally -> call premium rises -> short loses
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=ALWAYS_TRUE, exit_condition=ALWAYS_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade.pnl_rupees < 0


def test_simulate_day_long_put_profits_when_spot_falls():
    bars = _candle() + [_bar(9, 21, SPOT * 0.95)]  # drop -> put premium rises -> long gains
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=BUY_ATM_PUT,
        entry_condition=ALWAYS_TRUE, exit_condition=ALWAYS_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade.pnl_rupees > 0


def test_simulate_day_exits_via_premium_change_condition_from_entry():
    # Sell ATM call, exit when its premium has risen 50% from entry -- a rally should trigger it.
    exit_cond = PremiumChangeCondition("entry", ">=", 50.0)
    bars = _candle() + [_bar(9, 21, SPOT * 1.1)]
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=ALWAYS_TRUE, exit_condition=exit_cond, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade.exit_reason == "exit_condition"
    assert trade.current_premium >= 1.5 * trade.entry_premium


def test_simulate_day_force_closes_at_flat_by_when_exit_never_fires():
    bars = _candle() + [_bar(15, 15, SPOT)]
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=ALWAYS_TRUE, exit_condition=NEVER_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade.exit_reason == "eod"
    assert trade.is_open is False


def test_simulate_day_still_open_when_bars_run_out_before_eod():
    bars = _candle() + [_bar(9, 21, SPOT)]
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=ALWAYS_TRUE, exit_condition=NEVER_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade.is_open is True
    assert trade.exit_time is None


def test_simulate_day_gap_condition_as_entry_screen():
    gap_down_candle = [_bar(9, 15 + i, SPOT * 0.997) for i in range(5)]
    entry = GapCondition(direction="down", max_pct=0.5)
    trade_down = simulate_day(
        gap_down_candle, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=entry, exit_condition=NEVER_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade_down is not None

    flat_candle = _candle(1.0)
    trade_flat = simulate_day(
        flat_candle, prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=entry, exit_condition=NEVER_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade_flat is None


def test_simulate_day_or_condition_tree_as_entry():
    # Enter if VIX > 100 (false) OR gap down 0-0.5% (true) -- should still enter.
    entry = Or((VixCondition(">", 100.0), GapCondition(direction="down", max_pct=0.5)))
    gap_down_candle = [_bar(9, 15 + i, SPOT * 0.997) for i in range(5)]
    trade = simulate_day(
        gap_down_candle, prev_close=SPOT, vix=10.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.14,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=entry, exit_condition=NEVER_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade is not None


def test_simulate_day_iv_regime_condition_uses_distinct_iv_and_realized_vol():
    # base_iv proxy (0.30) well above the alternating-closes realized vol -> "rich".
    entry = IvRegimeCondition("rich")
    trade = simulate_day(
        _candle(), prev_close=SPOT, vix=20.0, prior_daily_closes=_alternating_closes(), iv_proxy=0.30,
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, leg_action=SELL_ATM_CALL,
        entry_condition=entry, exit_condition=NEVER_TRUE, day=DAY, lot_size=75, strike_step=STEP,
    )
    assert trade is not None


def test_backtest_summary_aggregation():
    mk = lambda pnl: TradeResult(
        DAY, OptionType.CALL, Side.SHORT, 24500.0, 1,
        datetime(2026, 1, 5, 9, 19), 10.0, datetime(2026, 1, 5, 15, 15), 8.0, "eod", pnl,
    )
    summary = BacktestSummary(trades=[mk(1000.0), mk(-400.0)])
    assert summary.total_trades == 2
    assert (summary.wins, summary.losses) == (1, 1)
    assert summary.total_pnl == pytest.approx(600.0)
    assert summary.equity_curve == [1000.0, 600.0]


def test_backtest_summary_empty_is_all_zeros():
    summary = BacktestSummary(trades=[])
    assert (summary.total_trades, summary.win_rate, summary.total_pnl, summary.max_drawdown) == (0, 0.0, 0.0, 0.0)


def test_run_backtest_against_mock_feed_covers_every_requested_day(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    summary = run_backtest(
        "NIFTY", SELL_ATM_CALL, ALWAYS_TRUE, PremiumChangeCondition("entry", ">=", 20.0),
        lookback_trading_days=10, as_of=date(2026, 6, 15),
    )
    assert isinstance(summary, BacktestSummary)
    trade_days = {t.day for t in summary.trades}
    skip_days = {s.day for s in summary.skipped_days}
    assert trade_days.isdisjoint(skip_days)
    assert len(trade_days | skip_days) == 10
