import math
from datetime import date, datetime, timedelta

import pytest

from app.backtest.vix_gap_ratio_backtest import (
    BacktestSummary,
    TradeResult,
    LegState,
    _trading_days_to_expiry,
    run_backtest,
    simulate_day,
)
from app.core.black_scholes import OptionType, price as bs_price

DAY = date(2026, 1, 5)
SPOT = 24000.0
STEP = 50.0


def _bar(hour, minute, close, o=None, h=None, l=None, v=1000):
    ts = datetime(2026, 1, 5, hour, minute)
    o = o if o is not None else close
    h = h if h is not None else close
    l = l if l is not None else close
    return (ts, o, h, l, close, v)


def _flat_bars(n, close=SPOT, start_hour=9, start_minute=15):
    bars = []
    for i in range(n):
        minute = start_minute + i
        hour = start_hour + minute // 60
        bars.append(_bar(hour, minute % 60, close))
    return bars


def _alternating_closes(n=40, base=100.0, step_pct=0.01):
    closes = [base]
    for i in range(n - 1):
        closes.append(closes[-1] * (1 + step_pct if i % 2 == 0 else 1 - step_pct))
    return closes


LONG_EXPIRY_DT = datetime(2026, 2, 5, 15, 30)  # ~31 calendar days out — negligible single-day theta decay
SHORT_EXPIRY_DT = datetime(2026, 1, 6, 15, 30)  # next day — big single-day theta decay


def test_trading_days_to_expiry_counts_weekdays_only():
    assert _trading_days_to_expiry(date(2026, 1, 5), date(2026, 1, 5)) == 0  # Monday -> same day
    assert _trading_days_to_expiry(date(2026, 1, 5), date(2026, 1, 6)) == 1  # Monday -> Tuesday
    assert _trading_days_to_expiry(date(2026, 1, 2), date(2026, 1, 5)) == 1  # Friday -> Monday, weekend skipped


def test_simulate_day_no_signal_when_vix_too_low():
    bars = _flat_bars(10)
    assert simulate_day(bars, prev_close=SPOT, vix=14.0, prior_30_closes=_alternating_closes(), expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY) is None


def test_simulate_day_no_signal_when_gap_outside_band():
    bars = _flat_bars(5) + [_bar(9, 20, SPOT * 0.99)]  # ~1% gap down — outside the 0-0.5% band
    assert simulate_day(bars, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(), expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY) is None


def test_simulate_day_no_signal_with_insufficient_history():
    bars = _flat_bars(10)
    assert simulate_day(bars, prev_close=SPOT, vix=20.0, prior_30_closes=[100.0], expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY) is None


def _gap_down_bars(extra=()):
    # 5-minute opening candle closing ~0.3% below prev_close, then whatever extra bars follow.
    candle = [_bar(9, 15 + i, SPOT * 0.997) for i in range(5)]
    return candle + list(extra)


def test_simulate_day_gap_down_enters_naked_call_and_covered_puts():
    trade = simulate_day(
        _gap_down_bars(), prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    assert trade is not None
    assert trade.direction == "down"
    assert trade.naked.option_type == OptionType.CALL
    assert trade.covered_short.option_type == OptionType.PUT
    assert trade.covered_long.option_type == OptionType.PUT
    # naked call sits above spot; covered puts sit below, short (2 SD) farther than long (1 SD)
    assert trade.naked.strike > SPOT * 0.997
    assert trade.covered_short.strike < trade.covered_long.strike < SPOT * 0.997
    assert all(s % STEP == 0 for s in (trade.naked.strike, trade.covered_short.strike, trade.covered_long.strike))


def test_simulate_day_gap_up_mirrors_gap_down():
    candle = [_bar(9, 15 + i, SPOT * 1.003) for i in range(5)]
    trade = simulate_day(
        candle, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    assert trade.direction == "up"
    assert trade.naked.option_type == OptionType.PUT
    assert trade.covered_short.option_type == OptionType.CALL


def test_simulate_day_entry_premiums_match_black_scholes():
    trade = simulate_day(
        _gap_down_bars(), prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    entry_ts = datetime(2026, 1, 5, 9, 19)
    t = (LONG_EXPIRY_DT - entry_ts).total_seconds() / (365.0 * 24 * 3600)
    spot_at_entry = SPOT * 0.997
    expected_naked = bs_price(spot_at_entry, trade.naked.strike, t, 0.065, trade.iv_used, OptionType.CALL)
    assert trade.naked.entry_premium == pytest.approx(expected_naked, rel=1e-6)


def test_simulate_day_still_open_when_bars_run_out_before_eod():
    # Only the opening candle plus a couple of quiet minutes — no stop, no target, no EOD bar yet.
    bars = _gap_down_bars(extra=[_bar(9, 21, SPOT * 0.997), _bar(9, 22, SPOT * 0.997)])
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    assert trade.is_open is True
    assert trade.naked.exit_reason == "open"
    assert trade.covered_short.exit_reason == "open"
    assert trade.naked.exit_premium is None  # no realized exit yet


def test_simulate_day_open_leg_carries_a_live_mark_not_the_stale_entry_price():
    # A big rally should move the naked call's live mark well above its entry premium,
    # even though nothing has triggered its stop yet (this move alone isn't quite 2x).
    bars = _gap_down_bars(extra=[_bar(9, 21, SPOT * 1.01)])
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    assert trade.naked.is_open is True
    assert trade.naked.current_premium > trade.naked.entry_premium


def test_simulate_day_naked_stop_fires_on_a_sharp_rally():
    # Gap down enters a naked CALL above spot; a sharp rally straight through that strike
    # should roughly double (or more) the naked call's premium and stop it out, while the
    # covered puts (below spot, now even farther away) are unaffected.
    rally = SPOT * 1.05
    bars = _gap_down_bars(extra=[_bar(9, 21, rally)])
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    assert trade.naked.exit_reason == "stop"
    assert trade.naked.exit_premium >= 2 * trade.naked.entry_premium
    assert trade.covered_short.exit_reason != "stop"


def test_simulate_day_covered_group_is_a_net_debit_with_these_sd_distances():
    """Real finding, not an assumption: with the confirmed SD distances (1
    SD hedge vs 2 SD shorts) and a 3:1 lot ratio, the single closer-to-
    the-money long put routinely costs *more* than 3x the farther-out
    short puts combined under a normal vol surface — the "covered group"
    comes out a net DEBIT, not the net credit the stop rule assumes. When
    that happens ``covered_group_stopped`` correctly refuses to fire
    (there's no credit to double), which is exactly what should be
    flagged back rather than silently producing a stop level that doesn't
    mean what it's supposed to.
    """
    trade = simulate_day(
        _gap_down_bars(), prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    entry_credit = 3 * trade.covered_short.entry_premium - trade.covered_long.entry_premium
    assert entry_credit < 0

    crash = SPOT * 0.90
    bars = _gap_down_bars(extra=[_bar(9, 21, crash)])
    crashed = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    # Even on a 10% crash straight through both put strikes, the covered
    # group's stop never fires, because entry_credit <= 0 makes "doubling"
    # undefined — the position rides this unstopped until EOD or the
    # bars run out, which is the real risk this backtest is meant to surface.
    assert crashed.covered_short.exit_reason != "stop"
    assert crashed.naked.exit_reason != "stop"


def test_simulate_day_force_closes_everything_at_flat_by():
    bars = _gap_down_bars(extra=[_bar(15, 15, SPOT * 0.997)])  # quiet day, straight to the 3:15pm cutoff
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY,
    )
    assert trade.is_open is False
    assert trade.naked.exit_reason in ("eod", "profit_target")
    assert trade.covered_short.exit_reason in ("eod", "profit_target")


def test_simulate_day_decay_can_hurt_this_structure_not_just_help_it():
    """Another real finding: because the covered group is a net debit (see
    the test above), its long hedge — priced closer to the money, so
    carrying more absolute theta — can decay faster than the far-OTM
    short legs gain, even on a flat day with zero price movement. The
    textbook "theta always helps the premium seller" intuition doesn't
    automatically hold once one leg of the combo is a net-debit long
    option — exactly the kind of thing a backtest should catch before a
    live account finds out the hard way.
    """
    bars = _gap_down_bars(extra=[_bar(h, m, SPOT * 0.997) for h, m in [(10, 0), (12, 0), (14, 0), (15, 15)]])
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=SHORT_EXPIRY_DT, trading_days_to_expiry=1, day=DAY,
    )
    assert trade.is_open is False
    assert trade.pnl_rupees < 0


def test_combined_pnl_matches_manual_calculation():
    bars = _gap_down_bars(extra=[_bar(15, 15, SPOT * 0.997)])
    trade = simulate_day(
        bars, prev_close=SPOT, vix=20.0, prior_30_closes=_alternating_closes(),
        expiry_dt=LONG_EXPIRY_DT, trading_days_to_expiry=20, day=DAY, lot_multiplier=2,
    )
    lot_size, lot_multiplier = 75, 2
    naked_pnl = (trade.naked.entry_premium - trade.naked.exit_premium) * 1 * lot_size * lot_multiplier
    short_pnl = (trade.covered_short.entry_premium - trade.covered_short.exit_premium) * 3 * lot_size * lot_multiplier
    long_pnl = (trade.covered_long.exit_premium - trade.covered_long.entry_premium) * 1 * lot_size * lot_multiplier
    assert trade.pnl_rupees == pytest.approx(round(naked_pnl + short_pnl + long_pnl, 2))


def test_backtest_summary_aggregates_from_trade_results():
    mk = lambda pnl: TradeResult(
        DAY, "down", 20.0, -0.3, 0.02, 0.15,
        LegState(24500, OptionType.CALL, 10.0, 8.0, datetime(2026, 1, 5, 15, 15), "eod"),
        LegState(23500, OptionType.PUT, 12.0, 10.0, datetime(2026, 1, 5, 15, 15), "eod"),
        LegState(23750, OptionType.PUT, 18.0, 15.0, datetime(2026, 1, 5, 15, 15), "eod"),
        1, pnl,
    )
    summary = BacktestSummary(trades=[mk(1000.0), mk(-500.0)])
    assert summary.total_trades == 2
    assert (summary.wins, summary.losses) == (1, 1)
    assert summary.total_pnl == pytest.approx(500.0)
    assert summary.equity_curve == [1000.0, 500.0]
    assert summary.max_drawdown == pytest.approx(-500.0)


def test_backtest_summary_empty_is_all_zeros():
    summary = BacktestSummary(trades=[])
    assert (summary.total_trades, summary.win_rate, summary.total_pnl, summary.max_drawdown) == (0, 0.0, 0.0, 0.0)


def test_run_backtest_against_mock_feed_covers_every_requested_day(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    summary = run_backtest(lookback_trading_days=10, as_of=date(2026, 6, 15))
    assert isinstance(summary, BacktestSummary)
    trade_days = {t.day for t in summary.trades}
    skip_days = {s.day for s in summary.skipped_days}
    assert trade_days.isdisjoint(skip_days)
    assert len(trade_days | skip_days) == 10
