import numpy as np
import pytest

from app.core.black_scholes import OptionType
from app.core.pop import breakeven_probability_range, leg_pop_delta_approx, strategy_pop_monte_carlo

SPOT, STRIKE, T, R, SIGMA = 24800.0, 24800.0, 0.05, 0.065, 0.12


def test_short_call_pop_complement_of_long_call_pop():
    short_pop = leg_pop_delta_approx(SPOT, STRIKE, T, R, SIGMA, OptionType.CALL, is_seller=True)
    long_pop = leg_pop_delta_approx(SPOT, STRIKE, T, R, SIGMA, OptionType.CALL, is_seller=False)
    assert short_pop == pytest.approx(1 - long_pop, abs=1e-9)


def test_deep_otm_short_put_has_high_pop():
    pop = leg_pop_delta_approx(SPOT, 20000.0, T, R, SIGMA, OptionType.PUT, is_seller=True)
    assert pop > 0.9


def test_monte_carlo_pop_matches_analytic_range_for_bull_put_spread():
    short_strike, long_strike = 24700.0, 24500.0
    credit = 40.0  # illustrative net credit received per unit

    def payoff(terminal: np.ndarray) -> np.ndarray:
        short_put_loss = np.maximum(short_strike - terminal, 0.0)
        long_put_gain = np.maximum(long_strike - terminal, 0.0)
        return credit - short_put_loss + long_put_gain

    mc = strategy_pop_monte_carlo(payoff, SPOT, T, R, SIGMA, n_paths=200_000, seed=1)
    # Profitable whenever terminal > short_strike - credit
    breakeven = short_strike - credit
    analytic_pop = breakeven_probability_range(breakeven, 1e9, SPOT, T, R, SIGMA)
    assert mc["probability_of_profit"] == pytest.approx(analytic_pop, abs=0.01)


def test_monte_carlo_expected_value_finite_and_sharpe_computed():
    def payoff(terminal: np.ndarray) -> np.ndarray:
        return terminal - SPOT  # a synthetic long-underlying payoff

    result = strategy_pop_monte_carlo(payoff, SPOT, T, R, SIGMA, n_paths=10_000, seed=7)
    assert np.isfinite(result["expected_value"])
    assert np.isfinite(result["sharpe"])
    assert result["pnl_std"] > 0
