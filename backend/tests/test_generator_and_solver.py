from app.data.instruments import get_instrument
from app.data.mock_feed import generate_option_chain
from app.strategy.generator import bear_call_spreads, bull_put_spreads, generate_all_candidates, iron_condors, iron_flies
from app.strategy.solver import StrategyConstraints, discover_strategies


def test_generator_produces_defined_risk_candidates():
    chain = generate_option_chain("NIFTY", seed=5)
    puts = bull_put_spreads(chain)
    calls = bear_call_spreads(chain)
    condors = iron_condors(chain, max_combos=50)
    flies = iron_flies(chain)

    assert len(puts) > 0
    assert len(calls) > 0
    assert len(condors) > 0
    assert len(flies) > 0

    for cand in puts:
        assert cand.strategy_type == "bull_put_spread"
        assert len(cand.legs) == 2

    for cand in condors:
        assert len(cand.legs) == 4


def test_generate_all_candidates_nonempty():
    chain = generate_option_chain("BANKNIFTY", seed=6)
    candidates = generate_all_candidates(chain)
    assert len(candidates) > 20
    strategy_types = {c.strategy_type for c in candidates}
    assert "bull_put_spread" in strategy_types
    assert "bear_call_spread" in strategy_types
    assert "iron_condor" in strategy_types
    assert "iron_fly" in strategy_types


def test_solver_respects_constraint_caps():
    chain = generate_option_chain("NIFTY", seed=7)
    instrument = get_instrument("NIFTY")
    constraints = StrategyConstraints(
        min_pop=0.5,
        min_yield_pct=0.005,
        max_profit_cap=20000,
        max_loss_cap=15000,
        margin_cap=300000,
        n_paths_screen=1500,
        n_paths_final=8000,
    )
    results = discover_strategies(chain, instrument, constraints, top_n=3)

    assert len(results) <= 3
    for r in results:
        assert r.probability_of_profit >= constraints.min_pop - 0.03  # small MC noise tolerance
        assert r.payoff.max_profit <= constraints.max_profit_cap
        assert r.payoff.max_loss >= -constraints.max_loss_cap
        assert r.margin.total_margin <= constraints.margin_cap
        assert r.yield_pct >= constraints.min_yield_pct - 1e-6


def test_solver_returns_sorted_by_composite_score_desc():
    # Ranking is the user-tunable yield/PoP/Sharpe blend (composite_score),
    # not raw expected_value — see solver.py's RANKING_WEIGHTS/module docstring.
    chain = generate_option_chain("NIFTY", seed=8)
    instrument = get_instrument("NIFTY")
    constraints = StrategyConstraints(
        min_pop=0.5,
        min_yield_pct=0.0,
        max_profit_cap=50000,
        max_loss_cap=30000,
        margin_cap=1_000_000,
        n_paths_screen=1500,
        n_paths_final=8000,
    )
    results = discover_strategies(chain, instrument, constraints, top_n=3)
    scores = [r.composite_score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_solver_with_impossible_constraints_returns_empty():
    chain = generate_option_chain("NIFTY", seed=9)
    instrument = get_instrument("NIFTY")
    constraints = StrategyConstraints(
        min_pop=0.999,
        min_yield_pct=5.0,  # 500% yield, unreachable
        max_profit_cap=100,
        max_loss_cap=10,
        margin_cap=1000,
        n_paths_screen=500,
        n_paths_final=1000,
    )
    results = discover_strategies(chain, instrument, constraints, top_n=3)
    assert results == []
