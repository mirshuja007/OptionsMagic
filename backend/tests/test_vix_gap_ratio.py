import math

import pytest

from app.analytics import vix_gap_ratio as vgr
from app.core.black_scholes import OptionType


def test_vix_filter_passes_above_threshold():
    assert vgr.vix_filter_passes(15.1) is True
    assert vgr.vix_filter_passes(15.0) is False
    assert vgr.vix_filter_passes(14.9) is False


def test_gap_pct_matches_simple_percent_change():
    assert vgr.gap_pct(99.0, 100.0) == pytest.approx(-1.0)
    assert vgr.gap_pct(100.5, 100.0) == pytest.approx(0.5)


@pytest.mark.parametrize(
    "change_pct,expected",
    [
        (-0.3, "down"),
        (-0.5, "down"),
        (0.3, "up"),
        (0.5, "up"),
        (0.0, None),         # dead flat — no signal
        (-0.6, None),        # too big a gap
        (0.6, None),
        (-0.0001, "down"),   # any nonzero gap up to 0.5% is in-band — only exactly 0% is excluded
    ],
)
def test_gap_direction_band(change_pct, expected):
    assert vgr.gap_direction(change_pct) == expected


def test_entry_direction_requires_both_vix_and_gap():
    assert vgr.entry_direction(vix=16.0, change_pct=-0.3) == "down"
    assert vgr.entry_direction(vix=14.0, change_pct=-0.3) is None  # VIX too low
    assert vgr.entry_direction(vix=16.0, change_pct=-0.8) is None  # gap too big


def test_historical_expected_move_pct_scales_with_sqrt_time():
    # A daily return series with a known, exact standard deviation: alternating +-1% moves.
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    move_5d = vgr.historical_expected_move_pct(closes, trading_days_to_expiry=5)
    move_20d = vgr.historical_expected_move_pct(closes, trading_days_to_expiry=20)
    assert move_5d > 0
    assert move_20d == pytest.approx(move_5d * math.sqrt(20 / 5), rel=1e-6)


def test_historical_expected_move_pct_zero_to_expiry_is_zero():
    closes = [100.0, 101.0, 99.0, 102.0]
    assert vgr.historical_expected_move_pct(closes, trading_days_to_expiry=0) == 0.0


def test_historical_expected_move_pct_insufficient_data_is_zero():
    assert vgr.historical_expected_move_pct([100.0], trading_days_to_expiry=5) == 0.0
    assert vgr.historical_expected_move_pct([], trading_days_to_expiry=5) == 0.0


def test_strike_levels_gap_down_puts_covered_group_below_naked_above():
    levels = vgr.strike_levels(spot=24000.0, expected_move_pct=0.02, strike_step=50.0, direction="down")
    assert levels.naked_strike > 24000.0  # naked CE above spot
    assert levels.covered_short_strike < levels.covered_long_strike < 24000.0  # both puts below spot
    # covered short (2 SD) is farther from spot than the long hedge (1 SD)
    assert (24000.0 - levels.covered_short_strike) > (24000.0 - levels.covered_long_strike)


def test_strike_levels_gap_up_mirrors_gap_down():
    down = vgr.strike_levels(spot=24000.0, expected_move_pct=0.02, strike_step=50.0, direction="down")
    up = vgr.strike_levels(spot=24000.0, expected_move_pct=0.02, strike_step=50.0, direction="up")
    assert up.naked_strike == pytest.approx(2 * 24000.0 - down.naked_strike, abs=50.0)
    assert up.covered_short_strike > 24000.0
    assert up.covered_long_strike > 24000.0


def test_strike_levels_rounds_to_strike_step():
    levels = vgr.strike_levels(spot=24000.0, expected_move_pct=0.0137, strike_step=50.0, direction="down")
    for strike in (levels.naked_strike, levels.covered_short_strike, levels.covered_long_strike):
        assert strike % 50.0 == 0


def test_option_type_mapping_gap_down():
    assert vgr.naked_option_type("down") == OptionType.CALL
    assert vgr.covered_option_type("down") == OptionType.PUT


def test_option_type_mapping_gap_up():
    assert vgr.naked_option_type("up") == OptionType.PUT
    assert vgr.covered_option_type("up") == OptionType.CALL


def test_naked_leg_stopped_at_exactly_double():
    assert vgr.naked_leg_stopped(entry_premium=10.0, current_premium=20.0) is True
    assert vgr.naked_leg_stopped(entry_premium=10.0, current_premium=19.99) is False


def test_covered_group_entry_net_credit():
    # 3 shorts sold at 8 each, 1 long bought at 15 -> 24 - 15 = 9 net credit
    assert vgr.covered_group_entry_net_credit(short_entry_premium=8.0, long_entry_premium=15.0) == pytest.approx(9.0)


def test_covered_group_stopped_nets_out_the_hedge():
    entry_credit = vgr.covered_group_entry_net_credit(short_entry_premium=8.0, long_entry_premium=15.0)  # 9.0
    # Shorts have widened a lot (18 each = 54 total) but the long hedge also gained a lot (40),
    # so net cost to close = 54 - 40 = 14, which is NOT double the 9.0 entry credit (18.0) -> not stopped.
    assert vgr.covered_group_stopped(entry_credit, short_current_premium=18.0, long_current_premium=40.0) is False
    # Now the long hedge hasn't moved much (still 16), so net cost = 54 - 16 = 38 >= 18.0 -> stopped.
    assert vgr.covered_group_stopped(entry_credit, short_current_premium=18.0, long_current_premium=16.0) is True


def test_covered_group_stopped_undefined_when_entry_credit_not_positive():
    assert vgr.covered_group_stopped(0.0, short_current_premium=100.0, long_current_premium=0.0) is False
    assert vgr.covered_group_stopped(-5.0, short_current_premium=100.0, long_current_premium=0.0) is False


def test_profit_target_hit_at_exactly_30_percent():
    assert vgr.profit_target_hit(total_entry_credit=100.0, total_current_cost=70.0) is True
    assert vgr.profit_target_hit(total_entry_credit=100.0, total_current_cost=70.01) is False


def test_profit_target_hit_handles_a_bigger_profit_too():
    assert vgr.profit_target_hit(total_entry_credit=100.0, total_current_cost=0.0) is True


def test_profit_target_not_hit_when_entry_credit_not_positive():
    assert vgr.profit_target_hit(total_entry_credit=0.0, total_current_cost=0.0) is False
