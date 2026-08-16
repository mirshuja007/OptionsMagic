"""Max Pain: the strike at which option writers (sellers) collectively lose
the least, i.e. where total intrinsic-value payout to option buyers is
minimized at expiry.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.data.mock_feed import OptionChain


@dataclass(frozen=True)
class MaxPainResult:
    max_pain_strike: float
    payout_by_strike: dict[float, float]


def compute_max_pain(chain: OptionChain) -> MaxPainResult:
    strikes = [row.strike for row in chain.rows]
    payout_by_strike: dict[float, float] = {}

    for candidate_expiry_price in strikes:
        total_payout = 0.0
        for row in chain.rows:
            call_itm_value = max(candidate_expiry_price - row.strike, 0.0)
            put_itm_value = max(row.strike - candidate_expiry_price, 0.0)
            total_payout += call_itm_value * row.call.oi
            total_payout += put_itm_value * row.put.oi
        payout_by_strike[candidate_expiry_price] = total_payout

    max_pain_strike = min(payout_by_strike, key=payout_by_strike.get)
    return MaxPainResult(max_pain_strike=max_pain_strike, payout_by_strike=payout_by_strike)
