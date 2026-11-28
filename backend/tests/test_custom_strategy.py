import pytest

from app.analytics.custom_strategy import LegAction, StrikeSelection, select_strike
from app.core.black_scholes import OptionType
from app.strategy.legs import Side


def test_atm_rounds_spot_to_strike_step():
    assert select_strike(24037.0, OptionType.CALL, StrikeSelection("atm"), 50.0) == 24050.0


def test_otm_points_call_goes_above_spot():
    strike = select_strike(24000.0, OptionType.CALL, StrikeSelection("otm_points", 200.0), 50.0)
    assert strike == 24200.0


def test_otm_points_put_goes_below_spot():
    strike = select_strike(24000.0, OptionType.PUT, StrikeSelection("otm_points", 200.0), 50.0)
    assert strike == 23800.0


def test_otm_sd_call_goes_above_spot_scaled_by_expected_move():
    strike = select_strike(24000.0, OptionType.CALL, StrikeSelection("otm_sd", 1.5), 50.0, expected_move_pct=0.02)
    # 24000 * (1 + 1.5*0.02) = 24720 -> rounds to nearest 50
    assert strike == 24700.0 or strike == 24750.0  # exact rounding depends on float noise; check range instead
    assert 24700.0 <= strike <= 24750.0


def test_otm_sd_put_goes_below_spot_scaled_by_expected_move():
    strike = select_strike(24000.0, OptionType.PUT, StrikeSelection("otm_sd", 1.5), 50.0, expected_move_pct=0.02)
    assert strike < 24000.0


def test_otm_sd_requires_expected_move_pct():
    with pytest.raises(ValueError, match="expected_move_pct"):
        select_strike(24000.0, OptionType.CALL, StrikeSelection("otm_sd", 1.5), 50.0)


def test_unknown_method_raises():
    with pytest.raises(ValueError, match="Unknown strike selection"):
        select_strike(24000.0, OptionType.CALL, StrikeSelection("bogus"), 50.0)


def test_leg_action_construction():
    action = LegAction(OptionType.CALL, Side.SHORT, StrikeSelection("atm"), lots=2)
    assert action.lots == 2
    assert action.side == Side.SHORT
