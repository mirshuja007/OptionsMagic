"""The DSRD framework (Direction / Support / Resistance / Delta) for
weekly-expiry NIFTY/SENSEX credit-spread selling, built on top of
``app.analytics.technicals``.

Terminology, confirmed rather than assumed: "Hedge Put" = a bull put
credit spread (sell an OTM put in the target delta band, buy a further-OTM
put as the hedge leg) for a bullish view; "Hedge Call" = the mirror bear
call credit spread for a bearish view; a "Sideways/Neutral" read runs an
Iron Condor (both sides). This module only computes the *short* strike
(the one the DSRD delta rule actually specifies) — the hedge/long leg's
distance is a risk-sizing choice the source material doesn't define, so it
is deliberately left out here rather than invented (see
``select_sell_strikes``'s docstring).

Every number produced here traces back to a specific indicator reading or
the live option chain — nothing is asserted without a computed source, and
every conviction factor below is shown with the raw reading that produced
it so it can be audited, not taken on faith.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.analytics import technicals as tech
from app.core.black_scholes import OptionType
from app.core.pop import leg_pop_delta_approx
from app.data.models import OptionChain

RSI_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0
ADX_PERIOD = 14
ADX_STRONG_TREND = 25.0
ADX_WEAK_TREND = 20.0

# Lookback windows per the source notes: daily "last 20-30 trading days",
# weekly "last 3-6 months", monthly "last 1-2 years".
DAILY_SR_LOOKBACK = 25
WEEKLY_SR_LOOKBACK = 20
MONTHLY_SR_LOOKBACK = 18
PIVOT_ORDER = 2

DIVERGENCE_LOOKBACK = 40
SR_PROXIMITY_PCT = 0.005  # within 0.5% counts as "at" a support/resistance level

DELTA_LOW, DELTA_HIGH = 0.07, 0.15

Bias = Literal["bullish", "bearish", "neutral"]

# (weekly_zone, daily_zone) -> (condition, strategy) — exactly the five rows
# given; every other combination is genuinely outside the documented system
# and is reported as such rather than guessed at.
STRATEGY_MATRIX: dict[tuple[str, str], tuple[str, str]] = {
    ("high", "high"): ("Strong Bullish", "Hedge Put"),
    ("high", "mid"): ("Bullish Pullback", "Hedge Put or Wait"),
    ("low", "low"): ("Strong Bearish", "Hedge Call"),
    ("low", "mid"): ("Bearish Bounce", "Hedge Call or Wait"),
    ("mid", "mid"): ("Sideways/Neutral", "Iron Condor"),
}
_BIAS_BY_CONDITION: dict[str, Bias] = {
    "Strong Bullish": "bullish",
    "Bullish Pullback": "bullish",
    "Strong Bearish": "bearish",
    "Bearish Bounce": "bearish",
    "Sideways/Neutral": "neutral",
}


def rsi_zone(value: float | None) -> str:
    """DSRD-specific 3-zone bucketing for the Strategy Matrix — distinct
    from the generic 5-band RSI reference (25/45/55/75) used elsewhere;
    this framework's matrix was given with exactly these three bands.
    """
    if value is None:
        return "unknown"
    if value >= 60:
        return "high"
    if value < 40:
        return "low"
    return "mid"


@dataclass(frozen=True)
class DirectionSignal:
    daily_rsi: float | None
    weekly_rsi: float | None
    daily_zone: str
    weekly_zone: str
    condition: str
    strategy: str
    bias: Bias
    in_matrix: bool


def direction_signal(daily_closes: list[float], weekly_closes: list[float]) -> DirectionSignal:
    d_rsi = tech.rsi(daily_closes, RSI_PERIOD)
    w_rsi = tech.rsi(weekly_closes, RSI_PERIOD)
    d_zone = rsi_zone(d_rsi)
    w_zone = rsi_zone(w_rsi)

    hit = STRATEGY_MATRIX.get((w_zone, d_zone))
    if hit is None:
        condition, strategy = "Conflicting (outside documented matrix)", "Wait"
    else:
        condition, strategy = hit

    bias = _BIAS_BY_CONDITION.get(condition, "neutral")
    return DirectionSignal(d_rsi, w_rsi, d_zone, w_zone, condition, strategy, bias, hit is not None)


@dataclass(frozen=True)
class SRLevel:
    support: float
    resistance: float
    target: float | None
    target_kind: Literal["resistance", "support", "none"]
    bars_available: int


def _sr_for_window(bars: list[tech.DailyBar], lookback: int, spot: float) -> tuple[float, float]:
    window = bars[-lookback:] if len(bars) > lookback else bars
    return tech.nearest_support_resistance(window, spot, PIVOT_ORDER)


def multi_timeframe_support_resistance(
    daily_bars: list[tech.DailyBar], spot: float, bias: Bias
) -> dict[str, SRLevel]:
    weekly_bars = tech.resample_weekly(daily_bars)
    monthly_bars = tech.resample_monthly(daily_bars)

    out: dict[str, SRLevel] = {}
    for label, bars, lookback in (
        ("daily", daily_bars, DAILY_SR_LOOKBACK),
        ("weekly", weekly_bars, WEEKLY_SR_LOOKBACK),
        ("monthly", monthly_bars, MONTHLY_SR_LOOKBACK),
    ):
        support, resistance = _sr_for_window(bars, lookback, spot)
        if bias == "bullish":
            target, target_kind = resistance, "resistance"
        elif bias == "bearish":
            target, target_kind = support, "support"
        else:
            target, target_kind = None, "none"
        out[label] = SRLevel(support, resistance, target, target_kind, len(bars))
    return out


@dataclass(frozen=True)
class ConfirmationSignals:
    adx: float | None
    trend_strength: Literal["strong", "weak", "unknown"]
    squeeze: bool
    breakout: Literal["bullish", "bearish", "none"]
    divergence: Literal["bullish", "bearish", "none"]
    candlestick: tuple[str, str] | None
    candlestick_at_sr: bool


def confirmation_signals(daily_bars: list[tech.DailyBar], support: float, resistance: float) -> ConfirmationSignals:
    closes = [b.close for b in daily_bars]
    adx_val = tech.adx(daily_bars, ADX_PERIOD)
    if adx_val is None:
        trend_strength: Literal["strong", "weak", "unknown"] = "unknown"
    else:
        trend_strength = "strong" if adx_val >= ADX_STRONG_TREND else "weak"

    squeeze = tech.bollinger_squeeze(closes, BOLLINGER_PERIOD, BOLLINGER_STD)
    bands = tech.bollinger_bands(closes, BOLLINGER_PERIOD, BOLLINGER_STD)
    latest_band = bands[-1] if bands else None
    breakout: Literal["bullish", "bearish", "none"] = "none"
    if latest_band is not None:
        _, upper, lower = latest_band
        if closes[-1] > upper:
            breakout = "bullish"
        elif closes[-1] < lower:
            breakout = "bearish"

    rsi_vals = tech.rsi_series(closes, RSI_PERIOD)
    divergence = tech.detect_rsi_divergence(daily_bars, rsi_vals, DIVERGENCE_LOOKBACK, PIVOT_ORDER)

    pattern = tech.detect_candlestick_pattern(daily_bars)
    at_sr = False
    if pattern is not None:
        last_close = closes[-1]
        near_support = support > 0 and abs(last_close - support) / support <= SR_PROXIMITY_PCT
        near_resistance = resistance > 0 and abs(last_close - resistance) / resistance <= SR_PROXIMITY_PCT
        _, direction = pattern
        at_sr = (direction == "bullish" and near_support) or (direction == "bearish" and near_resistance)

    return ConfirmationSignals(adx_val, trend_strength, squeeze, breakout, divergence or "none", pattern, at_sr)


@dataclass(frozen=True)
class ConvictionFactor:
    name: str
    reading: str
    contribution: int
    note: str


@dataclass(frozen=True)
class Conviction:
    tier: Literal["High", "Moderate", "Low", "Avoid/Wait"]
    score: int
    factors: list[ConvictionFactor] = field(default_factory=list)


def _tier_for_score(score: int) -> Literal["High", "Moderate", "Low", "Avoid/Wait"]:
    if score >= 4:
        return "High"
    if score >= 2:
        return "Moderate"
    if score >= 0:
        return "Low"
    return "Avoid/Wait"


def score_conviction(direction: DirectionSignal, signals: ConfirmationSignals) -> Conviction:
    """A documented, auditable point system combining the DSRD components
    into one tier. This is a heuristic combination the way the source
    framework itself is a heuristic combination — it is not a statistically
    validated model, and every factor is reported with its raw reading so
    the score can be checked, not just trusted.
    """
    bias = direction.bias
    strategy_label = direction.strategy
    factors: list[ConvictionFactor] = []

    if direction.in_matrix and bias != "neutral":
        factors.append(
            ConvictionFactor(
                "Direction (DSRD matrix)", direction.condition, 2,
                f"Weekly RSI {direction.weekly_rsi and round(direction.weekly_rsi, 1)} ({direction.weekly_zone}), "
                f"Daily RSI {direction.daily_rsi and round(direction.daily_rsi, 1)} ({direction.daily_zone}) -> {strategy_label}",
            )
        )
    elif direction.in_matrix:
        factors.append(
            ConvictionFactor("Direction (DSRD matrix)", direction.condition, 1, f"Range-bound reading -> {strategy_label}")
        )
    else:
        factors.append(
            ConvictionFactor(
                "Direction (DSRD matrix)", direction.condition, -1,
                "Weekly and Daily RSI zones disagree beyond the 5 documented rows — treat as low-confidence",
            )
        )

    if signals.trend_strength == "unknown":
        factors.append(ConvictionFactor("ADX(14) trend strength", "n/a", 0, "Not enough history yet"))
    else:
        adx_reading = f"{signals.adx:.1f}"
        if bias == "neutral":
            if signals.trend_strength == "weak":
                factors.append(ConvictionFactor("ADX(14) trend strength", adx_reading, 1, "No strong trend — supports the Iron Condor's range thesis"))
            else:
                factors.append(ConvictionFactor("ADX(14) trend strength", adx_reading, -1, "A trend is developing — breakout risk for the Iron Condor"))
        else:
            if signals.trend_strength == "strong":
                factors.append(ConvictionFactor("ADX(14) trend strength", adx_reading, 1, f"Trend confirmed (>={ADX_STRONG_TREND:.0f}) — supports the {strategy_label} continuing"))
            else:
                factors.append(ConvictionFactor("ADX(14) trend strength", adx_reading, -1, f"Weak/no trend (<{ADX_WEAK_TREND:.0f}) — less conviction the move continues"))

    if signals.breakout == "none":
        note = "Bands compressed, no breakout yet — a move is coming, direction unconfirmed" if signals.squeeze else "No compression or breakout signal currently"
        factors.append(ConvictionFactor("Bollinger squeeze", "Squeezed, no breakout" if signals.squeeze else "No squeeze", 0, note))
    elif bias == "neutral":
        factors.append(ConvictionFactor("Bollinger squeeze", f"Breakout {signals.breakout}", -2, "A breakout out of the range threatens the Iron Condor"))
    elif signals.breakout == bias:
        factors.append(ConvictionFactor("Bollinger squeeze", f"Breakout {signals.breakout}", 1, f"Breakout direction agrees with the {strategy_label} view"))
    else:
        factors.append(ConvictionFactor("Bollinger squeeze", f"Breakout {signals.breakout}", -2, f"Breakout direction conflicts with the {strategy_label} view — the early-exit signal"))

    if signals.divergence == "none":
        factors.append(ConvictionFactor("RSI divergence", "None detected", 0, "No leading reversal signal currently"))
    elif bias == "neutral":
        factors.append(ConvictionFactor("RSI divergence", f"{signals.divergence.capitalize()} divergence", -1, "A reversal setup is forming — risk to the Iron Condor's range"))
    elif signals.divergence == bias:
        factors.append(ConvictionFactor("RSI divergence", f"{signals.divergence.capitalize()} divergence", 1, f"Momentum confirms the {strategy_label} view"))
    else:
        factors.append(ConvictionFactor("RSI divergence", f"{signals.divergence.capitalize()} divergence", -2, f"Momentum turning against the {strategy_label} view"))

    if signals.candlestick is None or not signals.candlestick_at_sr:
        note = "No pattern at an identified level" if signals.candlestick is None else "Pattern printed away from support/resistance — noise, ignored per DSRD rule"
        factors.append(ConvictionFactor("Candlestick @ S/R", signals.candlestick[0] if signals.candlestick else "None", 0, note))
    else:
        pattern_name, pattern_dir = signals.candlestick
        if bias == "neutral":
            factors.append(ConvictionFactor("Candlestick @ S/R", pattern_name, 1, "Rejection candle at the level supports staying inside the range"))
        elif pattern_dir == bias:
            factors.append(ConvictionFactor("Candlestick @ S/R", pattern_name, 1, f"Confirms the {strategy_label} view"))
        else:
            factors.append(ConvictionFactor("Candlestick @ S/R", pattern_name, -2, f"Warns against the {strategy_label} view"))

    score = sum(f.contribution for f in factors)
    return Conviction(_tier_for_score(score), score, factors)


@dataclass(frozen=True)
class StrikeCandidate:
    strike: float
    tradingsymbol: str
    ltp: float
    delta: float
    probability_of_profit: float
    distance_pct: float
    aligned_with_sr: bool


def select_sell_strikes(
    chain: OptionChain, support: float, resistance: float
) -> dict[str, list[StrikeCandidate]]:
    """Short-strike candidates for a credit spread's sell leg: strikes
    whose |delta| falls in the DSRD 0.07-0.15 band, each flagged for
    whether it also sits beyond the relevant S/R level (the DSRD
    checklist's "right side of S/R" test) — sorted with aligned candidates
    first, but misaligned ones are still shown rather than hidden.

    Deliberately not selected here: the credit spread's hedge/long leg.
    The source notes specify the short strike's delta band but never say
    how far out the hedge leg should sit — that's a risk-sizing choice
    (tighter hedge = less premium, less margin/risk; wider hedge = more
    premium, more risk) this module won't invent a number for.
    """
    calls: list[StrikeCandidate] = []
    puts: list[StrikeCandidate] = []
    r = chain.risk_free_rate
    t = chain.time_to_expiry_years

    for row in chain.rows:
        call_delta = row.call.greeks.delta
        if DELTA_LOW <= call_delta <= DELTA_HIGH:
            pop = leg_pop_delta_approx(chain.spot, row.strike, t, r, row.call.iv, OptionType.CALL, is_seller=True)
            calls.append(
                StrikeCandidate(
                    strike=row.strike,
                    tradingsymbol=row.call.tradingsymbol,
                    ltp=row.call.ltp,
                    delta=call_delta,
                    probability_of_profit=pop,
                    distance_pct=(row.strike - chain.spot) / chain.spot * 100.0,
                    aligned_with_sr=row.strike >= resistance,
                )
            )
        put_delta = row.put.greeks.delta
        if DELTA_LOW <= abs(put_delta) <= DELTA_HIGH:
            pop = leg_pop_delta_approx(chain.spot, row.strike, t, r, row.put.iv, OptionType.PUT, is_seller=True)
            puts.append(
                StrikeCandidate(
                    strike=row.strike,
                    tradingsymbol=row.put.tradingsymbol,
                    ltp=row.put.ltp,
                    delta=put_delta,
                    probability_of_profit=pop,
                    distance_pct=(chain.spot - row.strike) / chain.spot * 100.0,
                    aligned_with_sr=row.strike <= support,
                )
            )

    calls.sort(key=lambda c: (not c.aligned_with_sr, c.strike))
    puts.sort(key=lambda p: (not p.aligned_with_sr, -p.strike))
    return {"calls": calls, "puts": puts}


@dataclass(frozen=True)
class ChecklistItem:
    step: str
    question: str
    reading: str
    passed: bool


def build_checklist(
    direction: DirectionSignal,
    sr: dict[str, SRLevel],
    strikes: dict[str, list[StrikeCandidate]],
) -> list[ChecklistItem]:
    direction_pass = direction.in_matrix
    sr_pass = all(level.bars_available > 0 for level in sr.values())

    if direction.bias == "bullish":
        relevant_side, other_ok = strikes["puts"], True
    elif direction.bias == "bearish":
        relevant_side, other_ok = strikes["calls"], True
    else:
        relevant_side, other_ok = strikes["puts"], any(c.aligned_with_sr for c in strikes["calls"])
    delta_pass = any(c.aligned_with_sr for c in relevant_side) and other_ok

    return [
        ChecklistItem("Direction", "Weekly + daily RSI zone?", direction.condition, direction_pass),
        ChecklistItem(
            "Support", "Where's multi-timeframe support?",
            ", ".join(f"{tf}: {lvl.support:.0f}" for tf, lvl in sr.items()), sr_pass,
        ),
        ChecklistItem(
            "Resistance", "Where's multi-timeframe resistance?",
            ", ".join(f"{tf}: {lvl.resistance:.0f}" for tf, lvl in sr.items()), sr_pass,
        ),
        ChecklistItem(
            "Delta", "Is the 0.07-0.15 strike on the right side of S/R?",
            "Aligned candidate found" if delta_pass else "No aligned candidate in the delta band", delta_pass,
        ),
    ]
