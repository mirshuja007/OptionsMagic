"""Constraint-Solving Strategy Discovery Engine.

Sweeps every combinatorially generated candidate (credit spreads, iron
condors, iron flies, ratio spreads) against the user's risk/capital/
probability boundaries, and returns the top strategies by expected value.

Screening runs Monte-Carlo PoP/EV at a cheap path count across every
candidate (numpy-vectorized, so a few hundred candidates x a few thousand
paths is sub-second); the surviving top candidates are then re-scored at a
much higher path count for reporting accuracy.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from app.core.pop import strategy_pop_monte_carlo
from app.data.instruments import Instrument
from app.data.mock_feed import OptionChain
from app.margin.span import MarginEstimate, estimate_margin
from app.strategy.generator import Candidate, generate_all_candidates
from app.strategy.legs import Leg, PayoffExtrema, payoff_at_expiry, payoff_extrema


@dataclass(frozen=True)
class StrategyConstraints:
    min_pop: float  # e.g. 0.80 for 80%
    min_yield_pct: float  # e.g. 0.01 for 1% of margin blocked
    max_profit_cap: float  # rupee ceiling
    max_loss_cap: float  # rupee cap, positive number
    margin_cap: float  # rupee ceiling on margin blocked
    n_paths_screen: int = 4000
    n_paths_final: int = 50_000


@dataclass(frozen=True)
class StrategyResult:
    strategy_type: str
    legs: list[Leg]
    margin: MarginEstimate
    payoff: PayoffExtrema
    probability_of_profit: float
    expected_value: float
    sharpe: float
    yield_pct: float


def _avg_iv(legs: list[Leg]) -> float:
    return statistics.fmean(leg.iv for leg in legs)


def _score_candidate(
    candidate: Candidate,
    chain: OptionChain,
    instrument: Instrument,
    constraints: StrategyConstraints,
    n_paths: int,
) -> StrategyResult | None:
    legs = candidate.legs
    extrema = payoff_extrema(legs, instrument.lot_size)
    if extrema.max_profit > constraints.max_profit_cap:
        return None
    if extrema.max_loss < -constraints.max_loss_cap:
        return None

    margin = estimate_margin(legs, instrument, chain.spot, chain.time_to_expiry_years, chain.risk_free_rate)
    if margin.total_margin <= 0 or margin.total_margin > constraints.margin_cap:
        return None

    yield_pct = extrema.max_profit / margin.total_margin
    if yield_pct < constraints.min_yield_pct:
        return None

    payoff_fn = lambda terminal: payoff_at_expiry(legs, instrument.lot_size, terminal)  # noqa: E731
    mc = strategy_pop_monte_carlo(
        payoff_fn,
        chain.spot,
        chain.time_to_expiry_years,
        chain.risk_free_rate,
        sigma=_avg_iv(legs),
        n_paths=n_paths,
    )
    if mc["probability_of_profit"] < constraints.min_pop:
        return None

    return StrategyResult(
        strategy_type=candidate.strategy_type,
        legs=legs,
        margin=margin,
        payoff=extrema,
        probability_of_profit=round(mc["probability_of_profit"], 4),
        expected_value=round(mc["expected_value"], 2),
        sharpe=round(mc["sharpe"], 4),
        yield_pct=round(yield_pct, 4),
    )


def discover_strategies(
    chain: OptionChain,
    instrument: Instrument,
    constraints: StrategyConstraints,
    top_n: int = 3,
) -> list[StrategyResult]:
    candidates = generate_all_candidates(chain)

    screened: list[StrategyResult] = []
    for candidate in candidates:
        result = _score_candidate(candidate, chain, instrument, constraints, constraints.n_paths_screen)
        if result is not None:
            screened.append(result)

    screened.sort(key=lambda r: r.expected_value, reverse=True)
    finalists = screened[:top_n]

    refined: list[StrategyResult] = []
    for result in finalists:
        candidate = Candidate(result.strategy_type, result.legs)
        rescored = _score_candidate(candidate, chain, instrument, constraints, constraints.n_paths_final)
        refined.append(rescored if rescored is not None else result)

    refined.sort(key=lambda r: r.expected_value, reverse=True)
    return refined
