"""Simplified SPAN + exposure margin estimator.

This is NOT the exchange's actual SPAN engine (that requires NSE's daily
risk-parameter files and proprietary scanning software). It reproduces
SPAN's *methodology* at a workable fidelity for strategy screening:

  1. Price/volatility scanning — reprice the whole combo across a grid of
     underlying price shocks (a symmetric scan range around spot) crossed
     with implied-vol shocks, and take the worst-case mark-to-market loss.
     Two extreme price shocks (2x the scan range) are included at a reduced
     "cover" weight, mirroring SPAN's short-option-minimum / extreme-move
     scenarios.
  2. Exposure margin — an additional buffer proportional to uncovered
     (unhedged) short notional, approximating SEBI's exposure-margin norms.

Defined-risk combos (e.g. credit spreads, iron condors) automatically get
margin relief here because the scan naturally caps losses at the hedge
strike — no special-casing needed.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.data.instruments import Instrument
from app.strategy.legs import Leg, Side, mark_to_market, net_entry_cashflow

_PRICE_SHOCK_FRACTIONS = [-1.0, -2 / 3, -1 / 3, 0.0, 1 / 3, 2 / 3, 1.0]
_VOL_SHOCK_POINTS = [-0.04, 0.0, 0.04]  # +/- 4 vol points
_EXTREME_PRICE_FRACTIONS = [-2.0, 2.0]
_EXTREME_COVER_WEIGHT = 0.35


@dataclass(frozen=True)
class MarginEstimate:
    span_margin: float
    exposure_margin: float
    total_margin: float
    net_entry_credit: float


def estimate_margin(
    legs: list[Leg],
    instrument: Instrument,
    spot: float,
    t: float,
    r: float,
) -> MarginEstimate:
    if not legs:
        return MarginEstimate(0.0, 0.0, 0.0, 0.0)

    lot_size = instrument.lot_size
    scan_range = spot * instrument.margin_price_scan_pct

    worst_loss = 0.0
    for frac in _PRICE_SHOCK_FRACTIONS:
        shocked_spot = max(spot + frac * scan_range, 0.01)
        for vol_shock in _VOL_SHOCK_POINTS:
            pnl = mark_to_market(legs, shocked_spot, t, r, lot_size, iv_shift=vol_shock)
            worst_loss = min(worst_loss, pnl)

    for frac in _EXTREME_PRICE_FRACTIONS:
        shocked_spot = max(spot + frac * scan_range, 0.01)
        pnl = mark_to_market(legs, shocked_spot, t, r, lot_size, iv_shift=0.0)
        worst_loss = min(worst_loss, pnl * _EXTREME_COVER_WEIGHT)

    span_margin = max(-worst_loss, 0.0)

    short_lots = sum(leg.quantity_lots for leg in legs if leg.side == Side.SHORT)
    long_lots = sum(leg.quantity_lots for leg in legs if leg.side == Side.LONG)
    uncovered_short_lots = max(short_lots - long_lots, 0)
    exposure_margin = uncovered_short_lots * lot_size * spot * instrument.margin_exposure_pct

    total_margin = span_margin + exposure_margin
    return MarginEstimate(
        span_margin=round(span_margin, 2),
        exposure_margin=round(exposure_margin, 2),
        total_margin=round(total_margin, 2),
        net_entry_credit=round(net_entry_cashflow(legs, lot_size), 2),
    )
