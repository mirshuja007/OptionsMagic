import math

import pytest

from app.core.black_scholes import OptionType, greeks, implied_volatility, intrinsic_value, price


SPOT, STRIKE, T, R, SIGMA = 24800.0, 24800.0, 0.05, 0.065, 0.12


def test_put_call_parity():
    call = price(SPOT, STRIKE, T, R, SIGMA, OptionType.CALL)
    put = price(SPOT, STRIKE, T, R, SIGMA, OptionType.PUT)
    # C - P = S - K*exp(-rT)
    lhs = call - put
    rhs = SPOT - STRIKE * math.exp(-R * T)
    assert lhs == pytest.approx(rhs, abs=1e-6)


def test_call_delta_between_0_and_1():
    g = greeks(SPOT, STRIKE, T, R, SIGMA, OptionType.CALL)
    assert 0 <= g.delta <= 1


def test_put_delta_between_minus1_and_0():
    g = greeks(SPOT, STRIKE, T, R, SIGMA, OptionType.PUT)
    assert -1 <= g.delta <= 0


def test_deep_itm_call_delta_near_1():
    g = greeks(SPOT, 15000.0, T, R, SIGMA, OptionType.CALL)
    assert g.delta > 0.95


def test_deep_otm_call_delta_near_0():
    g = greeks(SPOT, 40000.0, T, R, SIGMA, OptionType.CALL)
    assert g.delta < 0.05


def test_price_at_expiry_equals_intrinsic():
    assert price(SPOT, STRIKE, 0.0, R, SIGMA, OptionType.CALL) == intrinsic_value(SPOT, STRIKE, OptionType.CALL)
    assert price(SPOT, 25500.0, 0.0, R, SIGMA, OptionType.PUT) == intrinsic_value(SPOT, 25500.0, OptionType.PUT)


def test_implied_volatility_round_trip():
    true_sigma = 0.18
    market_price = price(SPOT, STRIKE, T, R, true_sigma, OptionType.CALL)
    solved = implied_volatility(market_price, SPOT, STRIKE, T, R, OptionType.CALL)
    assert solved == pytest.approx(true_sigma, abs=1e-4)


def test_implied_volatility_round_trip_put():
    true_sigma = 0.22
    market_price = price(SPOT, 24000.0, T, R, true_sigma, OptionType.PUT)
    solved = implied_volatility(market_price, SPOT, 24000.0, T, R, OptionType.PUT)
    assert solved == pytest.approx(true_sigma, abs=1e-4)


def test_gamma_positive_for_both_types():
    assert greeks(SPOT, STRIKE, T, R, SIGMA, OptionType.CALL).gamma > 0
    assert greeks(SPOT, STRIKE, T, R, SIGMA, OptionType.PUT).gamma > 0


def test_theta_negative_for_long_atm_options():
    assert greeks(SPOT, STRIKE, T, R, SIGMA, OptionType.CALL).theta < 0
    assert greeks(SPOT, STRIKE, T, R, SIGMA, OptionType.PUT).theta < 0
