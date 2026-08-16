"""ATM/multi-strike straddle pricing and time-decay projection."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.black_scholes import OptionType, price as bs_price
from app.data.mock_feed import OptionChain


@dataclass(frozen=True)
class StraddlePoint:
    strike: float
    call_premium: float
    put_premium: float
    combined_premium: float


def atm_straddle(chain: OptionChain) -> StraddlePoint:
    row = min(chain.rows, key=lambda r: abs(r.strike - chain.spot))
    return StraddlePoint(
        strike=row.strike,
        call_premium=row.call.ltp,
        put_premium=row.put.ltp,
        combined_premium=round(row.call.ltp + row.put.ltp, 2),
    )


def multistrike_straddle(chain: OptionChain, num_strikes: int = 5) -> list[StraddlePoint]:
    """Combined call+put premium across the strikes nearest the money, useful
    for spotting consolidation (falling combined premium) vs. breakout risk
    (rising combined premium) across the strip.
    """
    sorted_rows = sorted(chain.rows, key=lambda r: abs(r.strike - chain.spot))
    nearest = sorted_rows[:num_strikes]
    return [
        StraddlePoint(
            strike=row.strike,
            call_premium=row.call.ltp,
            put_premium=row.put.ltp,
            combined_premium=round(row.call.ltp + row.put.ltp, 2),
        )
        for row in sorted(nearest, key=lambda r: r.strike)
    ]


def premium_decay_curve(chain: OptionChain, points: int = 10) -> list[dict]:
    """Projected combined ATM straddle premium as time-to-expiry shrinks to
    zero, holding spot and IV fixed — isolates theta decay from directional
    or volatility moves, matching how the platform's decay chart should read.
    """
    row = min(chain.rows, key=lambda r: abs(r.strike - chain.spot))
    call_iv, put_iv = row.call.iv, row.put.iv
    strike, spot, r = row.strike, chain.spot, chain.risk_free_rate

    curve = []
    for i in range(points, -1, -1):
        t = chain.time_to_expiry_years * i / points
        if t <= 1e-6:
            call_p = max(spot - strike, 0.0)
            put_p = max(strike - spot, 0.0)
        else:
            call_p = bs_price(spot, strike, t, r, call_iv, OptionType.CALL)
            put_p = bs_price(spot, strike, t, r, put_iv, OptionType.PUT)
        curve.append(
            {
                "time_to_expiry_years": round(t, 6),
                "combined_premium": round(call_p + put_p, 2),
            }
        )
    return curve
