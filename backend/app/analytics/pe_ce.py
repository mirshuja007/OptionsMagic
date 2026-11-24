"""Per-strike put-minus-call premium difference."""
from __future__ import annotations

from dataclasses import dataclass

from app.data.mock_feed import OptionChain


@dataclass(frozen=True)
class PeCeDifferencePoint:
    strike: float
    call_premium: float
    put_premium: float
    difference: float  # put premium minus call premium, at this strike


def pe_ce_difference(chain: OptionChain) -> list[PeCeDifferencePoint]:
    """Put premium minus call premium at every strike. Near the money this
    tracks how far spot sits from that strike (put-call parity), but a
    reading that drifts away from the parity-implied value points at a
    skew/demand imbalance between the put and call at that specific strike
    — a per-strike read, distinct from ``volatility.volatility_skew``'s
    single chain-wide 25-delta number.
    """
    return [
        PeCeDifferencePoint(
            strike=row.strike, call_premium=row.call.ltp, put_premium=row.put.ltp,
            difference=round(row.put.ltp - row.call.ltp, 2),
        )
        for row in sorted(chain.rows, key=lambda r: r.strike)
    ]
