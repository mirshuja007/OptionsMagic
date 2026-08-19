"""Constraint-Solving Strategy Discovery Engine.

Sweeps every combinatorially generated candidate (credit spreads, iron
condors, iron flies, ratio spreads) against the user's risk/capital/
probability boundaries, scores the survivors, and returns the top-ranked
ones.

Screening runs Monte-Carlo PoP/EV at a cheap path count across every
candidate (numpy-vectorized, so a few hundred candidates x a few thousand
paths is sub-second); the surviving top candidates are then re-scored at a
much higher path count for reporting accuracy.

Ranking is a user-tunable blend of yield-on-margin, probability of profit,
and Sharpe (EV per unit of P&L variance) — see ``RANKING_WEIGHTS`` — with an
optional small nudge from Research Mode's read of the chain (OI-based
support/resistance, Max Pain, Smart OI bias, VWAP position; see
``ResearchContext`` and ``build_research_context``). The nudge is
deliberately soft: it can move two close candidates past each other, capped
at +/-``SIGNAL_BONUS_MAX`` of the base score, but it never overrides the
hard constraint filters (PoP floor, profit/loss caps, margin cap) and never
promotes a candidate the pure math ranked far behind.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, replace

from app.analytics import commentary as commentary_mod
from app.analytics import max_pain as max_pain_mod
from app.analytics import oi as oi_mod
from app.core.black_scholes import OptionType
from app.core.pop import strategy_pop_monte_carlo
from app.data.instruments import Instrument
from app.data.mock_feed import OptionChain
from app.margin.span import MarginEstimate, estimate_margin
from app.strategy.generator import Candidate, generate_all_candidates
from app.strategy.legs import Leg, PayoffExtrema, Side, payoff_at_expiry, payoff_extrema

#: (yield_weight, pop_weight, sharpe_weight) — each triple sums to ~1.0.
#: "yield" chases raw return on margin, "safety" leans hard on PoP + Sharpe
#: (risk-adjusted quality) at the expense of magnitude, "balanced" splits
#: the difference. This is the user-facing "ranking mode" knob.
RANKING_WEIGHTS: dict[str, tuple[float, float, float]] = {
    "yield": (0.6, 0.2, 0.2),
    "balanced": (0.34, 0.33, 0.33),
    "safety": (0.2, 0.5, 0.3),
}

#: Maximum fractional boost/penalty the Research-signal alignment nudge can
#: apply to a candidate's composite score (e.g. 0.15 = +/-15%). Deliberately
#: small — see module docstring: a tiebreaker, not a second hard filter.
SIGNAL_BONUS_MAX = 0.15

#: How far beyond a support/resistance wall (as a fraction of spot) counts
#: as "fully" cushioned for the strike-safety-margin component of the
#: alignment score. Strikes beyond this get the max bonus; nothing extra
#: past it.
_CUSHION_CAP_PCT = 0.02


@dataclass(frozen=True)
class StrategyConstraints:
    min_pop: float  # e.g. 0.80 for 80%
    min_yield_pct: float  # e.g. 0.01 for 1% of margin blocked
    max_profit_cap: float  # rupee ceiling
    max_loss_cap: float  # rupee cap, positive number
    margin_cap: float  # rupee ceiling on margin blocked
    n_paths_screen: int = 4000
    n_paths_final: int = 50_000
    ranking_mode: str = "balanced"  # "yield" | "balanced" | "safety" — see RANKING_WEIGHTS
    strategy_types: frozenset[str] | None = None  # None = every type in ALL_STRATEGY_TYPES
    use_research_signals: bool = True  # apply the Research Mode alignment nudge, see module docstring
    direction_bias: str = "auto"  # "auto" | "bullish" | "bearish" | "neutral" — see _target_direction


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
    technical_alignment: float = 0.0  # 0..1, how well this candidate reads with Research Mode's signals
    composite_score: float = 0.0  # the actual ranking key — see _apply_composite_scores


@dataclass(frozen=True)
class ResearchContext:
    """A snapshot of Research Mode's read on the chain, passed into the
    solver purely to bias ranking (see module docstring) — never to filter.
    Build via ``build_research_context``.
    """

    support_strike: float
    resistance_strike: float
    max_pain_strike: float
    smart_oi_bias: str  # "bullish" | "bearish" | "neutral"
    vwap_direction: str  # "bullish" | "bearish" | "neutral" — spot vs. session VWAP


def build_research_context(chain: OptionChain, vwap: float | None = None) -> ResearchContext:
    """Derive a ResearchContext straight from the chain (and, if available,
    the session VWAP) using the same analytics Research Mode's commentary
    box already computes — no separate/duplicated logic.
    """
    sr = commentary_mod.support_resistance(chain)
    mp = max_pain_mod.compute_max_pain(chain)
    smart = oi_mod.smart_oi_score(chain)

    vwap_direction = "neutral"
    if vwap is not None:
        diff = chain.spot - vwap
        vwap_direction = "bullish" if diff > 0.01 else "bearish" if diff < -0.01 else "neutral"

    return ResearchContext(
        support_strike=sr.support_strike,
        resistance_strike=sr.resistance_strike,
        max_pain_strike=mp.max_pain_strike,
        smart_oi_bias=smart["bias"],
        vwap_direction=vwap_direction,
    )


def _direction_lean(strategy_type: str) -> str:
    """Which side of the market a strategy shape implicitly bets on, from
    which side carries the extra naked-short exposure: net short puts hurts
    on a fall (so the trade wants the market to hold up = bullish lean); net
    short calls hurts on a rally (bearish lean). Iron condors/flies are
    symmetric (both sides shorted equally) — neutral. This is a simplifying
    heuristic for the ranking nudge only, not a claim about a strategy's
    full risk profile.
    """
    if strategy_type in ("bull_put_spread", "ratio_spread_put"):
        return "bullish"
    if strategy_type in ("bear_call_spread", "ratio_spread_call"):
        return "bearish"
    return "neutral"


def _target_direction(ctx: ResearchContext, direction_bias: str) -> str:
    """The market direction to reward alignment with. An explicit
    ``direction_bias`` (from the user) always wins; "auto" derives it from
    Smart OI bias + VWAP position, and only commits to a direction when both
    available signals agree — if they disagree (or neither has a read),
    "neutral" is returned and no directional strategy gets the alignment
    bonus, rather than guessing.
    """
    if direction_bias != "auto":
        return direction_bias
    votes = [v for v in (ctx.smart_oi_bias, ctx.vwap_direction) if v in ("bullish", "bearish")]
    if len(set(votes)) == 1:
        return votes[0]
    return "neutral"


def _short_leg_strike(legs: list[Leg], option_type: OptionType) -> float | None:
    shorts = [leg.strike for leg in legs if leg.option_type == option_type and leg.side == Side.SHORT]
    return shorts[0] if shorts else None


def _strike_safety_fraction(strategy_type: str, legs: list[Leg], ctx: ResearchContext, spot: float) -> float:
    """0..1: how much cushion this candidate's risk strike(s) have against
    Research Mode's read of the chain. Directional strategies are scored by
    distance of their short strike beyond the OI-based support/resistance
    wall (capped at ``_CUSHION_CAP_PCT`` of spot for full credit); neutral
    strategies (iron condor/fly) are scored by how centered their short
    strikes are around Max Pain.
    """
    lean = _direction_lean(strategy_type)
    if lean == "bullish":
        short_strike = _short_leg_strike(legs, OptionType.PUT)
        if short_strike is None:
            return 0.0
        distance_pct = (ctx.support_strike - short_strike) / spot
        return max(min(distance_pct / _CUSHION_CAP_PCT, 1.0), 0.0)
    if lean == "bearish":
        short_strike = _short_leg_strike(legs, OptionType.CALL)
        if short_strike is None:
            return 0.0
        distance_pct = (short_strike - ctx.resistance_strike) / spot
        return max(min(distance_pct / _CUSHION_CAP_PCT, 1.0), 0.0)

    short_put = _short_leg_strike(legs, OptionType.PUT)
    short_call = _short_leg_strike(legs, OptionType.CALL)
    if short_put is None or short_call is None or short_put >= short_call:
        return 0.0
    if not (short_put <= ctx.max_pain_strike <= short_call):
        return 0.0
    half_width = (short_call - short_put) / 2.0
    if half_width <= 0:
        return 0.0
    mid = (short_put + short_call) / 2.0
    return max(1.0 - abs(ctx.max_pain_strike - mid) / half_width, 0.0)


def _technical_alignment(
    strategy_type: str, legs: list[Leg], ctx: ResearchContext | None, direction_bias: str, spot: float
) -> float:
    """0..1 composite alignment score: 70% strike-safety-margin (see
    ``_strike_safety_fraction``), 30% whether the candidate's directional
    lean matches ``_target_direction`` (neutral strategies get no
    directional component either way — they're scored entirely on Max Pain
    centering).
    """
    if ctx is None:
        return 0.0
    safety = _strike_safety_fraction(strategy_type, legs, ctx, spot)
    lean = _direction_lean(strategy_type)
    direction_bonus = 0.0
    if lean != "neutral":
        direction_bonus = 1.0 if lean == _target_direction(ctx, direction_bias) else 0.0
    return 0.7 * safety + 0.3 * direction_bonus


def _normalize(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5 for _ in values]  # no discriminating spread in this batch -> neutral midpoint for all
    return [(v - lo) / (hi - lo) for v in values]


def _apply_composite_scores(
    results: list[StrategyResult],
    ranking_mode: str,
    research_ctx: ResearchContext | None,
    direction_bias: str,
    spot: float,
) -> list[StrategyResult]:
    """Recomputes technical_alignment and composite_score for a batch,
    normalizing yield/PoP/Sharpe against each other *within this batch*
    (min-max), so the three metrics — on very different natural scales —
    contribute comparably to the blend regardless of ranking_mode.
    """
    if not results:
        return results
    w_yield, w_pop, w_sharpe = RANKING_WEIGHTS.get(ranking_mode, RANKING_WEIGHTS["balanced"])
    norm_yield = _normalize([r.yield_pct for r in results])
    norm_pop = _normalize([r.probability_of_profit for r in results])
    norm_sharpe = _normalize([r.sharpe for r in results])

    out = []
    for r, ny, npp, ns in zip(results, norm_yield, norm_pop, norm_sharpe):
        alignment = _technical_alignment(r.strategy_type, r.legs, research_ctx, direction_bias, spot)
        base = w_yield * ny + w_pop * npp + w_sharpe * ns
        composite = base * (1 + SIGNAL_BONUS_MAX * alignment)
        out.append(replace(r, technical_alignment=round(alignment, 4), composite_score=round(composite, 6)))
    return out


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
    research_ctx: ResearchContext | None = None,
) -> list[StrategyResult]:
    strategy_types = set(constraints.strategy_types) if constraints.strategy_types else None
    candidates = generate_all_candidates(chain, strategy_types=strategy_types)
    effective_ctx = research_ctx if constraints.use_research_signals else None

    screened: list[StrategyResult] = []
    for candidate in candidates:
        result = _score_candidate(candidate, chain, instrument, constraints, constraints.n_paths_screen)
        if result is not None:
            screened.append(result)

    screened = _apply_composite_scores(screened, constraints.ranking_mode, effective_ctx, constraints.direction_bias, chain.spot)
    screened.sort(key=lambda r: r.composite_score, reverse=True)
    finalists = screened[:top_n]

    refined: list[StrategyResult] = []
    for result in finalists:
        candidate = Candidate(result.strategy_type, result.legs)
        rescored = _score_candidate(candidate, chain, instrument, constraints, constraints.n_paths_final)
        refined.append(rescored if rescored is not None else result)

    refined = _apply_composite_scores(refined, constraints.ranking_mode, effective_ctx, constraints.direction_bias, chain.spot)
    refined.sort(key=lambda r: r.composite_score, reverse=True)
    return refined
