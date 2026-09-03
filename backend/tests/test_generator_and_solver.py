from app.data.instruments import get_instrument
from app.data.mock_feed import generate_option_chain
from app.strategy.generator import (
    bear_call_spreads,
    bear_put_spreads,
    bull_call_spreads,
    bull_put_spreads,
    generate_all_candidates,
    iron_condors,
    iron_flies,
)
from app.strategy.legs import Side
from app.strategy.solver import StrategyConstraints, discover_strategies, evaluate_strategy


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
    assert "bull_call_spread" in strategy_types
    assert "bear_put_spread" in strategy_types
    assert "iron_condor" in strategy_types
    assert "iron_fly" in strategy_types


def test_debit_spread_generator_shapes_are_correct():
    chain = generate_option_chain("NIFTY", seed=33)
    calls = bull_call_spreads(chain)
    puts = bear_put_spreads(chain)

    assert len(calls) > 0
    assert len(puts) > 0

    for cand in calls:
        assert cand.strategy_type == "bull_call_spread"
        assert len(cand.legs) == 2
        long_leg, short_leg = cand.legs
        assert long_leg.side == Side.LONG
        assert short_leg.side == Side.SHORT
        assert long_leg.strike < short_leg.strike  # long the near strike, short further OTM
        assert long_leg.strike <= chain.spot

    for cand in puts:
        assert cand.strategy_type == "bear_put_spread"
        long_leg, short_leg = cand.legs
        assert long_leg.side == Side.LONG
        assert short_leg.side == Side.SHORT
        assert long_leg.strike > short_leg.strike  # long the near strike, short further OTM
        assert long_leg.strike >= chain.spot


def test_debit_spreads_are_genuinely_net_debit_not_credit():
    # The defining feature of a debit spread: you pay to enter, unlike
    # every credit shape elsewhere in this file (positive net_entry_credit).
    chain = generate_option_chain("NIFTY", seed=34)
    instrument = get_instrument("NIFTY")
    for cand in bull_call_spreads(chain)[:5] + bear_put_spreads(chain)[:5]:
        result = evaluate_strategy(cand.legs, chain, instrument, n_paths=1000, strategy_type=cand.strategy_type)
        assert result.margin.net_entry_credit < 0, f"{cand.strategy_type} should be a net debit"


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


def test_max_loss_cap_none_allows_undefined_risk_candidates_through():
    # Ratio spreads have genuinely unbounded downside on the excess short
    # leg (payoff_extrema returns the -1e12 sentinel for that) — any finite
    # max_loss_cap always rejects them (see generator.py's ratio_spreads
    # docstring). max_loss_cap=None ("Unlimited" in the UI) is the only way
    # to actually see one.
    chain = generate_option_chain("NIFTY", seed=31)
    instrument = get_instrument("NIFTY")
    capped = StrategyConstraints(
        min_pop=0.0, min_yield_pct=0.0, max_profit_cap=None, max_loss_cap=2_000_000, margin_cap=1_000_000,
        n_paths_screen=1000, n_paths_final=1000, strategy_types=frozenset({"ratio_spread_put"}),
    )
    uncapped = StrategyConstraints(
        min_pop=0.0, min_yield_pct=0.0, max_profit_cap=None, max_loss_cap=None, margin_cap=1_000_000,
        n_paths_screen=1000, n_paths_final=1000, strategy_types=frozenset({"ratio_spread_put"}),
    )
    assert discover_strategies(chain, instrument, capped, top_n=5) == []
    uncapped_results = discover_strategies(chain, instrument, uncapped, top_n=5)
    assert len(uncapped_results) > 0
    assert all(r.payoff.unlimited_downside_risk for r in uncapped_results)


def test_max_profit_cap_none_avoids_conflict_with_min_yield_pct():
    # yield_pct = max_profit / margin, so a high min_yield_pct on a
    # high-margin candidate forces a large max_profit — a finite
    # max_profit_cap can then reject every candidate that clears the yield
    # floor. max_profit_cap=None (unlimited) removes that ceiling entirely,
    # letting min_yield_pct be the only thing governing profit magnitude.
    chain = generate_option_chain("NIFTY", seed=32)
    instrument = get_instrument("NIFTY")
    conflicting = StrategyConstraints(
        min_pop=0.0, min_yield_pct=0.5, max_profit_cap=200, max_loss_cap=None, margin_cap=1_000_000,
        n_paths_screen=1000, n_paths_final=1000,
    )
    unlimited_profit = StrategyConstraints(
        min_pop=0.0, min_yield_pct=0.5, max_profit_cap=None, max_loss_cap=None, margin_cap=1_000_000,
        n_paths_screen=1000, n_paths_final=1000,
    )
    assert discover_strategies(chain, instrument, conflicting, top_n=5) == []
    results = discover_strategies(chain, instrument, unlimited_profit, top_n=5)
    assert len(results) > 0
    assert all(r.yield_pct >= 0.5 - 1e-6 for r in results)


def test_solver_discovers_debit_spreads_when_restricted_to_them():
    chain = generate_option_chain("NIFTY", seed=35)
    instrument = get_instrument("NIFTY")
    constraints = StrategyConstraints(
        min_pop=0.0, min_yield_pct=0.0, max_profit_cap=None, max_loss_cap=None, margin_cap=1_000_000,
        n_paths_screen=1000, n_paths_final=4000,
        strategy_types=frozenset({"bull_call_spread", "bear_put_spread"}),
    )
    results = discover_strategies(chain, instrument, constraints, top_n=5)
    assert results
    assert all(r.strategy_type in ("bull_call_spread", "bear_put_spread") for r in results)
    assert all(r.margin.net_entry_credit < 0 for r in results)


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
