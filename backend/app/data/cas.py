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

import statistics
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


@dataclass(frozen=True)
class ConstituentSnapshot:
    """One tracked stock's CAS reference price/band alongside its most
    recent traded price and how far that's drifted from the reference —
    all real, directly-computed numbers, no modeling involved.
    """

    symbol: str
    display_name: str
    reference_price: float | None
    band_lower: float | None
    band_upper: float | None
    current_price: float | None
    deviation_pct: float | None  # (current - reference) / reference * 100


def constituent_snapshot(
    symbol: str, display_name: str, series: list[tuple[datetime, float, int]]
) -> ConstituentSnapshot:
    """Build one stock's snapshot from its minute-bar series (same series
    ``app.data.feed.generate_minute_series`` returns) — the last bar is
    taken as "current price" so this needs only one data call per stock,
    not a separate quote fetch.
    """
    if not series:
        return ConstituentSnapshot(symbol, display_name, None, None, None, None, None)

    ref = reference_price(series)
    current = series[-1][1]
    if ref is None:
        return ConstituentSnapshot(symbol, display_name, None, None, None, current, None)

    lower, upper = reference_band(ref)
    deviation = (current - ref) / ref * 100.0
    return ConstituentSnapshot(symbol, display_name, ref, lower, upper, current, deviation)


# Magnitude buckets for the constituent-bias signal below, in ascending
# order of |average deviation %| — (upper_bound_exclusive, label). The last
# entry's bound is infinite so every value lands in some bucket.
_MAGNITUDE_BUCKETS: list[tuple[float, str]] = [
    (0.1, "< 0.1%"),
    (0.3, "0.1% – 0.3%"),
    (1.0, "0.3% – 1%"),
    (3.0, "1% – 3%"),
    (float("inf"), "> 3%"),
]


def _magnitude_bucket(abs_pct: float) -> str:
    for bound, label in _MAGNITUDE_BUCKETS:
        if abs_pct < bound:
            return label
    return _MAGNITUDE_BUCKETS[-1][1]  # unreachable given the inf bound, kept for clarity


@dataclass(frozen=True)
class ConstituentBiasSignal:
    """A transparent, equal-weighted aggregate of how the tracked
    constituents are trading relative to their own CAS reference prices —
    NOT a prediction, NOT a statistically validated or backtested model,
    and NOT a probability of the index actually moving. It is exactly what
    its fields say: how many of N tracked stocks are currently above/below
    their reference price, and the plain average of their % deviations.
    Read ``breadth_pct`` as "how many heavyweight constituents agree with
    the average direction right now," not as a confidence score.
    """

    direction: str  # "Upside" | "Downside" | "Flat"
    magnitude_bucket: str
    average_deviation_pct: float
    breadth_pct: float
    n_up: int
    n_down: int
    n_flat: int
    n_total: int
    n_with_data: int


def compute_bias_signal(
    snapshots: list[ConstituentSnapshot], flat_threshold_pct: float = 0.02
) -> ConstituentBiasSignal | None:
    """Equal-weighted average deviation + breadth across every snapshot
    that actually has a reference price yet (``None`` if none do — e.g.
    called before 3:00pm IST). Equal-weighted deliberately: real NIFTY/
    SENSEX index weights are known precisely for only some of the tracked
    stocks (see instruments.py's STOCKS comments) — mixing confirmed and
    estimated weights into one blended figure would look more precise than
    it is. ``flat_threshold_pct`` is the +/- band around 0% treated as "not
    really moved" for both the direction call and the breadth count.
    """
    usable = [s for s in snapshots if s.deviation_pct is not None]
    if not usable:
        return None

    deviations = [s.deviation_pct for s in usable]
    average = statistics.fmean(deviations)
    n_up = sum(1 for d in deviations if d > flat_threshold_pct)
    n_down = sum(1 for d in deviations if d < -flat_threshold_pct)
    n_flat = len(deviations) - n_up - n_down

    if average > flat_threshold_pct:
        direction, breadth = "Upside", n_up / len(deviations) * 100.0
    elif average < -flat_threshold_pct:
        direction, breadth = "Downside", n_down / len(deviations) * 100.0
    else:
        direction, breadth = "Flat", n_flat / len(deviations) * 100.0

    return ConstituentBiasSignal(
        direction=direction,
        magnitude_bucket=_magnitude_bucket(abs(average)),
        average_deviation_pct=round(average, 3),
        breadth_pct=round(breadth, 1),
        n_up=n_up,
        n_down=n_down,
        n_flat=n_flat,
        n_total=len(snapshots),
        n_with_data=len(usable),
    )
