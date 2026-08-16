import pytest

from app.core.black_scholes import OptionType
from app.data.instruments import get_instrument
from app.data.mock_feed import generate_option_chain
from app.margin.span import estimate_margin
from app.strategy.legs import Leg, Side, net_entry_cashflow, payoff_extrema


def _bull_put_spread():
    short_leg = Leg(OptionType.PUT, 24700.0, Side.SHORT, 1, entry_price=60.0, iv=0.13)
    long_leg = Leg(OptionType.PUT, 24500.0, Side.LONG, 1, entry_price=25.0, iv=0.14)
    return [short_leg, long_leg]


def _naked_short_put():
    return [Leg(OptionType.PUT, 24700.0, Side.SHORT, 1, entry_price=60.0, iv=0.13)]


def test_bull_put_spread_is_defined_risk():
    legs = _bull_put_spread()
    extrema = payoff_extrema(legs, lot_size=75)
    assert not extrema.unlimited_downside_risk
    assert not extrema.unlimited_upside_risk
    assert extrema.max_profit == pytest.approx((60.0 - 25.0) * 75)
    assert extrema.max_loss == pytest.approx(((60.0 - 25.0) + (24500.0 - 24700.0)) * 75)


def test_naked_short_put_has_bounded_upside_unlimited_ish_downside():
    # A short put's max loss at expiry is capped only by spot going to zero,
    # which our extrema calc treats as bounded-but-large, not literally infinite.
    legs = _naked_short_put()
    extrema = payoff_extrema(legs, lot_size=75)
    assert extrema.max_profit == pytest.approx(60.0 * 75)
    assert extrema.max_loss < 0


def test_net_entry_cashflow_credit_spread_is_positive():
    legs = _bull_put_spread()
    credit = net_entry_cashflow(legs, lot_size=75)
    assert credit == pytest.approx((60.0 - 25.0) * 75)


def test_spread_margin_less_than_naked_margin():
    chain = generate_option_chain("NIFTY", seed=3)
    instrument = get_instrument("NIFTY")
    spread_legs = _bull_put_spread()
    naked_legs = _naked_short_put()

    spread_margin = estimate_margin(spread_legs, instrument, chain.spot, chain.time_to_expiry_years, chain.risk_free_rate)
    naked_margin = estimate_margin(naked_legs, instrument, chain.spot, chain.time_to_expiry_years, chain.risk_free_rate)

    assert spread_margin.total_margin < naked_margin.total_margin
    assert spread_margin.total_margin > 0
    assert naked_margin.total_margin > 0


def test_margin_is_never_negative():
    chain = generate_option_chain("BANKNIFTY", seed=4)
    instrument = get_instrument("BANKNIFTY")
    legs = [
        Leg(OptionType.CALL, chain.spot, Side.LONG, 1, entry_price=100.0, iv=0.15),
        Leg(OptionType.PUT, chain.spot, Side.LONG, 1, entry_price=100.0, iv=0.15),
    ]
    margin = estimate_margin(legs, instrument, chain.spot, chain.time_to_expiry_years, chain.risk_free_rate)
    assert margin.total_margin >= 0
