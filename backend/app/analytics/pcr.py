"""Put-Call Ratio, by open interest and by volume."""
from __future__ import annotations

from dataclasses import dataclass

from app.data.mock_feed import OptionChain


@dataclass(frozen=True)
class PcrResult:
    pcr_oi: float
    pcr_volume: float
    total_call_oi: int
    total_put_oi: int
    total_call_volume: int
    total_put_volume: int


def compute_pcr(chain: OptionChain) -> PcrResult:
    total_call_oi = sum(row.call.oi for row in chain.rows)
    total_put_oi = sum(row.put.oi for row in chain.rows)
    total_call_volume = sum(row.call.volume for row in chain.rows)
    total_put_volume = sum(row.put.volume for row in chain.rows)

    pcr_oi = total_put_oi / total_call_oi if total_call_oi else 0.0
    pcr_volume = total_put_volume / total_call_volume if total_call_volume else 0.0

    return PcrResult(
        pcr_oi=round(pcr_oi, 4),
        pcr_volume=round(pcr_volume, 4),
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
        total_call_volume=total_call_volume,
        total_put_volume=total_put_volume,
    )
