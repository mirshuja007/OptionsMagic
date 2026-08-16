"""Probability-of-Profit estimation for single legs and combined multi-leg strategies.

Two methods are exposed:
  * ``leg_pop_delta_approx`` — fast closed-form approximation (|delta| as a proxy
    for probability of expiring ITM, adapted for probability of profit of the
    opposite side, per standard options-desk heuristics).
  * ``strategy_pop_monte_carlo`` — a GBM Monte Carlo simulation of the underlying
    at expiry, which is accurate for arbitrary combined payoffs (spreads,
    condors, flies, ratios) where the breakeven zone is not a single strike.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

from app.core.black_scholes import OptionType, greeks, norm_cdf


def leg_pop_delta_approx(
    spot: float,
    strike: float,
    t: float,
    r: float,
    sigma: float,
    option_type: OptionType,
    is_seller: bool,
    q: float = 0.0,
) -> float:
    """Standard options-desk heuristic: |delta| approximates the risk-neutral
    probability of expiring in-the-money. A short (seller) leg profits when the
    option expires OTM, so its PoP is ``1 - |delta|``; a long (buyer) leg's PoP
    is the complement, ``|delta|``.
    """
    if t <= 0:
        return 1.0 if _is_otm(spot, strike, option_type) == is_seller else 0.0
    prob_itm = abs(greeks(spot, strike, t, r, sigma, option_type, q).delta)
    return (1.0 - prob_itm) if is_seller else prob_itm


def _is_otm(spot: float, strike: float, option_type: OptionType) -> bool:
    if option_type == OptionType.CALL:
        return spot <= strike
    return spot >= strike


def simulate_terminal_prices(
    spot: float,
    t: float,
    r: float,
    sigma: float,
    q: float = 0.0,
    n_paths: int = 50_000,
    seed: int | None = None,
) -> np.ndarray:
    """Terminal underlying prices at expiry under risk-neutral GBM."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n_paths)
    drift = (r - q - 0.5 * sigma * sigma) * t
    diffusion = sigma * math.sqrt(t) * z
    return spot * np.exp(drift + diffusion)


def strategy_pop_monte_carlo(
    payoff_fn: Callable[[np.ndarray], np.ndarray],
    spot: float,
    t: float,
    r: float,
    sigma: float,
    q: float = 0.0,
    n_paths: int = 50_000,
    seed: int | None = 42,
) -> dict:
    """Run GBM Monte Carlo and return PoP plus expected value / EV stats for a strategy.

    ``payoff_fn`` maps an array of terminal spot prices to an array of net P&L
    (already net of premium collected/paid) for the combined strategy.
    """
    terminal = simulate_terminal_prices(spot, t, r, sigma, q, n_paths, seed)
    pnl = payoff_fn(terminal)
    pop = float(np.mean(pnl > 0))
    ev = float(np.mean(pnl))
    std = float(np.std(pnl))
    sharpe = ev / std if std > 1e-9 else 0.0
    return {
        "probability_of_profit": pop,
        "expected_value": ev,
        "pnl_std": std,
        "sharpe": sharpe,
        "worst_case": float(np.min(pnl)),
        "best_case": float(np.max(pnl)),
    }


def breakeven_probability_range(
    lower: float,
    upper: float,
    spot: float,
    t: float,
    r: float,
    sigma: float,
    q: float = 0.0,
) -> float:
    """Analytic PoP for strategies whose profit zone is a single contiguous
    [lower, upper] band in terminal price (true for credit spreads, iron
    condors/flies at defined strikes). Uses the risk-neutral lognormal CDF of
    the terminal price, no simulation needed.
    """
    if t <= 0:
        return 1.0 if lower <= spot <= upper else 0.0

    def _cdf_terminal_le(x: float) -> float:
        """P(S_T <= x) under risk-neutral GBM."""
        if x <= 0:
            return 0.0
        d2 = (math.log(spot / x) + (r - q - 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
        return 1.0 - norm_cdf(d2)

    return _cdf_terminal_le(upper) - _cdf_terminal_le(lower)
