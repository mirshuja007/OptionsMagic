"""Closing Auction Session (CAS) mechanics: the day's auction-window
schedule and reference-price/band calculation.

SEBI introduced CAS for NSE/BSE-listed F&O-eligible stocks ("Category I")
from August 3, 2026 (SEBI circular, Jan 16 2026), replacing the old
last-30-minutes-VWAP closing-price method for those stocks with a 20-minute
call auction: buy/sell orders are collected (not executed immediately) from
3:15-3:35pm and matched at the single price that maximizes executable
volume. See Zerodha's own explainer for the mechanism this module encodes:
https://zerodha.com/z-connect/general/everything-you-need-to-know-about-closing-auction-session-cas

This only covers individual stocks — there is no per-index CAS. An index's
closing value is still just the weighted sum of its constituents' closes;
those constituents' closes are now set by CAS instead of VWAP, for whichever
of them are Category I (have listed F&O contracts).

Whether Kite Connect's *API* (as opposed to Kite Web's own UI) surfaces the
live auction fields Kite Web shows (reference price, indicative close,
imbalance quantity) during the 3:15-3:35pm window is unconfirmed — see
``app.data.kite_feed.raw_underlying_quote`` for the live diagnostic probe.
Nothing in this module depends on that being true: the reference price and
band below are computed independently from ordinary minute-bar data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from app.analytics.vwap import vwap_series

IST = timezone(timedelta(hours=5, minutes=30))

# NSE's CAS timeline for Category I (F&O-eligible) stocks.
MARKET_OPEN = time(9, 15)
REFERENCE_WINDOW_START = time(15, 0)
REFERENCE_WINDOW_END = time(15, 15)
AUCTION_END = time(15, 35)
TRANSITION_END = time(15, 50)
POST_CLOSE_END = time(16, 0)

REFERENCE_BAND_PCT = 0.03  # +/-3% of the reference price — the auction's allowed price band


@dataclass(frozen=True)
class CASWindowStatus:
    phase: str  # "pre_open" | "continuous" | "reference_window" | "auction" | "transition" | "post_close" | "closed"
    label: str
    now_ist: datetime


def cas_window_status(now: datetime | None = None) -> CASWindowStatus:
    """Which phase of the trading/CAS day it is right now, in IST.
    ``now`` is for tests (pass an aware or naive datetime — only ``.time()``
    is used); omit it to use the actual current time.
    """
    now_ist = now if now is not None else datetime.now(IST)
    t = now_ist.time()

    if t < MARKET_OPEN:
        return CASWindowStatus("pre_open", "Market not yet open", now_ist)
    if t < REFERENCE_WINDOW_START:
        return CASWindowStatus("continuous", "Continuous trading", now_ist)
    if t < REFERENCE_WINDOW_END:
        return CASWindowStatus("reference_window", "Reference price window (VWAP building, 3:00–3:15pm)", now_ist)
    if t < AUCTION_END:
        return CASWindowStatus("auction", "Closing Auction Session (CAS) — live", now_ist)
    if t < TRANSITION_END:
        return CASWindowStatus("transition", "Transition period", now_ist)
    if t < POST_CLOSE_END:
        return CASWindowStatus("post_close", "Post-close session", now_ist)
    return CASWindowStatus("closed", "Market closed", now_ist)


def reference_price(series: list[tuple[datetime, float, int]]) -> float | None:
    """VWAP of the 3:00-3:15pm bars in ``series`` (a
    ``generate_minute_series``-shaped list of (timestamp, price, volume)) —
    the price CAS's +/-3% band is centered on. ``None`` if the series
    doesn't cover that window (e.g. queried before 3:00pm, or a series that
    ends earlier in the session).
    """
    window = [(p, v) for t, p, v in series if REFERENCE_WINDOW_START <= t.time() < REFERENCE_WINDOW_END]
    if not window:
        return None
    return vwap_series(window)[-1]


def reference_band(ref_price: float) -> tuple[float, float]:
    """(lower, upper) +/-3% auction price band around the reference price."""
    return ref_price * (1 - REFERENCE_BAND_PCT), ref_price * (1 + REFERENCE_BAND_PCT)
