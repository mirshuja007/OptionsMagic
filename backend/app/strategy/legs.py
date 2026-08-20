"""Generic multi-leg option strategy representation: payoff, mark-to-market,
portfolio Greeks, and defined/undefined-risk detection. Every strategy in the
platform (credit spread, iron condor, iron fly, ratio spread, custom hedge)
is just a list of ``Leg``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

from app.core.black_scholes import Greeks, OptionType, greeks as bs_greeks, intrinsic_value, price as bs_price


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Leg:
    option_type: OptionType
    strike: float
    side: Side
    quantity_lots: int
    entry_price: float  # premium per unit at entry (mid price from the chain)
    iv: float
    q: float = 0.0  # carry/dividend yield for repricing; commodity (futures-underlying) legs set q=r (Black-76)
    tradingsymbol: str = ""  # Kite tradingsymbol; only set for legs sourced from the live Kite feed

    @property
    def sign(self) -> int:
        return 1 if self.side == Side.LONG else -1


def _signed_units(leg: Leg, lot_size: int) -> int:
    return leg.sign * leg.quantity_lots * lot_size


def payoff_at_expiry(legs: list[Leg], lot_size: int, terminal_spot: np.ndarray) -> np.ndarray:
    """Vectorized net P&L of the combo at expiry across an array of terminal
    underlying prices.
    """
    pnl = np.zeros_like(terminal_spot, dtype=float)
    for leg in legs:
        if leg.option_type == OptionType.CALL:
            intrinsic = np.maximum(terminal_spot - leg.strike, 0.0)
        else:
            intrinsic = np.maximum(leg.strike - terminal_spot, 0.0)
        pnl += _signed_units(leg, lot_size) * (intrinsic - leg.entry_price)
    return pnl


def net_entry_cashflow(legs: list[Leg], lot_size: int) -> float:
    """Cash received (positive) or paid (negative) when the combo is opened."""
    return sum(-leg.sign * leg.quantity_lots * lot_size * leg.entry_price for leg in legs)


def mark_to_market(legs: list[Leg], spot: float, t: float, r: float, lot_size: int, iv_shift: float = 0.0) -> float:
    """Current P&L of the open combo if repriced now, ``t`` years to expiry,
    each leg's IV shifted by ``iv_shift`` (for margin/vol-scan scenarios).
    """
    total = 0.0
    for leg in legs:
        shifted_iv = max(leg.iv + iv_shift, 1e-4)
        current_price = bs_price(spot, leg.strike, t, r, shifted_iv, leg.option_type, leg.q) if t > 0 else intrinsic_value(
            spot, leg.strike, leg.option_type
        )
        total += _signed_units(leg, lot_size) * (current_price - leg.entry_price)
    return total


def portfolio_greeks(legs: list[Leg], spot: float, t: float, r: float, lot_size: int) -> Greeks:
    delta = gamma = theta = vega = rho = 0.0
    for leg in legs:
        g = bs_greeks(spot, leg.strike, t, r, leg.iv, leg.option_type, leg.q) if t > 0 else Greeks(0, 0, 0, 0, 0)
        units = _signed_units(leg, lot_size)
        delta += units * g.delta
        gamma += units * g.gamma
        theta += units * g.theta
        vega += units * g.vega
        rho += units * g.rho
    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho)


@dataclass(frozen=True)
class PayoffExtrema:
    max_profit: float
    max_loss: float
    unlimited_upside_risk: bool
    unlimited_downside_risk: bool


_UNLIMITED_RISK_SENTINEL = 1e12


def payoff_extrema(legs: list[Leg], lot_size: int) -> PayoffExtrema:
    """Exact max profit / max loss of the combo at expiry. The payoff of a
    portfolio of European options is piecewise-linear in the terminal spot,
    so extrema occur either at strikes (kink points) or, if a tail slope is
    non-zero, at +/- infinity in whichever direction that slope points.

    A non-flat tail isn't automatically "unlimited profit" — a ratio spread
    with more short legs than long on one side (e.g. 1 long call + 2 short
    calls) has a *falling* tail beyond the short strike: unbounded loss, not
    unbounded profit. The sign of the tail slope, not just its non-flatness,
    determines which side (max_profit or max_loss) actually goes unbounded.
    """
    strikes = sorted({leg.strike for leg in legs})
    if not strikes:
        return PayoffExtrema(0.0, 0.0, False, False)

    lo_bound = max(strikes[0] * 0.01, 0.01)
    hi_bound = strikes[-1] * 5.0
    kinks = np.array(strikes + [lo_bound, hi_bound], dtype=float)
    kink_pnl = payoff_at_expiry(legs, lot_size, kinks)

    # Detect unbounded tails, and their direction, by comparing the slope
    # just outside the outer kinks. Moving further down (lo_bound -> far_lo)
    # or further up (hi_bound -> far_hi): a rising tail means profit grows
    # without bound in that direction; a falling tail means loss does.
    far_lo = max(lo_bound * 0.1, 1e-3)
    far_hi = hi_bound * 2.0
    pnl_lo, pnl_far_lo = payoff_at_expiry(legs, lot_size, np.array([lo_bound, far_lo]))
    pnl_hi, pnl_far_hi = payoff_at_expiry(legs, lot_size, np.array([hi_bound, far_hi]))

    downside_flat = math.isclose(pnl_lo, pnl_far_lo, rel_tol=1e-6, abs_tol=1e-6)
    upside_flat = math.isclose(pnl_hi, pnl_far_hi, rel_tol=1e-6, abs_tol=1e-6)

    unlimited_downside_loss = not downside_flat and pnl_far_lo < pnl_lo
    unlimited_downside_profit = not downside_flat and pnl_far_lo > pnl_lo
    unlimited_upside_loss = not upside_flat and pnl_far_hi < pnl_hi
    unlimited_upside_profit = not upside_flat and pnl_far_hi > pnl_hi

    max_profit = _UNLIMITED_RISK_SENTINEL if (unlimited_downside_profit or unlimited_upside_profit) else float(np.max(kink_pnl))
    max_loss = -_UNLIMITED_RISK_SENTINEL if (unlimited_downside_loss or unlimited_upside_loss) else float(np.min(kink_pnl))

    return PayoffExtrema(
        max_profit=max_profit,
        max_loss=max_loss,
        unlimited_upside_risk=unlimited_upside_loss or unlimited_upside_profit,
        unlimited_downside_risk=unlimited_downside_loss or unlimited_downside_profit,
    )
