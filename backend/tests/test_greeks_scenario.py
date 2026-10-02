import math

import pytest

from app.analytics import greeks_scenario as gs
from app.core.black_scholes import OptionType


def test_iv_change_for_move_down_uses_down_sensitivity():
    assert gs.iv_change_for_move(-2.0, "Normal") == pytest.approx(3.0)  # 1.5 * 2


def test_iv_change_for_move_up_uses_up_sensitivity():
    assert gs.iv_change_for_move(2.0, "Normal") == pytest.approx(-1.0)  # -0.5 * 2


def test_iv_change_for_move_zero_is_zero():
    assert gs.iv_change_for_move(0.0, "Panic") == 0.0


def test_panic_regime_reacts_harder_than_calm_on_a_down_move():
    panic = gs.iv_change_for_move(-1.0, "Panic")
    calm = gs.iv_change_for_move(-1.0, "Calm")
    assert panic > calm > 0


def test_dte_reaction_multiplier_is_1x_at_the_7_day_reference():
    assert gs.dte_reaction_multiplier(7.0) == pytest.approx(1.0)


def test_dte_reaction_multiplier_grows_as_expiry_nears():
    assert gs.dte_reaction_multiplier(1.0) > gs.dte_reaction_multiplier(3.0) > gs.dte_reaction_multiplier(7.0)


def test_dte_reaction_multiplier_shrinks_for_far_dated_options():
    assert gs.dte_reaction_multiplier(30.0) < gs.dte_reaction_multiplier(7.0)


def test_dte_reaction_multiplier_is_clamped_both_ends():
    assert gs.dte_reaction_multiplier(0.01) == pytest.approx(2.5)
    assert gs.dte_reaction_multiplier(10_000.0) == pytest.approx(0.5)


def test_iv_change_for_move_without_dte_is_unscaled():
    assert gs.iv_change_for_move(-2.0, "Normal", dte_days=None) == gs.iv_change_for_move(-2.0, "Normal")


def test_iv_change_for_move_with_dte_applies_the_multiplier():
    base = gs.iv_change_for_move(-2.0, "Normal")
    near_expiry = gs.iv_change_for_move(-2.0, "Normal", dte_days=1.0)
    far_dated = gs.iv_change_for_move(-2.0, "Normal", dte_days=30.0)
    assert near_expiry > base > far_dated > 0


@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
def test_reprice_scenario_no_change_reproduces_base_price(option_type):
    result = gs.reprice_scenario(
        spot=22000, strike=22000, t=7 / 365, r=0.065, iv=0.14,
        option_type=option_type, move_pct=0.0, iv_change_points=0.0,
    )
    assert result.price_change == pytest.approx(0.0, abs=1e-9)
    assert result.spot_component == pytest.approx(0.0, abs=1e-9)
    assert result.vol_component == pytest.approx(0.0, abs=1e-9)
    assert result.new_price == pytest.approx(result.base_price)


def test_reprice_scenario_components_sum_exactly_to_total_change():
    result = gs.reprice_scenario(
        spot=22000, strike=22000, t=7 / 365, r=0.065, iv=0.14,
        option_type=OptionType.PUT, move_pct=-1.5, iv_change_points=2.25,
    )
    assert result.spot_component + result.vol_component == pytest.approx(result.price_change)


def test_reprice_scenario_put_gains_value_on_a_down_move_with_vol_spike():
    result = gs.reprice_scenario(
        spot=22000, strike=22000, t=7 / 365, r=0.065, iv=0.14,
        option_type=OptionType.PUT, move_pct=-2.0, iv_change_points=3.0,
    )
    assert result.price_change > 0
    assert result.spot_component > 0  # a falling index alone helps an ATM put
    assert result.vol_component > 0  # rising IV alone also helps any long option


def test_reprice_scenario_shocked_spot_and_iv_match_inputs():
    result = gs.reprice_scenario(
        spot=100, strike=100, t=0.1, r=0.06, iv=0.20,
        option_type=OptionType.CALL, move_pct=5.0, iv_change_points=4.0,
    )
    assert result.shocked_spot == pytest.approx(105.0)
    assert result.shocked_iv == pytest.approx(0.24)


def test_daily_std_pct_matches_sqrt_time_scaling():
    assert gs.daily_std_pct(0.19) == pytest.approx(0.19 * 100 / math.sqrt(365))


def test_move_in_sigma_one_sigma_move():
    iv = 0.146  # daily sd ~= 0.146*100/sqrt(365) ~= 0.764%
    z = gs.move_in_sigma(gs.daily_std_pct(iv), iv)
    assert z == pytest.approx(1.0)


def test_move_in_sigma_zero_iv_does_not_divide_by_zero():
    assert gs.move_in_sigma(1.0, 0.0) == 0.0


@pytest.mark.parametrize("z,expected_fragment", [(0.3, "ordinary"), (1.5, "stronger"), (3.0, "rare")])
def test_sigma_label_buckets(z, expected_fragment):
    assert expected_fragment in gs.sigma_label(z)
    assert expected_fragment in gs.sigma_label(-z)  # direction doesn't change the label


def test_seller_pnl_profits_when_premium_falls():
    assert gs.seller_pnl(entry_premium=100, new_premium=60, lot_size=75, lots=2) == pytest.approx(2 * 75 * 40)


def test_seller_pnl_loses_when_premium_rises():
    assert gs.seller_pnl(entry_premium=100, new_premium=250, lot_size=75, lots=1) == pytest.approx(75 * -150)
