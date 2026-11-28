import math

import pytest

from app.analytics.greek_bias import gamma_theta_risk_ratio, greek_bias


def test_gamma_theta_risk_ratio_known_values():
    # move = 100*0.01 = 1.0; gamma_cost = 0.5*0.002*1.0^2*75 = 0.075; ratio = 0.075/0.15 = 0.5
    ratio = gamma_theta_risk_ratio(gamma=0.002, theta=-0.15, spot=100.0, lot_size=75, move_pct=1.0)
    assert ratio == pytest.approx(0.5)


def test_gamma_theta_risk_ratio_scales_with_move_squared():
    r1 = gamma_theta_risk_ratio(gamma=0.002, theta=-0.15, spot=100.0, lot_size=75, move_pct=1.0)
    r2 = gamma_theta_risk_ratio(gamma=0.002, theta=-0.15, spot=100.0, lot_size=75, move_pct=2.0)
    assert r2 == pytest.approx(r1 * 4)  # second-order term scales with move^2


def test_gamma_theta_risk_ratio_infinite_when_theta_is_zero():
    assert gamma_theta_risk_ratio(gamma=0.002, theta=0.0, spot=100.0, lot_size=75) == math.inf


def test_greek_bias_rich_iv_plus_low_gamma_risk_favors_selling():
    result = greek_bias(gamma=0.0005, theta=-10.0, spot=100.0, lot_size=75, iv_regime="rich")
    assert result.bias == "sell_premium"
    assert result.score == 2
    assert len(result.reasons) == 2


def test_greek_bias_cheap_iv_plus_high_gamma_risk_favors_buying():
    result = greek_bias(gamma=0.05, theta=-0.5, spot=100.0, lot_size=75, iv_regime="cheap")
    assert result.bias == "buy_premium"
    assert result.score == -2


def test_greek_bias_neutral_iv_and_middling_gamma_risk_is_neutral():
    # Pick gamma/theta so the ratio lands strictly between the low/high thresholds.
    result = greek_bias(gamma=0.0012, theta=-0.1, spot=100.0, lot_size=75, iv_regime="neutral")
    assert 0.3 < result.gamma_theta_ratio < 1.0
    assert result.bias == "neutral"
    assert result.score == 0


def test_greek_bias_conflicting_signals_can_cancel_to_neutral():
    # Rich IV (+1) but high gamma risk (-1) -> net zero, still "neutral" despite two real reasons.
    result = greek_bias(gamma=0.05, theta=-0.5, spot=100.0, lot_size=75, iv_regime="rich")
    assert result.score == 0
    assert result.bias == "neutral"
    assert len(result.reasons) == 2
