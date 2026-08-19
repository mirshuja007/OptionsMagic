from app.data.instruments import get_instrument
from app.data.mock_feed import generate_option_chain
from app.strategy.generator import ALL_STRATEGY_TYPES, generate_all_candidates
from app.strategy.solver import (
    ResearchContext,
    StrategyConstraints,
    _apply_composite_scores,
    _direction_lean,
    _target_direction,
    _technical_alignment,
    build_research_context,
    discover_strategies,
)


def test_direction_lean_mapping():
    assert _direction_lean("bull_put_spread") == "bullish"
    assert _direction_lean("ratio_spread_put") == "bullish"
    assert _direction_lean("bear_call_spread") == "bearish"
    assert _direction_lean("ratio_spread_call") == "bearish"
    assert _direction_lean("iron_condor") == "neutral"
    assert _direction_lean("iron_fly") == "neutral"


def _ctx(smart_oi_bias="neutral", vwap_direction="neutral", support=24700.0, resistance=24900.0, max_pain=24800.0):
    return ResearchContext(
        support_strike=support,
        resistance_strike=resistance,
        max_pain_strike=max_pain,
        smart_oi_bias=smart_oi_bias,
        vwap_direction=vwap_direction,
    )


def test_target_direction_explicit_bias_wins_over_signals():
    ctx = _ctx(smart_oi_bias="bullish", vwap_direction="bullish")
    assert _target_direction(ctx, "bearish") == "bearish"
    assert _target_direction(ctx, "neutral") == "neutral"


def test_target_direction_auto_agrees_when_signals_agree():
    ctx = _ctx(smart_oi_bias="bullish", vwap_direction="bullish")
    assert _target_direction(ctx, "auto") == "bullish"


def test_target_direction_auto_is_neutral_when_signals_disagree():
    ctx = _ctx(smart_oi_bias="bullish", vwap_direction="bearish")
    assert _target_direction(ctx, "auto") == "neutral"


def test_target_direction_auto_is_neutral_when_no_signal_available():
    ctx = _ctx(smart_oi_bias="neutral", vwap_direction="neutral")
    assert _target_direction(ctx, "auto") == "neutral"


def test_build_research_context_from_real_chain():
    chain = generate_option_chain("NIFTY", seed=40)
    ctx = build_research_context(chain, vwap=None)
    strikes = {row.strike for row in chain.rows}
    assert ctx.support_strike in strikes
    assert ctx.resistance_strike in strikes
    assert ctx.max_pain_strike in strikes
    assert ctx.smart_oi_bias in {"bullish", "bearish", "neutral"}
    assert ctx.vwap_direction == "neutral"  # no vwap passed


def test_build_research_context_vwap_direction_reflects_spot_position():
    chain = generate_option_chain("NIFTY", seed=41)
    above = build_research_context(chain, vwap=chain.spot - 50)
    below = build_research_context(chain, vwap=chain.spot + 50)
    assert above.vwap_direction == "bullish"
    assert below.vwap_direction == "bearish"


def test_technical_alignment_is_zero_without_context():
    chain = generate_option_chain("NIFTY", seed=42)
    candidates = generate_all_candidates(chain)
    cand = candidates[0]
    assert _technical_alignment(cand.strategy_type, cand.legs, None, "auto", chain.spot) == 0.0


def test_technical_alignment_bounded_for_real_candidates():
    chain = generate_option_chain("NIFTY", seed=43)
    ctx = build_research_context(chain, vwap=chain.spot - 20)
    candidates = generate_all_candidates(chain)
    for cand in candidates[:50]:
        score = _technical_alignment(cand.strategy_type, cand.legs, ctx, "auto", chain.spot)
        assert 0.0 <= score <= 1.0


def test_strategy_types_filter_restricts_generated_candidates():
    chain = generate_option_chain("NIFTY", seed=44)
    only_condors = generate_all_candidates(chain, strategy_types={"iron_condor"})
    assert len(only_condors) > 0
    assert {c.strategy_type for c in only_condors} == {"iron_condor"}

    everything = generate_all_candidates(chain)
    assert set(ALL_STRATEGY_TYPES) >= {c.strategy_type for c in everything}
    assert len(everything) > len(only_condors)


def test_discover_strategies_respects_strategy_types_constraint():
    chain = generate_option_chain("NIFTY", seed=45)
    instrument = get_instrument("NIFTY")
    constraints = StrategyConstraints(
        min_pop=0.5,
        min_yield_pct=0.0,
        max_profit_cap=50000,
        max_loss_cap=30000,
        margin_cap=1_000_000,
        n_paths_screen=1500,
        n_paths_final=4000,
        strategy_types=frozenset({"bull_put_spread"}),
    )
    results = discover_strategies(chain, instrument, constraints, top_n=5)
    assert results  # NIFTY at these caps should find at least one
    assert all(r.strategy_type == "bull_put_spread" for r in results)


def test_use_research_signals_false_zeroes_alignment_even_with_context():
    chain = generate_option_chain("NIFTY", seed=46)
    instrument = get_instrument("NIFTY")
    ctx = build_research_context(chain, vwap=chain.spot - 30)
    constraints = StrategyConstraints(
        min_pop=0.5,
        min_yield_pct=0.0,
        max_profit_cap=50000,
        max_loss_cap=30000,
        margin_cap=1_000_000,
        n_paths_screen=1500,
        n_paths_final=4000,
        use_research_signals=False,
    )
    results = discover_strategies(chain, instrument, constraints, top_n=5, research_ctx=ctx)
    assert results
    assert all(r.technical_alignment == 0.0 for r in results)


def test_ranking_mode_changes_relative_order():
    # Construct two synthetic results where one clearly wins on yield and the
    # other clearly wins on PoP/Sharpe, then check that "yield" mode and
    # "safety" mode actually disagree on which ranks first.
    from app.strategy.legs import Leg, PayoffExtrema, Side
    from app.core.black_scholes import OptionType
    from app.margin.span import MarginEstimate
    from app.strategy.solver import StrategyResult

    leg = Leg(option_type=OptionType.PUT, strike=100.0, side=Side.SHORT, quantity_lots=1, entry_price=1.0, iv=0.15)
    margin = MarginEstimate(span_margin=1000, exposure_margin=100, total_margin=1100, net_entry_credit=50)
    payoff = PayoffExtrema(max_profit=100, max_loss=-500, unlimited_upside_risk=False, unlimited_downside_risk=False)

    high_yield_low_safety = StrategyResult(
        strategy_type="bull_put_spread", legs=[leg], margin=margin, payoff=payoff,
        probability_of_profit=0.55, expected_value=10.0, sharpe=0.05, yield_pct=0.30,
    )
    low_yield_high_safety = StrategyResult(
        strategy_type="bull_put_spread", legs=[leg], margin=margin, payoff=payoff,
        probability_of_profit=0.95, expected_value=8.0, sharpe=0.40, yield_pct=0.02,
    )

    yield_ranked = _apply_composite_scores(
        [high_yield_low_safety, low_yield_high_safety], "yield", None, "auto", spot=100.0
    )
    safety_ranked = _apply_composite_scores(
        [high_yield_low_safety, low_yield_high_safety], "safety", None, "auto", spot=100.0
    )

    yield_ranked.sort(key=lambda r: r.composite_score, reverse=True)
    safety_ranked.sort(key=lambda r: r.composite_score, reverse=True)

    assert yield_ranked[0].yield_pct == 0.30
    assert safety_ranked[0].yield_pct == 0.02
