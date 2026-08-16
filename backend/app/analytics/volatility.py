"""Implied volatility grid, IV-vs-historical-volatility, and volatility skew."""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.data.mock_feed import OptionChain


@dataclass(frozen=True)
class IvGridPoint:
    strike: float
    call_iv: float
    put_iv: float


def iv_grid(chain: OptionChain) -> list[IvGridPoint]:
    return [IvGridPoint(strike=row.strike, call_iv=row.call.iv, put_iv=row.put.iv) for row in chain.rows]


def atm_iv(chain: OptionChain) -> float:
    atm_row = min(chain.rows, key=lambda r: abs(r.strike - chain.spot))
    return round((atm_row.call.iv + atm_row.put.iv) / 2.0, 4)


def realized_volatility(minute_prices: list[float], annualization_minutes: int = 375 * 252) -> float:
    """Annualized realized (historical) volatility from a minute-close series,
    via the standard sum-of-squared-log-returns estimator.
    """
    if len(minute_prices) < 2:
        return 0.0
    log_returns = [
        math.log(minute_prices[i] / minute_prices[i - 1])
        for i in range(1, len(minute_prices))
        if minute_prices[i - 1] > 0
    ]
    if not log_returns:
        return 0.0
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / max(len(log_returns) - 1, 1)
    per_minute_vol = math.sqrt(variance)
    return per_minute_vol * math.sqrt(annualization_minutes)


def iv_hv_spread(chain: OptionChain, minute_prices: list[float]) -> dict:
    iv = atm_iv(chain)
    hv = realized_volatility(minute_prices)
    return {
        "atm_iv": iv,
        "historical_vol": round(hv, 4),
        "spread": round(iv - hv, 4),
        "regime": "iv_rich" if iv > hv else "iv_cheap",
    }


def volatility_skew(chain: OptionChain, delta_target: float = 0.25) -> dict:
    """25-delta risk reversal style skew: OTM put IV minus OTM call IV at the
    strikes closest to the target |delta|. Positive skew (the typical Indian
    index smirk) means downside puts are priced richer than upside calls.
    """
    otm_puts = [r for r in chain.rows if r.strike < chain.spot]
    otm_calls = [r for r in chain.rows if r.strike > chain.spot]

    put_leg = min(otm_puts, key=lambda r: abs(abs(r.put.greeks.delta) - delta_target), default=None)
    call_leg = min(otm_calls, key=lambda r: abs(abs(r.call.greeks.delta) - delta_target), default=None)

    if put_leg is None or call_leg is None:
        return {"skew": 0.0, "put_iv": None, "call_iv": None}

    skew = put_leg.put.iv - call_leg.call.iv
    return {
        "skew": round(skew, 4),
        "put_strike": put_leg.strike,
        "put_iv": put_leg.put.iv,
        "call_strike": call_leg.strike,
        "call_iv": call_leg.call.iv,
    }
