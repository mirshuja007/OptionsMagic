"""Commodity options are options-on-futures: correct pricing sets the carry
rate q equal to r, which should reduce our Black-Scholes-Merton formula to
Black-76 (the market-standard model for options on futures). These tests
verify that equivalence directly, plus that Instrument/Leg/generator wiring
actually uses q=r for commodities and q=0 for equity/index.
"""
import math

import pytest

from app.core.black_scholes import OptionType, greeks, price
from app.data.instruments import get_instrument
from app.strategy.generator import carry_rate, bull_put_spreads
from app.strategy.legs import Leg, Side, mark_to_market, portfolio_greeks
from app.data.mock_feed import generate_option_chain


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_76_price(future: float, strike: float, t: float, r: float, sigma: float, option_type: OptionType) -> float:
    """Reference Black-76 implementation, independent of app.core.black_scholes."""
    d1 = (math.log(future / strike) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    disc = math.exp(-r * t)
    if option_type == OptionType.CALL:
        return disc * (future * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return disc * (strike * _norm_cdf(-d2) - future * _norm_cdf(-d1))


@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
def test_q_equals_r_reduces_bsm_to_black76(option_type):
    future, strike, t, r, sigma = 6200.0, 6300.0, 0.08, 0.065, 0.32
    bsm_with_q_eq_r = price(future, strike, t, r, sigma, option_type, q=r)
    reference = black_76_price(future, strike, t, r, sigma, option_type)
    assert bsm_with_q_eq_r == pytest.approx(reference, abs=1e-6)


def test_q_zero_differs_from_black76_for_nonzero_rate():
    future, strike, t, r, sigma = 6200.0, 6300.0, 0.08, 0.065, 0.32
    bsm_with_q_zero = price(future, strike, t, r, sigma, OptionType.CALL, q=0.0)
    reference = black_76_price(future, strike, t, r, sigma, OptionType.CALL)
    assert bsm_with_q_zero != pytest.approx(reference, abs=1e-2)


def test_commodity_instruments_flag_carry_rate_equals_risk_free():
    assert get_instrument("CRUDEOIL").pricing_carry_rate_equals_risk_free is True
    assert get_instrument("GOLD").pricing_carry_rate_equals_risk_free is True
    assert get_instrument("NIFTY").pricing_carry_rate_equals_risk_free is False
    assert get_instrument("RELIANCE").pricing_carry_rate_equals_risk_free is False


def test_asset_class_derivation():
    assert get_instrument("NIFTY").asset_class == "index"
    assert get_instrument("SENSEX").asset_class == "index"
    assert get_instrument("RELIANCE").asset_class == "equity"
    assert get_instrument("CRUDEOIL").asset_class == "commodity"
    assert get_instrument("GOLD").asset_class == "commodity"


def test_commodity_margin_scan_wider_than_index():
    assert get_instrument("CRUDEOIL").margin_price_scan_pct > get_instrument("NIFTY").margin_price_scan_pct


def test_mock_chain_uses_black76_pricing_for_commodities():
    """The generated CRUDEOIL chain's LTP should track the Black-76 price
    (q=r), not the plain q=0 Black-Scholes price, given its own solved IV.
    """
    chain = generate_option_chain("CRUDEOIL", seed=42)
    row = min(chain.rows, key=lambda r: abs(r.strike - chain.spot))
    black76 = black_76_price(chain.spot, row.strike, chain.time_to_expiry_years, chain.risk_free_rate, row.call.iv, OptionType.CALL)
    q_zero = price(chain.spot, row.strike, chain.time_to_expiry_years, chain.risk_free_rate, row.call.iv, OptionType.CALL, q=0.0)
    # mock feed adds +/-1% noise around the theoretical price
    assert abs(row.call.ltp - black76) < abs(row.call.ltp - q_zero)


def test_leg_q_changes_mark_to_market_pricing():
    spot, strike, t, r, iv, lot_size = 6200.0, 6300.0, 0.08, 0.065, 0.32, 100
    leg_q0 = Leg(OptionType.CALL, strike, Side.LONG, 1, entry_price=0.0, iv=iv, q=0.0)
    leg_qr = Leg(OptionType.CALL, strike, Side.LONG, 1, entry_price=0.0, iv=iv, q=r)

    pnl_q0 = mark_to_market([leg_q0], spot, t, r, lot_size)
    pnl_qr = mark_to_market([leg_qr], spot, t, r, lot_size)

    assert pnl_q0 != pytest.approx(pnl_qr, abs=1e-6)
    expected_qr_price = price(spot, strike, t, r, iv, OptionType.CALL, q=r)
    assert pnl_qr == pytest.approx(expected_qr_price * lot_size, abs=1e-6)


def test_portfolio_greeks_use_leg_q():
    spot, strike, t, r, iv, lot_size = 6200.0, 6300.0, 0.08, 0.065, 0.32, 100
    leg_qr = Leg(OptionType.CALL, strike, Side.LONG, 1, entry_price=0.0, iv=iv, q=r)
    g = portfolio_greeks([leg_qr], spot, t, r, lot_size)
    expected = greeks(spot, strike, t, r, iv, OptionType.CALL, q=r)
    assert g.delta == pytest.approx(expected.delta * lot_size, abs=1e-6)


def test_carry_rate_helper_matches_instrument_flag():
    crude_chain = generate_option_chain("CRUDEOIL", seed=1)
    nifty_chain = generate_option_chain("NIFTY", seed=1)
    assert carry_rate(crude_chain) == crude_chain.risk_free_rate
    assert carry_rate(nifty_chain) == 0.0


def test_generated_commodity_legs_carry_q_equal_to_rate():
    chain = generate_option_chain("CRUDEOIL", seed=1)
    candidates = bull_put_spreads(chain)
    assert candidates, "expected at least one bull put spread candidate"
    for leg in candidates[0].legs:
        assert leg.q == chain.risk_free_rate


def test_generated_equity_legs_carry_zero_q():
    chain = generate_option_chain("NIFTY", seed=1)
    candidates = bull_put_spreads(chain)
    assert candidates, "expected at least one bull put spread candidate"
    for leg in candidates[0].legs:
        assert leg.q == 0.0
