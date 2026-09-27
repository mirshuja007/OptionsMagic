from datetime import date, time

import pytest

from app.analytics import strangle_system as ss


# ---------------------------------------------------------------------------
# trading_days_between / dte_for
# ---------------------------------------------------------------------------


def test_trading_days_between_same_day_is_zero():
    d = date(2026, 9, 15)
    assert ss.trading_days_between(d, d) == 0


def test_trading_days_between_consecutive_weekdays_is_one():
    assert ss.trading_days_between(date(2026, 9, 14), date(2026, 9, 15)) == 1  # Mon -> Tue


def test_trading_days_between_skips_weekend():
    # Friday -> Monday is 1 trading day, not 3 calendar days.
    assert ss.trading_days_between(date(2026, 9, 11), date(2026, 9, 14)) == 1


def test_trading_days_between_full_week():
    # Wed after an expiry -> the following Tuesday: Thu, Fri, Mon, Tue = 4.
    assert ss.trading_days_between(date(2026, 9, 16), date(2026, 9, 22)) == 4


def test_trading_days_between_negative_when_end_precedes_start():
    assert ss.trading_days_between(date(2026, 9, 15), date(2026, 9, 14)) == -1


def test_dte_for_matches_trading_days_between():
    today = date(2026, 9, 11)  # Friday
    expiry = date(2026, 9, 15)  # Tuesday
    assert ss.dte_for(today, expiry) == 2


def test_dte_for_zero_on_expiry_day_itself():
    d = date(2026, 9, 15)
    assert ss.dte_for(d, d) == 0


# ---------------------------------------------------------------------------
# The 4 documented strategies
# ---------------------------------------------------------------------------


def test_strategies_configuration():
    by_key = {s.key: s for s in ss.STRATEGIES}
    assert len(by_key) == 4

    d1111 = by_key["nifty_d1111"]
    assert (d1111.symbol, d1111.strike_rule, d1111.sl_pct, d1111.dte_allowed) == ("NIFTY", "OTM2", 0.60, frozenset({0, 1, 2}))
    assert (d1111.window_start, d1111.window_end) == (time(9, 30), time(10, 30))

    # D5HJ and A5X have no DTE gate at all — see module docstring: strict
    # DTE 0-2/0-1 gating would make their eligible days (Fri/Mon/Tue vs.
    # Wed/Thu) structurally disjoint, so the source material's own claimed
    # daily overlap between them could never happen under that gate.
    d5hj = by_key["nifty_d5hj"]
    assert (d5hj.symbol, d5hj.strike_rule, d5hj.sl_pct, d5hj.dte_allowed) == ("NIFTY", "ATM", 0.60, None)
    assert (d5hj.window_start, d5hj.window_end) == (time(13, 30), time(14, 30))

    a5x = by_key["sensex_a5x"]
    assert (a5x.symbol, a5x.strike_rule, a5x.sl_pct, a5x.dte_allowed) == ("SENSEX", "ATM", 0.35, None)
    assert (a5x.window_start, a5x.window_end) == (time(13, 30), time(14, 30))

    a6x = by_key["sensex_a6x"]
    assert (a6x.symbol, a6x.strike_rule, a6x.sl_pct, a6x.dte_allowed) == ("SENSEX", "ATM", 0.35, frozenset({0, 1}))
    assert (a6x.window_start, a6x.window_end) == (time(14, 30), time(15, 28))


def test_nifty_and_sensex_overlap_between_1330_and_1430():
    d5hj = next(s for s in ss.STRATEGIES if s.key == "nifty_d5hj")
    a5x = next(s for s in ss.STRATEGIES if s.key == "sensex_a5x")
    assert d5hj.window_start == a5x.window_start
    assert d5hj.window_end == a5x.window_end


# ---------------------------------------------------------------------------
# is_dte_eligible
# ---------------------------------------------------------------------------


def test_is_dte_eligible_with_a_dte_gate():
    d1111 = next(s for s in ss.STRATEGIES if s.key == "nifty_d1111")
    assert ss.is_dte_eligible(d1111, 0) is True
    assert ss.is_dte_eligible(d1111, 2) is True
    assert ss.is_dte_eligible(d1111, 3) is False


def test_is_dte_eligible_with_no_gate_is_always_true():
    d5hj = next(s for s in ss.STRATEGIES if s.key == "nifty_d5hj")
    assert ss.is_dte_eligible(d5hj, 0) is True
    assert ss.is_dte_eligible(d5hj, 6) is True
    assert ss.is_dte_eligible(d5hj, -1) is True


def test_overlapping_strategies_are_simultaneously_eligible_every_trading_day():
    # The whole point of removing the DTE gate on D5HJ/A5X: on ANY day's
    # DTE reading for either index, both must be eligible so the shared
    # 1:30-2:30 window actually overlaps, as the source material states.
    d5hj = next(s for s in ss.STRATEGIES if s.key == "nifty_d5hj")
    a5x = next(s for s in ss.STRATEGIES if s.key == "sensex_a5x")
    for dte in range(0, 7):
        assert ss.is_dte_eligible(d5hj, dte) is True
        assert ss.is_dte_eligible(a5x, dte) is True


# ---------------------------------------------------------------------------
# window_status
# ---------------------------------------------------------------------------


def test_window_status_before_at_and_after():
    start, end = time(9, 30), time(10, 30)
    assert ss.window_status(time(9, 0), start, end) == "before"
    assert ss.window_status(time(9, 30), start, end) == "live"
    assert ss.window_status(time(10, 0), start, end) == "live"
    assert ss.window_status(time(10, 30), start, end) == "live"  # inclusive end
    assert ss.window_status(time(10, 31), start, end) == "after"


# ---------------------------------------------------------------------------
# resolve_leg_strikes
# ---------------------------------------------------------------------------


def test_resolve_leg_strikes_atm_uses_same_strike_both_legs():
    call, put = ss.resolve_leg_strikes(spot=24837.0, strike_step=50.0, strike_rule="ATM")
    assert call == put == 24850.0  # nearest 50-step to 24837


def test_resolve_leg_strikes_otm2_is_two_steps_out_each_side():
    call, put = ss.resolve_leg_strikes(spot=24850.0, strike_step=50.0, strike_rule="OTM2")
    assert call == 24950.0
    assert put == 24750.0


def test_resolve_leg_strikes_rounds_to_nearest_step():
    call, put = ss.resolve_leg_strikes(spot=24824.0, strike_step=50.0, strike_rule="ATM")
    assert call == put == 24800.0  # 24824 rounds down to nearest 50


# ---------------------------------------------------------------------------
# leg_status / re_entry_ready
# ---------------------------------------------------------------------------


def test_leg_status_computes_threshold_and_flags_hit():
    status = ss.leg_status(entry_premium=10.0, current_premium=16.0, sl_pct=0.60)
    assert status.sl_threshold == pytest.approx(16.0)
    assert status.sl_hit is True
    assert status.pct_move == pytest.approx(0.60)


def test_leg_status_not_hit_below_threshold():
    status = ss.leg_status(entry_premium=10.0, current_premium=15.9, sl_pct=0.60)
    assert status.sl_hit is False


def test_leg_status_exactly_at_threshold_counts_as_hit():
    status = ss.leg_status(entry_premium=10.0, current_premium=16.0, sl_pct=0.60)
    assert status.sl_hit is True


def test_re_entry_ready_only_once_premium_decays_to_entry_level():
    assert ss.re_entry_ready(entry_premium=10.0, current_premium=10.0) is True
    assert ss.re_entry_ready(entry_premium=10.0, current_premium=9.5) is True
    assert ss.re_entry_ready(entry_premium=10.0, current_premium=10.5) is False
