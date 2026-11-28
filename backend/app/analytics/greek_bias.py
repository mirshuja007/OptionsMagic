"""Greek-based "does this favor selling or buying premium right now"
advisory — requested as help designing the judgment call, not a handed-
down formula. This combines two standard options-desk rules of thumb:

  1. IV regime (``app.analytics.volatility.classify_iv_regime``): is
     implied vol itself rich or cheap versus what's actually realizing.
     Rich favors selling, cheap favors buying — the same read already
     feeding ``app.strategy.solver``'s ranking.
  2. Gamma-vs-theta: how many days' worth of theta income a single
     ``move_pct`` move in the underlying would erase via gamma convexity
     alone (the second-order Taylor term, ``0.5 * gamma * move^2 *
     lot_size``, divided by the day's theta). A high ratio means you're
     not being paid enough theta for the gamma risk you're carrying —
     the classic reason a theoretically-positive-theta short position
     still loses money on a sharp move.

There is no single settled industry threshold for "how much gamma risk
is too much theta-adjusted" — real desks disagree, same as they disagree
on exact IV-rich/cheap cutoffs. ``GAMMA_RISK_HIGH``/``GAMMA_RISK_LOW``
below are an illustrative starting framework, clearly labeled as such,
not a calibrated signal to trade blind.
"""
from __future__ import annotations

from dataclasses import dataclass

GAMMA_RISK_HIGH = 1.0  # a 1% move would cost more than a full day's theta via gamma alone
GAMMA_RISK_LOW = 0.3  # theta comfortably outweighs the gamma convexity risk

Bias = str  # "sell_premium" | "buy_premium" | "neutral"


def gamma_theta_risk_ratio(gamma: float, theta: float, spot: float, lot_size: int, move_pct: float = 1.0) -> float:
    """How many days' worth of theta a single ``move_pct``% underlying
    move would cost via gamma convexity alone, for one lot. Returns
    ``inf`` when theta is zero/negligible (can't be compensating for
    anything), rather than dividing by zero.
    """
    daily_theta = abs(theta)
    if daily_theta <= 0:
        return float("inf")
    move = spot * move_pct / 100.0
    gamma_cost = 0.5 * abs(gamma) * move * move * lot_size
    return gamma_cost / daily_theta


@dataclass(frozen=True)
class GreekBiasResult:
    bias: Bias
    score: int  # positive leans toward selling premium, negative toward buying, 0 is neutral
    gamma_theta_ratio: float
    reasons: list[str]


def greek_bias(
    gamma: float, theta: float, spot: float, lot_size: int, iv_regime: str, move_pct: float = 1.0,
) -> GreekBiasResult:
    """Combine the IV-regime read and the gamma/theta risk ratio into one
    plain-language bias. ``iv_regime`` is whatever
    ``app.analytics.volatility.classify_iv_regime`` (or
    ``conditions.IvRegimeCondition``) already computed — this function
    doesn't recompute it, so the two stay consistent with each other.
    """
    ratio = gamma_theta_risk_ratio(gamma, theta, spot, lot_size, move_pct)
    score = 0
    reasons: list[str] = []

    if iv_regime == "rich":
        score += 1
        reasons.append("IV is rich versus realized vol — favors selling premium")
    elif iv_regime == "cheap":
        score -= 1
        reasons.append("IV is cheap versus realized vol — favors buying premium")
    else:
        reasons.append("IV is roughly in line with realized vol — no edge either way from this alone")

    if ratio >= GAMMA_RISK_HIGH:
        score -= 1
        reasons.append(
            f"a {move_pct:.1f}% move would cost about {ratio:.1f}x today's theta via gamma alone — "
            "more convexity risk than the theta collected compensates for; caution on selling"
        )
    elif ratio <= GAMMA_RISK_LOW:
        score += 1
        reasons.append(
            f"a {move_pct:.1f}% move would cost only about {ratio:.1f}x today's theta via gamma — "
            "theta comfortably outweighs the convexity risk, favors selling"
        )
    else:
        reasons.append(f"gamma/theta ratio ({ratio:.1f}x) is in between — no strong lean from this alone")

    bias = "sell_premium" if score > 0 else "buy_premium" if score < 0 else "neutral"
    return GreekBiasResult(bias=bias, score=score, gamma_theta_ratio=round(ratio, 3), reasons=reasons)
