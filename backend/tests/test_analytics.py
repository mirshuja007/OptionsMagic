from app.analytics.max_pain import compute_max_pain
from app.analytics.oi import gamma_exposure, multistrike_oi, smart_oi_score
from app.analytics.pcr import compute_pcr
from app.analytics.straddle import atm_straddle, multistrike_straddle, premium_decay_curve
from app.analytics.volatility import atm_iv, iv_grid, realized_volatility, volatility_skew
from app.data.mock_feed import generate_option_chain, generate_minute_series


def test_max_pain_strike_is_one_of_the_listed_strikes():
    chain = generate_option_chain("NIFTY", seed=10)
    result = compute_max_pain(chain)
    strikes = {row.strike for row in chain.rows}
    assert result.max_pain_strike in strikes
    assert len(result.payout_by_strike) == len(chain.rows)


def test_pcr_is_nonnegative():
    chain = generate_option_chain("NIFTY", seed=11)
    result = compute_pcr(chain)
    assert result.pcr_oi >= 0
    assert result.pcr_volume >= 0


def test_multistrike_oi_matches_row_count():
    chain = generate_option_chain("NIFTY", seed=12)
    rows = multistrike_oi(chain)
    assert len(rows) == len(chain.rows)


def test_smart_oi_score_bounded():
    chain = generate_option_chain("NIFTY", seed=13)
    result = smart_oi_score(chain)
    assert -1.0 <= result["score"] <= 1.0
    assert result["bias"] in {"bullish", "bearish", "neutral"}


def test_gamma_exposure_regime_label():
    chain = generate_option_chain("NIFTY", seed=14)
    result = gamma_exposure(chain)
    assert result["regime"] in {"long_gamma", "short_gamma"}
    assert len(result["by_strike"]) == len(chain.rows)


def test_iv_grid_matches_chain_and_atm_iv_reasonable():
    chain = generate_option_chain("NIFTY", seed=15)
    grid = iv_grid(chain)
    assert len(grid) == len(chain.rows)
    iv = atm_iv(chain)
    assert 0.03 < iv < 1.0


def test_volatility_skew_puts_richer_than_calls_for_index_smirk():
    chain = generate_option_chain("NIFTY", seed=16)
    skew = volatility_skew(chain)
    assert skew["skew"] > 0  # documented Indian index smirk: OTM puts pricier than OTM calls


def test_realized_volatility_positive_for_a_moving_series():
    prices = [p for _, p in generate_minute_series("NIFTY", seed=17)]
    hv = realized_volatility(prices)
    assert hv > 0


def test_atm_straddle_combined_equals_call_plus_put():
    chain = generate_option_chain("NIFTY", seed=18)
    s = atm_straddle(chain)
    assert s.combined_premium == round(s.call_premium + s.put_premium, 2)


def test_multistrike_straddle_sorted_by_strike():
    chain = generate_option_chain("NIFTY", seed=19)
    points = multistrike_straddle(chain, num_strikes=5)
    strikes = [p.strike for p in points]
    assert strikes == sorted(strikes)


def test_premium_decay_curve_decreases_toward_expiry():
    chain = generate_option_chain("NIFTY", seed=20)
    curve = premium_decay_curve(chain, points=8)
    assert curve[0]["time_to_expiry_years"] > curve[-1]["time_to_expiry_years"]
    assert curve[-1]["time_to_expiry_years"] == 0.0
    # combined premium should trend down as time value bleeds out (allow noise-free monotonic check on the mean)
    assert curve[-1]["combined_premium"] <= curve[0]["combined_premium"]
