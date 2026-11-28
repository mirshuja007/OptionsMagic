import pytest

from app.analytics.conditions import (
    And,
    GapCondition,
    GreekCondition,
    IvRegimeCondition,
    MarketSnapshot,
    Not,
    Or,
    PremiumChangeCondition,
    StraddlePctCondition,
    VixCondition,
)


def _snap(**overrides) -> MarketSnapshot:
    base = dict(spot=100.0, prev_close=100.0)
    base.update(overrides)
    return MarketSnapshot(**base)


# --- GapCondition -----------------------------------------------------------

def test_gap_condition_up_within_band():
    cond = GapCondition(direction="up", min_pct=0.0, max_pct=0.5)
    assert cond.evaluate(_snap(spot=100.3, prev_close=100.0)) is True


def test_gap_condition_up_rejects_down_move():
    cond = GapCondition(direction="up", min_pct=0.0, max_pct=0.5)
    assert cond.evaluate(_snap(spot=99.7, prev_close=100.0)) is False


def test_gap_condition_down_within_band():
    cond = GapCondition(direction="down", min_pct=0.0, max_pct=0.5)
    assert cond.evaluate(_snap(spot=99.7, prev_close=100.0)) is True


def test_gap_condition_rejects_move_outside_band():
    cond = GapCondition(direction="down", min_pct=0.0, max_pct=0.5)
    assert cond.evaluate(_snap(spot=98.0, prev_close=100.0)) is False


def test_gap_condition_flat_uses_max_pct_as_symmetric_band():
    cond = GapCondition(direction="flat", max_pct=0.2)
    assert cond.evaluate(_snap(spot=100.1, prev_close=100.0)) is True
    assert cond.evaluate(_snap(spot=99.85, prev_close=100.0)) is True
    assert cond.evaluate(_snap(spot=100.5, prev_close=100.0)) is False


def test_gap_condition_dead_flat_is_not_up_or_down():
    assert GapCondition(direction="up").evaluate(_snap(spot=100.0, prev_close=100.0)) is False
    assert GapCondition(direction="down").evaluate(_snap(spot=100.0, prev_close=100.0)) is False


# --- VixCondition / GreekCondition / StraddlePctCondition -------------------

def test_vix_condition():
    assert VixCondition(">", 15.0).evaluate(_snap(vix=16.0)) is True
    assert VixCondition(">", 15.0).evaluate(_snap(vix=14.0)) is False


def test_vix_condition_missing_field_raises():
    with pytest.raises(ValueError, match="vix"):
        VixCondition(">", 15.0).evaluate(_snap())


def test_greek_condition():
    assert GreekCondition("vega", ">=", 10.0).evaluate(_snap(vega=12.0)) is True
    assert GreekCondition("theta", "<", -5.0).evaluate(_snap(theta=-8.0)) is True
    assert GreekCondition("theta", "<", -5.0).evaluate(_snap(theta=-2.0)) is False


def test_straddle_pct_condition():
    cond = StraddlePctCondition(">=", 1.0)
    assert cond.evaluate(_snap(spot=100.0, atm_call_premium=0.6, atm_put_premium=0.5)) is True  # 1.1%
    assert cond.evaluate(_snap(spot=100.0, atm_call_premium=0.3, atm_put_premium=0.3)) is False  # 0.6%


# --- PremiumChangeCondition --------------------------------------------------

def test_premium_change_condition_entry_reference_as_exit_rule():
    cond = PremiumChangeCondition("entry", ">=", 100.0)
    assert cond.evaluate(_snap(option_premium=20.0, option_entry_premium=10.0)) is True  # +100%
    assert cond.evaluate(_snap(option_premium=15.0, option_entry_premium=10.0)) is False  # +50%


def test_premium_change_condition_day_open_reference_as_entry_screener():
    cond = PremiumChangeCondition("day_open", ">=", 100.0)
    assert cond.evaluate(_snap(option_premium=22.0, option_day_open_premium=10.0)) is True


def test_premium_change_condition_prev_close_reference():
    cond = PremiumChangeCondition("prev_close", "<=", -50.0)
    assert cond.evaluate(_snap(option_premium=4.0, option_prev_close_premium=10.0)) is True  # -60%


def test_premium_change_condition_missing_reference_raises():
    with pytest.raises(ValueError, match="option_entry_premium"):
        PremiumChangeCondition("entry", ">=", 100.0).evaluate(_snap(option_premium=20.0))


def test_premium_change_condition_non_positive_reference_is_false_not_error():
    cond = PremiumChangeCondition("entry", ">=", 100.0)
    assert cond.evaluate(_snap(option_premium=20.0, option_entry_premium=0.0)) is False


# --- IvRegimeCondition --------------------------------------------------------

def test_iv_regime_condition_rich():
    cond = IvRegimeCondition("rich")
    assert cond.evaluate(_snap(iv=0.20, realized_vol=0.14)) is True
    assert cond.evaluate(_snap(iv=0.15, realized_vol=0.14)) is False


def test_iv_regime_condition_cheap_and_neutral():
    assert IvRegimeCondition("cheap").evaluate(_snap(iv=0.10, realized_vol=0.16)) is True
    assert IvRegimeCondition("neutral").evaluate(_snap(iv=0.15, realized_vol=0.14)) is True


# --- And / Or / Not combinators ---------------------------------------------

def test_and_requires_every_condition():
    cond = And((VixCondition(">", 15.0), GapCondition(direction="down", max_pct=0.5)))
    assert cond.evaluate(_snap(vix=16.0, spot=99.7, prev_close=100.0)) is True
    assert cond.evaluate(_snap(vix=10.0, spot=99.7, prev_close=100.0)) is False


def test_or_requires_any_condition():
    cond = Or((VixCondition(">", 25.0), GapCondition(direction="down", max_pct=0.5)))
    assert cond.evaluate(_snap(vix=10.0, spot=99.7, prev_close=100.0)) is True  # gap matches even though VIX doesn't
    assert cond.evaluate(_snap(vix=10.0, spot=100.0, prev_close=100.0)) is False


def test_not_inverts():
    cond = Not(VixCondition(">", 15.0))
    assert cond.evaluate(_snap(vix=10.0)) is True
    assert cond.evaluate(_snap(vix=20.0)) is False


def test_nested_and_or_tree():
    # (VIX > 15 AND gap down 0-0.5%) OR (premium already +100% from entry)
    tree = Or((
        And((VixCondition(">", 15.0), GapCondition(direction="down", max_pct=0.5))),
        PremiumChangeCondition("entry", ">=", 100.0),
    ))
    # Neither branch true
    assert tree.evaluate(_snap(vix=10.0, spot=100.0, prev_close=100.0, option_premium=15.0, option_entry_premium=10.0)) is False
    # Only the premium branch true
    assert tree.evaluate(_snap(vix=10.0, spot=100.0, prev_close=100.0, option_premium=25.0, option_entry_premium=10.0)) is True
    # Only the VIX+gap branch true
    assert tree.evaluate(_snap(vix=20.0, spot=99.7, prev_close=100.0, option_premium=15.0, option_entry_premium=10.0)) is True
