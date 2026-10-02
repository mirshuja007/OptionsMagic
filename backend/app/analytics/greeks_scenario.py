"""Greeks + volatility "what-if" scenario calculator.

Answers one question in the plainest terms possible: *if the index moves by
X% and implied vol moves with it, roughly what happens to this option's
premium, and what would that cost (or make) me as a seller?*

This is a sizing aid, not a forecast. It reprices the option with Black-
Scholes at a shocked spot and a shocked IV — the same technique
``app.margin.span`` already uses for margin scenario scanning — rather than
a linear delta/gamma/vega Taylor approximation, so the two displayed
components (the move's spot-driven part and its vol-driven part) sum
*exactly* to the total premium change instead of only approximately.

India VIX is not wired into this platform as a live feed (no instrument
entry, no fetch path), and a single number wouldn't tell you how much a
*given* underlying's IV typically reacts to a given move anyway — that
varies by symbol and by how fearful the move feels. So instead of a live
VIX quote, ``VOL_REGIMES`` below holds hand-picked, clearly-labeled
rule-of-thumb IV reaction points per 1% of index move, asymmetric by
direction because downside moves reliably spike IV harder than upside moves
cool it (the "fear skew" every index options desk prices in). These are
illustrative defaults for building intuition, not a calibrated model — the
page lets the user override the IV change by hand if they have a better
number (e.g. from watching India VIX themselves).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.black_scholes import OptionType, price as bs_price

# (IV points gained per 1% the index falls, IV points gained per 1% the
# index rises — the second is usually negative, i.e. vol cools on rallies).
# One point = one percentage point of annualized IV (matches Greeks.vega's
# "per 1 vol point" convention).
VOL_REGIMES: dict[str, tuple[float, float]] = {
    "Calm": (0.5, -0.3),
    "Normal": (1.5, -0.5),
    "Panic": (4.0, -1.0),
}

DEFAULT_REGIME = "Normal"


def iv_change_for_move(move_pct: float, regime: str) -> float:
    """IV change in percentage points implied by an index move of ``move_pct``
    percent under ``regime``'s rule-of-thumb sensitivities. Linear in move
    size either side of zero.
    """
    down_sensitivity, up_sensitivity = VOL_REGIMES[regime]
    if move_pct < 0:
        return down_sensitivity * abs(move_pct)
    return up_sensitivity * move_pct


@dataclass(frozen=True)
class ScenarioResult:
    shocked_spot: float
    shocked_iv: float  # decimal, e.g. 0.16
    base_price: float
    new_price: float
    price_change: float
    price_change_pct: float
    spot_component: float  # price change attributable to the spot move alone
    vol_component: float  # price change attributable to the IV change alone


def reprice_scenario(
    spot: float,
    strike: float,
    t: float,
    r: float,
    iv: float,
    option_type: OptionType,
    move_pct: float,
    iv_change_points: float,
    q: float = 0.0,
) -> ScenarioResult:
    """Reprice one leg under a hypothetical ``move_pct``% spot move combined
    with an ``iv_change_points`` percentage-point IV shift, decomposing the
    total premium change into its spot-driven and vol-driven pieces.
    """
    shocked_spot = max(spot * (1 + move_pct / 100.0), 0.01)
    shocked_iv = max(iv + iv_change_points / 100.0, 1e-4)

    base_price = bs_price(spot, strike, t, r, iv, option_type, q)
    price_spot_only = bs_price(shocked_spot, strike, t, r, iv, option_type, q)
    new_price = bs_price(shocked_spot, strike, t, r, shocked_iv, option_type, q)

    price_change = new_price - base_price
    price_change_pct = (price_change / base_price * 100.0) if base_price > 0 else 0.0

    return ScenarioResult(
        shocked_spot=shocked_spot,
        shocked_iv=shocked_iv,
        base_price=base_price,
        new_price=new_price,
        price_change=price_change,
        price_change_pct=price_change_pct,
        spot_component=price_spot_only - base_price,
        vol_component=new_price - price_spot_only,
    )


def daily_std_pct(iv: float) -> float:
    """Convert annualized IV (decimal) to an implied one-trading-day
    standard deviation of the index's move, in percent — the same
    sqrt(time) scaling Black-Scholes itself uses, with 365 calendar days as
    the annualization base (matching ``black_scholes.py``'s own convention).
    """
    return iv * 100.0 / math.sqrt(365.0)


def move_in_sigma(move_pct: float, iv: float) -> float:
    """How many implied one-day standard deviations a hypothetical move of
    ``move_pct``% represents, given current IV. This is the "probability
    flavor" read: a bigger number means current option prices treat a move
    that size as rarer.
    """
    sd = daily_std_pct(iv)
    return move_pct / sd if sd > 0 else 0.0


def sigma_label(z: float) -> str:
    az = abs(z)
    if az < 1.0:
        return "a fairly ordinary day by current IV"
    if az < 2.0:
        return "a stronger-than-usual day by current IV"
    return "a rare, panic-sized day by current IV"


def seller_pnl(entry_premium: float, new_premium: float, lot_size: int, lots: int) -> float:
    """P&L for someone who *sold* ``lots`` lots at ``entry_premium``: profits
    when the premium falls, loses when it rises — the mirror image of a
    buyer's P&L.
    """
    return (entry_premium - new_premium) * lot_size * lots
