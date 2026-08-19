from datetime import date, datetime

import pytest

from app.analytics.commentary import expiry_band_probability, support_resistance
from app.core.black_scholes import Greeks, OptionType
from app.data.mock_feed import generate_option_chain
from app.data.models import ChainRow, LegQuote, OptionChain


def _leg(oi: int) -> LegQuote:
    return LegQuote(
        option_type=OptionType.CALL,
        ltp=1.0,
        bid=0.9,
        ask=1.1,
        oi=oi,
        oi_change=0,
        volume=0,
        iv=0.15,
        greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0),
    )


def _chain(spot: float, strike_oi: dict[float, tuple[int, int]]) -> OptionChain:
    """``strike_oi``: {strike: (call_oi, put_oi)}."""
    rows = [
        ChainRow(strike=strike, call=_leg(call_oi), put=_leg(put_oi))
        for strike, (call_oi, put_oi) in strike_oi.items()
    ]
    return OptionChain(
        symbol="TEST",
        spot=spot,
        expiry=date(2026, 8, 25),
        timestamp=datetime(2026, 8, 19, 10, 0),
        time_to_expiry_years=0.02,
        risk_free_rate=0.065,
        prev_close=spot,
        rows=rows,
    )


def test_support_resistance_picks_heaviest_oi_on_each_side_of_spot():
    chain = _chain(
        spot=100.0,
        strike_oi={
            90.0: (100, 500),  # below spot, heaviest put OI -> support
            95.0: (200, 300),
            100.0: (400, 400),
            105.0: (900, 150),  # above spot, heaviest call OI -> resistance
            110.0: (300, 50),
        },
    )
    result = support_resistance(chain)
    assert result.support_strike == 90.0
    assert result.support_put_oi == 500
    assert result.resistance_strike == 105.0
    assert result.resistance_call_oi == 900


def test_support_resistance_falls_back_when_chain_is_one_sided():
    # Every listed strike is below spot — no "above or at spot" side exists.
    chain = _chain(spot=200.0, strike_oi={90.0: (10, 20), 95.0: (30, 5)})
    result = support_resistance(chain)
    assert result.support_strike in {90.0, 95.0}
    assert result.resistance_strike in {90.0, 95.0}


def test_support_resistance_against_real_mock_chain():
    chain = generate_option_chain("NIFTY", seed=30)
    result = support_resistance(chain)
    strikes = {row.strike for row in chain.rows}
    assert result.support_strike in strikes
    assert result.resistance_strike in strikes
    assert result.support_strike <= chain.spot or result.support_strike == min(strikes)
    assert result.resistance_strike >= chain.spot or result.resistance_strike == max(strikes)


def test_expiry_band_probability_symmetric_band_matches_closed_form():
    spot, iv, t = 100.0, 0.20, 0.05
    result = expiry_band_probability(spot, iv, t, band_pct=0.002)
    assert result["lower"] == pytest.approx(99.8, abs=0.01)
    assert result["upper"] == pytest.approx(100.2, abs=0.01)
    # Closed form for the zero-drift symmetric-log-band case: 2*N(z) - 1.
    import math

    from app.core.black_scholes import norm_cdf

    sigma_t = iv * math.sqrt(t)
    z = math.log(1.002) / sigma_t
    expected = 2 * norm_cdf(z) - 1
    assert result["probability"] == pytest.approx(expected, abs=1e-4)


def test_expiry_band_probability_higher_for_more_time_or_higher_iv():
    base = expiry_band_probability(100.0, 0.15, 0.01)
    more_time = expiry_band_probability(100.0, 0.15, 0.10)
    more_iv = expiry_band_probability(100.0, 0.60, 0.01)
    # More time or more vol widens the terminal-price distribution, which
    # LOWERS the probability of landing in a fixed-width band.
    assert more_time["probability"] < base["probability"]
    assert more_iv["probability"] < base["probability"]


def test_expiry_band_probability_bounded_in_zero_one():
    result = expiry_band_probability(24800.0, 0.12, 0.0137)
    assert 0.0 <= result["probability"] <= 1.0


def test_expiry_band_probability_none_when_no_time_or_iv_left():
    assert expiry_band_probability(100.0, 0.0, 0.05)["probability"] is None
    assert expiry_band_probability(100.0, 0.2, 0.0)["probability"] is None
