"""Open Interest analytics: per-strike aggregation, buildup classification,
a "Smart OI" institutional-flow proxy, and dealer Gamma Exposure (GEX).

Note on Smart OI: a true FII/DII participant-wise breakdown requires NSE's
separate participant-wise open-interest bulletin (not part of the option
chain itself). Until that feed is wired in, ``smart_oi_score`` is a
documented heuristic — it weights net OI change by proximity to the ATM
strike (large institutional writers concentrate near the money, while
far-OTM buildup skews retail) and combines it with the OI-weighted PCR
trend. Treat it as a directional proxy, not a literal FII/DII number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from app.data.instruments import get_instrument
from app.data.mock_feed import ChainRow, OptionChain


class Buildup(str, Enum):
    LONG_BUILDUP = "long_buildup"
    SHORT_BUILDUP = "short_buildup"
    SHORT_COVERING = "short_covering"
    LONG_UNWINDING = "long_unwinding"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class StrikeOi:
    strike: float
    call_oi: int
    put_oi: int
    call_oi_change: int
    put_oi_change: int
    call_buildup: Buildup
    put_buildup: Buildup


def _classify(oi_change: int, price_change_pct: float, threshold: float = 0.0015) -> Buildup:
    if abs(price_change_pct) < threshold or oi_change == 0:
        return Buildup.NEUTRAL
    price_up = price_change_pct > 0
    oi_up = oi_change > 0
    if price_up and oi_up:
        return Buildup.LONG_BUILDUP
    if price_up and not oi_up:
        return Buildup.SHORT_COVERING
    if not price_up and oi_up:
        return Buildup.SHORT_BUILDUP
    return Buildup.LONG_UNWINDING


def multistrike_oi(chain: OptionChain, spot_change_pct: float = 0.0) -> list[StrikeOi]:
    """Per-strike OI with buildup classification. ``spot_change_pct`` is the
    underlying's session change (e.g. vs. today's open); call premiums move
    with the underlying, put premiums move inversely, so the put buildup
    classification uses the sign-flipped change.
    """
    out: list[StrikeOi] = []
    for row in chain.rows:
        out.append(
            StrikeOi(
                strike=row.strike,
                call_oi=row.call.oi,
                put_oi=row.put.oi,
                call_oi_change=row.call.oi_change,
                put_oi_change=row.put.oi_change,
                call_buildup=_classify(row.call.oi_change, spot_change_pct),
                put_buildup=_classify(row.put.oi_change, -spot_change_pct),
            )
        )
    return out


def smart_oi_score(chain: OptionChain) -> dict:
    """Institutional-flow proxy in [-1, 1]: positive leans bullish (net call
    writing/put buying unwinding near ATM), negative leans bearish.
    """
    atm_strike = min(chain.rows, key=lambda r: abs(r.strike - chain.spot)).strike
    step = abs(chain.rows[1].strike - chain.rows[0].strike) if len(chain.rows) > 1 else 1.0

    weighted_call = 0.0
    weighted_put = 0.0
    weight_total = 0.0
    for row in chain.rows:
        distance = abs(row.strike - atm_strike) / step
        weight = math.exp(-0.2 * distance)  # institutional writing concentrates near ATM
        weighted_call += weight * row.call.oi_change
        weighted_put += weight * row.put.oi_change
        weight_total += weight

    if weight_total == 0:
        return {"score": 0.0, "bias": "neutral"}

    net = (weighted_put - weighted_call) / weight_total
    denom = sum(abs(r.call.oi_change) + abs(r.put.oi_change) for r in chain.rows) or 1
    score = max(min(net / denom * len(chain.rows), 1.0), -1.0)

    bias = "bullish" if score > 0.1 else "bearish" if score < -0.1 else "neutral"
    return {"score": round(score, 4), "bias": bias}


def gamma_exposure(chain: OptionChain) -> dict:
    """Dealer Gamma Exposure (GEX), convention: dealers assumed net short
    calls / net long puts against customer flow, so positive GEX from calls
    means dealers buy the dip / sell the rip (dampens moves), and put GEX
    contributes with the opposite sign. Units: rupees of underlying delta
    hedge notional per 1% move in spot.
    """
    instrument = get_instrument(chain.symbol)
    spot = chain.spot
    total_call_gex = 0.0
    total_put_gex = 0.0
    by_strike: dict[float, float] = {}

    for row in chain.rows:
        call_gex = row.call.greeks.gamma * row.call.oi * instrument.lot_size * spot * spot * 0.01
        put_gex = row.put.greeks.gamma * row.put.oi * instrument.lot_size * spot * spot * 0.01
        total_call_gex += call_gex
        total_put_gex -= put_gex
        by_strike[row.strike] = call_gex - put_gex

    net_gex = total_call_gex + total_put_gex
    return {
        "net_gex": net_gex,
        "call_gex": total_call_gex,
        "put_gex": total_put_gex,
        "regime": "long_gamma" if net_gex > 0 else "short_gamma",
        "by_strike": by_strike,
    }
