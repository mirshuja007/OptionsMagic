"""Simulated live option-chain feed.

Stands in for a real broker/vendor feed (Kite Connect, SmartAPI, Fyers, NSE
India) so every analytics/strategy/backtest module has real, internally
consistent data to operate on without external credentials. Swap this module
out for a real feed adapter behind the same function signatures to go live.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np

from app.core.black_scholes import OptionType, greeks as bs_greeks, price as bs_price
from app.data.instruments import Instrument, get_instrument
from app.data.models import ChainRow, LegQuote, OptionChain

__all__ = [
    "ChainRow",
    "LegQuote",
    "OptionChain",
    "RISK_FREE_RATE",
    "generate_minute_series",
    "generate_option_chain",
    "next_weekly_expiry",
]

RISK_FREE_RATE = 0.065  # approx. Indian T-bill / repo-anchored rate used across the platform


def next_weekly_expiry(today: date | None = None, weekday: int = 1) -> date:
    """Nearest ``weekday`` (Monday=0 ... Sunday=6) on/after today. Defaults
    to Tuesday (1), NSE's current weekly index-options expiry day — see
    ``Instrument.expiry_weekday`` for the per-instrument, currently-correct
    values (BSE's Sensex, for example, is Thursday, not Tuesday).
    """
    today = today or date.today()
    days_ahead = (weekday - today.weekday()) % 7
    days_ahead = days_ahead or 7
    return today + timedelta(days=days_ahead)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """The last occurrence of ``weekday`` in the given month — NSE's monthly
    F&O expiry convention (e.g. last Tuesday) for contracts without weekly
    expiry.
    """
    first_of_next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day_of_month = first_of_next_month - timedelta(days=1)
    offset = (last_day_of_month.weekday() - weekday) % 7
    return last_day_of_month - timedelta(days=offset)


def _next_monthly_expiry(today: date, weekday: int) -> date:
    candidate = _last_weekday_of_month(today.year, today.month, weekday)
    if candidate < today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        candidate = _last_weekday_of_month(year, month, weekday)
    return candidate


def _default_expiry(instrument: Instrument, today: date) -> date:
    if instrument.is_commodity:
        # MCX commodity options run monthly cycles that vary per commodity
        # and shift for holidays — there's no simple formula like NSE's
        # weekly/monthly convention below. This is a rough illustrative
        # stand-in only; real usage should get the actual listed expiry
        # from kite_feed instead, which never hardcodes any of this.
        return today + timedelta(days=20)
    if instrument.expiry_cadence == "monthly":
        return _next_monthly_expiry(today, instrument.expiry_weekday)
    return next_weekly_expiry(today, instrument.expiry_weekday)


def _iv_smile(moneyness_log: float, base_iv: float) -> float:
    """A volatility skew/smirk: OTM puts (negative log-moneyness) trade richer
    than OTM calls, a well-documented feature of Indian index options.
    """
    skew_slope = -0.35
    smile_curvature = 0.9
    iv = base_iv + skew_slope * moneyness_log + smile_curvature * moneyness_log**2
    return max(iv, 0.04)


def _seed_for(symbol: str, seed: int | None) -> int:
    if seed is not None:
        return seed
    return abs(hash(symbol)) % (2**31)


def generate_option_chain(
    symbol: str,
    expiry: date | None = None,
    spot_override: float | None = None,
    num_strikes: int = 21,
    as_of: datetime | None = None,
    seed: int | None = None,
) -> OptionChain:
    instrument = get_instrument(symbol)
    rng = np.random.default_rng(_seed_for(symbol, seed))

    as_of = as_of or datetime.now()
    expiry = expiry or _default_expiry(instrument, as_of.date())
    expiry_dt = datetime.combine(expiry, instrument.session_end)
    t = max((expiry_dt - as_of).total_seconds() / (365.0 * 24 * 3600), 1e-6)
    q = RISK_FREE_RATE if instrument.pricing_carry_rate_equals_risk_free else 0.0

    spot = spot_override if spot_override is not None else instrument.base_spot * (1 + rng.normal(0, 0.004))
    step = instrument.strike_step
    atm_strike = round(spot / step) * step
    half = num_strikes // 2

    rows: list[ChainRow] = []
    for i in range(-half, half + 1):
        strike = atm_strike + i * step
        if strike <= 0:
            continue
        log_moneyness = math.log(strike / spot)

        call_iv = _iv_smile(log_moneyness, instrument.base_iv)
        put_iv = _iv_smile(log_moneyness, instrument.base_iv)

        call = _make_leg_quote(spot, strike, t, OptionType.CALL, call_iv, i, rng, q)
        put = _make_leg_quote(spot, strike, t, OptionType.PUT, put_iv, i, rng, q)
        rows.append(ChainRow(strike=strike, call=call, put=put))

    return OptionChain(
        symbol=instrument.symbol,
        spot=spot,
        expiry=expiry,
        timestamp=as_of,
        time_to_expiry_years=t,
        risk_free_rate=RISK_FREE_RATE,
        rows=rows,
    )


def _make_leg_quote(
    spot: float,
    strike: float,
    t: float,
    option_type: OptionType,
    iv: float,
    strike_offset: int,
    rng: np.random.Generator,
    q: float = 0.0,
) -> LegQuote:
    theo = bs_price(spot, strike, t, RISK_FREE_RATE, iv, option_type, q)
    spread_pct = 0.004 + 0.0015 * abs(strike_offset)
    mid = max(theo * (1 + rng.normal(0, 0.01)), 0.05)
    half_spread = max(mid * spread_pct, 0.05)
    bid = max(mid - half_spread, 0.05)
    ask = mid + half_spread

    g = bs_greeks(spot, strike, t, RISK_FREE_RATE, iv, option_type, q)

    # OI/volume peak near ATM and decay with distance; add noise for realism.
    distance_decay = math.exp(-0.15 * strike_offset**2)
    base_oi = int(rng.uniform(0.4, 1.0) * 3_000_000 * distance_decay) + int(rng.integers(0, 50_000))
    oi_change = int(rng.normal(0, base_oi * 0.08 + 1))
    volume = int(base_oi * rng.uniform(0.05, 0.35))

    return LegQuote(
        option_type=option_type,
        ltp=round(mid, 2),
        bid=round(bid, 2),
        ask=round(ask, 2),
        oi=max(base_oi, 0),
        oi_change=oi_change,
        volume=max(volume, 0),
        iv=round(iv, 4),
        greeks=g,
    )


def generate_minute_series(
    symbol: str,
    session_date: date | None = None,
    minutes: int = 375,  # NSE cash session length, 09:15-15:30
    seed: int | None = None,
) -> list[tuple[datetime, float]]:
    """Simulated minute-by-minute underlying spot path for a trading session,
    used by the backtest replay engine. GBM with intraday vol.
    """
    instrument = get_instrument(symbol)
    rng = np.random.default_rng(_seed_for(symbol + "-minute", seed))
    session_date = session_date or date.today()
    start = datetime.combine(session_date, instrument.session_start)

    dt = 1.0 / (252 * 375)  # one minute as a fraction of a trading year
    sigma = instrument.base_iv
    prices = [instrument.base_spot]
    for _ in range(minutes - 1):
        shock = rng.normal(-0.5 * sigma**2 * dt, sigma * math.sqrt(dt))
        prices.append(prices[-1] * math.exp(shock))

    return [(start + timedelta(minutes=i), p) for i, p in enumerate(prices)]
