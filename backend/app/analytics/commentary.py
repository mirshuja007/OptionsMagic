"""Expiry-day commentary: OI-based technical levels and a probability
estimate for the underlying settling within a tight band of spot.

Everything here is a plain deterministic calculation over numbers the rest
of ``app.analytics`` already computes from the live/mock chain — there is no
free-text generation. The frontend assembles the actual commentary sentence
by substituting these numbers into a fixed template, so nothing displayed is
invented beyond what the chain/IV/OI data itself says.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.black_scholes import norm_cdf
from app.data.mock_feed import OptionChain


@dataclass(frozen=True)
class SupportResistance:
    support_strike: float
    support_put_oi: int
    resistance_strike: float
    resistance_call_oi: int


def support_resistance(chain: OptionChain) -> SupportResistance:
    """The standard retail-options reading of an OI profile: the strike at
    or below spot with the heaviest put OI acts as a support floor (put
    writers are collectively betting the underlying doesn't fall through
    it), and the strike at or above spot with the heaviest call OI acts as
    a resistance ceiling (same logic, call writers, upside). Falls back to
    the chain-wide heaviest-OI strike on either side if the chain doesn't
    span both sides of spot (e.g. a very short, narrow chain).
    """
    below_or_at = [r for r in chain.rows if r.strike <= chain.spot] or chain.rows
    above_or_at = [r for r in chain.rows if r.strike >= chain.spot] or chain.rows

    support_row = max(below_or_at, key=lambda r: r.put.oi)
    resistance_row = max(above_or_at, key=lambda r: r.call.oi)

    return SupportResistance(
        support_strike=support_row.strike,
        support_put_oi=support_row.put.oi,
        resistance_strike=resistance_row.strike,
        resistance_call_oi=resistance_row.call.oi,
    )


def expiry_band_probability(
    spot: float, atm_iv: float, time_to_expiry_years: float, band_pct: float = 0.002
) -> dict:
    """Probability the underlying settles within +/- ``band_pct`` of the
    current spot at expiry, from ATM implied volatility and time to expiry.

    Standard lognormal "probability of expiring in range" calculation: under
    a lognormal terminal-price model, ln(S_T / S_0) is approximately Normal
    with standard deviation ``sigma * sqrt(T)``. This assumes zero drift over
    the remaining horizon (no r/q term) — the usual simplification for
    near-dated probability-of-touch/expire estimates, since for the short
    windows involved (same-day to a few weeks) the drift term is negligible
    next to the volatility term. Reuses ``core.black_scholes.norm_cdf``
    (the same normal-CDF machinery Black-Scholes pricing itself uses) rather
    than a separate implementation.

    Returns ``probability: None`` if the inputs make the calculation
    undefined (no time left, or IV couldn't be solved) rather than guessing.
    """
    lower = spot * (1 - band_pct)
    upper = spot * (1 + band_pct)

    if time_to_expiry_years <= 0 or atm_iv <= 0:
        return {"band_pct": band_pct, "lower": round(lower, 2), "upper": round(upper, 2), "probability": None}

    sigma_t = atm_iv * math.sqrt(time_to_expiry_years)
    z_upper = math.log(upper / spot) / sigma_t
    z_lower = math.log(lower / spot) / sigma_t
    probability = norm_cdf(z_upper) - norm_cdf(z_lower)

    return {
        "band_pct": band_pct,
        "lower": round(lower, 2),
        "upper": round(upper, 2),
        "probability": round(probability, 4),
    }
