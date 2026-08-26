"""Manual strategy builder backend: evaluate_strategy (analyze an arbitrary,
user-assembled leg list) and optimize_legs (nearby-strike search that
improves max profit without worsening max loss/margin) — see
app.strategy.solver's docstrings for the design rationale.
"""
from app.core.black_scholes import OptionType
from app.data.instruments import get_instrument
from app.data.mock_feed import generate_option_chain
from app.strategy.generator import bull_put_spreads
from app.strategy.legs import Leg, Side
from app.strategy.solver import evaluate_strategy, optimize_legs


def test_evaluate_strategy_matches_generator_candidate():
    chain = generate_option_chain("NIFTY", seed=11)
    instrument = get_instrument("NIFTY")
    candidate = bull_put_spreads(chain)[0]

    result = evaluate_strategy(candidate.legs, chain, instrument, n_paths=5000, strategy_type=candidate.strategy_type)

    assert result.strategy_type == candidate.strategy_type
    assert result.legs == candidate.legs
    assert result.margin.total_margin > 0
    assert 0.0 <= result.probability_of_profit <= 1.0


def test_evaluate_strategy_never_filters_even_far_outside_normal_bounds():
    # discover_strategies would reject a 100-lot naked short on margin/PoP
    # grounds; evaluate_strategy has no constraints to fail against — it
    # just reports whatever the numbers are, for the manual builder to show.
    chain = generate_option_chain("NIFTY", seed=12)
    instrument = get_instrument("NIFTY")
    row = chain.rows[0]
    legs = [Leg(OptionType.PUT, row.strike, Side.SHORT, 100, entry_price=row.put.bid, iv=row.put.iv)]

    result = evaluate_strategy(legs, chain, instrument, n_paths=2000)

    assert result.margin.total_margin > 0


def test_optimize_legs_alternatives_beat_baseline_without_worse_risk():
    chain = generate_option_chain("NIFTY", seed=13)
    instrument = get_instrument("NIFTY")
    candidate = bull_put_spreads(chain)[0]
    baseline = evaluate_strategy(candidate.legs, chain, instrument, n_paths=3000, strategy_type=candidate.strategy_type)

    alternatives = optimize_legs(candidate.legs, chain, instrument, strike_range=2, n_paths=2000, max_combos=100, top_n=5)

    for alt in alternatives:
        assert alt.payoff.max_profit > baseline.payoff.max_profit
        assert alt.payoff.max_loss >= baseline.payoff.max_loss
        assert alt.margin.total_margin <= baseline.margin.total_margin * 1.1 + 1e-6


def test_optimize_legs_preserves_leg_shape():
    # Only strikes should move — option_type/side/quantity_lots per leg must
    # stay exactly what the user picked, or this isn't "the same strategy,
    # tweaked" any more.
    chain = generate_option_chain("NIFTY", seed=14)
    instrument = get_instrument("NIFTY")
    candidate = bull_put_spreads(chain)[0]

    alternatives = optimize_legs(candidate.legs, chain, instrument, strike_range=2, n_paths=2000, max_combos=100, top_n=5)

    for alt in alternatives:
        assert len(alt.legs) == len(candidate.legs)
        for orig, new in zip(candidate.legs, alt.legs):
            assert orig.option_type == new.option_type
            assert orig.side == new.side
            assert orig.quantity_lots == new.quantity_lots


def test_optimize_legs_returns_sorted_by_max_profit_desc():
    chain = generate_option_chain("NIFTY", seed=16)
    instrument = get_instrument("NIFTY")
    candidate = bull_put_spreads(chain)[0]

    alternatives = optimize_legs(candidate.legs, chain, instrument, strike_range=3, n_paths=2000, max_combos=200, top_n=5)

    profits = [alt.payoff.max_profit for alt in alternatives]
    assert profits == sorted(profits, reverse=True)


def test_optimize_legs_empty_for_empty_input():
    chain = generate_option_chain("NIFTY", seed=15)
    instrument = get_instrument("NIFTY")
    assert optimize_legs([], chain, instrument) == []
