from datetime import date, datetime

import pytest

from app.backtest.ovb_backtest import (
    BacktestSummary,
    TradeResult,
    _breakout_direction,
    run_backtest,
    simulate_day,
)
from app.analytics.opening_volume_breakout import OPENING_CANDLE_MINUTES, OpeningCandle, position_size

DAY = date(2026, 1, 5)
CANDLE = OpeningCandle(open=100.0, high=105.0, low=95.0, close=102.0, volume=50_000)
CAPITAL, RISK_PCT = 100_000.0, 0.01
LONG_QTY = position_size(CAPITAL, RISK_PCT, CANDLE.high, CANDLE.low).quantity  # entry 105, stop 95 -> qty 100


def _bar(hour, minute, o, h, l, c, v=1000):
    return (datetime(2026, 1, 5, hour, minute), o, h, l, c, v)


def _padding():
    return [_bar(9, 15 + i, 100, 100, 100, 100) for i in range(OPENING_CANDLE_MINUTES)]


def test_breakout_direction_long():
    assert _breakout_direction(high=106, low=96, close=104, candle=CANDLE) == "long"


def test_breakout_direction_short():
    assert _breakout_direction(high=104, low=94, close=96, candle=CANDLE) == "short"


def test_breakout_direction_none_when_inside_range():
    assert _breakout_direction(high=104, low=96, close=100, candle=CANDLE) is None


def test_breakout_direction_gap_bar_resolved_by_close():
    # Both levels breached in one bar (e.g. a gap) — resolved by where it closed.
    assert _breakout_direction(high=110, low=90, close=108, candle=CANDLE) == "long"
    assert _breakout_direction(high=110, low=90, close=92, candle=CANDLE) == "short"


def test_simulate_day_long_breakout_hits_target():
    bars = _padding() + [
        _bar(9, 21, 103, 106, 102, 105),   # breaks high 105 -> long entry at 105 (stop 95, target_min 125)
        _bar(9, 22, 105, 110, 104, 108),
        _bar(9, 23, 108, 126, 107, 120),   # high 126 >= target 125
    ]
    results = simulate_day(bars, CANDLE, "both", DAY, "NIFTY", CAPITAL, RISK_PCT, "min")
    assert len(results) == 1
    t = results[0]
    assert (t.direction, t.exit_reason, t.entry_price, t.exit_price) == ("long", "target", 105.0, 125.0)
    assert t.quantity == LONG_QTY
    assert t.pnl_rupees == pytest.approx((125.0 - 105.0) * LONG_QTY)


def test_simulate_day_long_breakout_hits_stop():
    bars = _padding() + [
        _bar(9, 21, 103, 106, 102, 105),   # long entry at 105, stop 95
        _bar(9, 22, 105, 109, 90, 95),     # low 90 <= stop 95
    ]
    results = simulate_day(bars, CANDLE, "both", DAY, "NIFTY", CAPITAL, RISK_PCT, "min")
    assert len(results) == 1
    t = results[0]
    assert (t.exit_reason, t.exit_price) == ("stop", 95.0)
    assert t.pnl_rupees == pytest.approx((95.0 - 105.0) * LONG_QTY)


def test_simulate_day_same_bar_stop_and_target_prefers_stop():
    bars = _padding() + [
        _bar(9, 21, 103, 106, 102, 105),   # long entry at 105
        _bar(9, 22, 105, 130, 90, 110),    # both target (125) and stop (95) touched in one bar
    ]
    results = simulate_day(bars, CANDLE, "both", DAY, "NIFTY", CAPITAL, RISK_PCT, "min")
    assert len(results) == 1
    assert results[0].exit_reason == "stop"


def test_simulate_day_force_closes_flat_at_or_after_flat_by():
    bars = _padding() + [
        _bar(9, 21, 103, 106, 102, 105),   # long entry at 105; stays inside stop/target all day
        _bar(15, 15, 104, 104, 103, 103),  # 3:15pm — flat-by cutoff, no stop/target hit
    ]
    results = simulate_day(bars, CANDLE, "both", DAY, "NIFTY", CAPITAL, RISK_PCT, "min")
    assert len(results) == 1
    assert results[0].exit_reason == "flat"
    assert results[0].exit_price == 103.0


def test_simulate_day_force_closes_at_eod_if_still_open_past_last_bar():
    bars = _padding() + [
        _bar(9, 21, 103, 106, 102, 105),   # long entry, never hits stop/target/flat-by in this short list
        _bar(9, 22, 105, 106, 104, 105),
    ]
    results = simulate_day(bars, CANDLE, "both", DAY, "NIFTY", CAPITAL, RISK_PCT, "min")
    assert len(results) == 1
    assert results[0].exit_reason == "eod_force_close"
    assert results[0].exit_price == 105.0


def test_simulate_day_no_fresh_entries_after_cutoff():
    bars = _padding() + [_bar(15, 16, 106, 107, 106, 106)]  # breakout, but after 3:15pm cutoff
    results = simulate_day(bars, CANDLE, "both", DAY, "NIFTY", CAPITAL, RISK_PCT, "min")
    assert results == []


def test_simulate_day_skips_disallowed_direction_then_takes_allowed_one_later():
    bars = _padding() + [
        _bar(9, 21, 103, 106, 102, 104),   # long breakout, but bias is short_only -> skipped, not entered
        _bar(9, 22, 104, 104, 100, 102),   # back inside range
        _bar(9, 23, 102, 103, 94, 95),     # now breaks low -> short allowed under short_only
        _bar(9, 24, 95, 96, 70, 75),       # low 70 <= target_min for the short (95 - 2*10 = 75)
    ]
    results = simulate_day(bars, CANDLE, "short_only", DAY, "NIFTY", CAPITAL, RISK_PCT, "min")
    assert len(results) == 1
    assert results[0].direction == "short"
    assert results[0].exit_reason == "target"


def test_simulate_day_respects_daily_trade_limit():
    bars = _padding() + [
        _bar(9, 21, 103, 106, 102, 105),   # long entry 105
        _bar(9, 22, 105, 109, 90, 95),     # stop out at 95 (trade 1)
        _bar(9, 23, 95, 106, 96, 105),     # re-breaks high 105 -> long entry again (trade 2)
        _bar(9, 24, 105, 109, 90, 95),     # stop out again (trade 2 closes)
        _bar(9, 25, 95, 106, 94, 105),     # would be a 3rd signal — limit is 2/day
    ]
    results = simulate_day(bars, CANDLE, "both", DAY, "NIFTY", CAPITAL, RISK_PCT, "min")
    assert len(results) == 2


def test_backtest_summary_aggregates_wins_losses_and_pnl():
    t1 = TradeResult(DAY, "NIFTY", "long", datetime(2026, 1, 5, 9, 21), 105, datetime(2026, 1, 5, 9, 30), 125, "target", 100, 20, 2000)
    t2 = TradeResult(DAY, "NIFTY", "short", datetime(2026, 1, 6, 9, 21), 95, datetime(2026, 1, 6, 9, 30), 105, "stop", 100, -10, -1000)
    summary = BacktestSummary(symbol="NIFTY", trades=[t1, t2])
    assert summary.total_trades == 2
    assert (summary.wins, summary.losses) == (1, 1)
    assert summary.win_rate == pytest.approx(0.5)
    assert summary.total_pnl == pytest.approx(1000.0)
    assert summary.avg_pnl == pytest.approx(500.0)
    assert summary.equity_curve == [2000.0, 1000.0]


def test_backtest_summary_max_drawdown_from_equity_curve():
    mk = lambda pnl: TradeResult(DAY, "NIFTY", "long", datetime(2026, 1, 5, 9, 21), 100, datetime(2026, 1, 5, 9, 30), 100, "target", 1, 0, pnl)
    summary = BacktestSummary(symbol="NIFTY", trades=[mk(1000), mk(-1500), mk(-500), mk(800)])
    # equity curve: 1000, -500, -1000, -200 -> peak 1000, worst trough -1000 -> drawdown -2000
    assert summary.equity_curve == [1000.0, -500.0, -1000.0, -200.0]
    assert summary.max_drawdown == pytest.approx(-2000.0)


def test_backtest_summary_empty_trades_is_all_zeros():
    summary = BacktestSummary(symbol="NIFTY", trades=[])
    assert (summary.total_trades, summary.win_rate, summary.total_pnl, summary.max_drawdown) == (0, 0.0, 0.0, 0.0)


def test_run_backtest_against_mock_feed_covers_every_requested_day(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    summary = run_backtest("NIFTY", lookback_trading_days=10, as_of=date(2026, 6, 15))
    assert isinstance(summary, BacktestSummary)
    trade_days = {t.day for t in summary.trades}
    skip_days = {s.day for s in summary.skipped_days}
    assert trade_days.isdisjoint(skip_days)
    assert len(trade_days | skip_days) == 10


def test_run_backtest_different_symbols_are_independent(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    nifty = run_backtest("NIFTY", lookback_trading_days=5, as_of=date(2026, 6, 15))
    reliance = run_backtest("RELIANCE", lookback_trading_days=5, as_of=date(2026, 6, 15))
    assert nifty.symbol == "NIFTY"
    assert reliance.symbol == "RELIANCE"
