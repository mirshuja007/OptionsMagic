"""Black-Scholes-Merton pricing, Greeks, and implied-volatility inversion.

All rates/vols are annualized; ``t`` is time to expiry in years. Options are
European-style, matching NSE/BSE index and stock F&O settlement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"


_SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 vol point (1%)
    rho: float  # per 1% rate change


def d1_d2(spot: float, strike: float, t: float, r: float, sigma: float, q: float = 0.0) -> tuple[float, float]:
    if t <= 0 or sigma <= 0:
        raise ValueError("t and sigma must be positive")
    vol_sqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def price(
    spot: float,
    strike: float,
    t: float,
    r: float,
    sigma: float,
    option_type: OptionType,
    q: float = 0.0,
) -> float:
    """Theoretical option price. ``t`` in years, ``r``/``q``/``sigma`` as decimals (0.06 = 6%)."""
    if t <= 0:
        return intrinsic_value(spot, strike, option_type)
    d1, d2 = d1_d2(spot, strike, t, r, sigma, q)
    disc_r = math.exp(-r * t)
    disc_q = math.exp(-q * t)
    if option_type == OptionType.CALL:
        return spot * disc_q * norm_cdf(d1) - strike * disc_r * norm_cdf(d2)
    return strike * disc_r * norm_cdf(-d2) - spot * disc_q * norm_cdf(-d1)


def intrinsic_value(spot: float, strike: float, option_type: OptionType) -> float:
    if option_type == OptionType.CALL:
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def greeks(
    spot: float,
    strike: float,
    t: float,
    r: float,
    sigma: float,
    option_type: OptionType,
    q: float = 0.0,
) -> Greeks:
    if t <= 0 or sigma <= 0:
        return Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = d1_d2(spot, strike, t, r, sigma, q)
    disc_r = math.exp(-r * t)
    disc_q = math.exp(-q * t)
    pdf_d1 = norm_pdf(d1)

    gamma = disc_q * pdf_d1 / (spot * sigma * math.sqrt(t))
    vega = spot * disc_q * pdf_d1 * math.sqrt(t) / 100.0  # per 1 vol point

    if option_type == OptionType.CALL:
        delta = disc_q * norm_cdf(d1)
        theta_annual = (
            -spot * disc_q * pdf_d1 * sigma / (2 * math.sqrt(t))
            - r * strike * disc_r * norm_cdf(d2)
            + q * spot * disc_q * norm_cdf(d1)
        )
        rho = strike * t * disc_r * norm_cdf(d2) / 100.0
    else:
        delta = -disc_q * norm_cdf(-d1)
        theta_annual = (
            -spot * disc_q * pdf_d1 * sigma / (2 * math.sqrt(t))
            + r * strike * disc_r * norm_cdf(-d2)
            - q * spot * disc_q * norm_cdf(-d1)
        )
        rho = -strike * t * disc_r * norm_cdf(-d2) / 100.0

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta_annual / 365.0,
        vega=vega,
        rho=rho,
    )


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    t: float,
    r: float,
    option_type: OptionType,
    q: float = 0.0,
    initial_guess: float = 0.3,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """Solve for sigma via Newton-Raphson with a bisection fallback. Returns None if it fails to converge."""
    if t <= 0 or market_price <= 0:
        return None

    lower = intrinsic_value(spot, strike, option_type) * math.exp(-q * t)
    if market_price < lower - 1e-9:
        return None

    sigma = initial_guess
    for _ in range(max_iter):
        try:
            model_price = price(spot, strike, t, r, sigma, option_type, q)
            d1, _ = d1_d2(spot, strike, t, r, sigma, q)
            vega_raw = spot * math.exp(-q * t) * norm_pdf(d1) * math.sqrt(t)
        except ValueError:
            break
        diff = model_price - market_price
        if abs(diff) < tol:
            return max(sigma, 1e-6)
        if vega_raw < 1e-8:
            break
        sigma -= diff / vega_raw
        if sigma <= 0:
            sigma = 1e-4

    # Bisection fallback, robust but slower.
    lo, hi = 1e-4, 5.0
    f_lo = price(spot, strike, t, r, lo, option_type, q) - market_price
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = price(spot, strike, t, r, mid, option_type, q) - market_price
        if abs(f_mid) < tol:
            return mid
        if (f_lo < 0) == (f_mid < 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return None
