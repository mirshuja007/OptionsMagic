"""Volume-Weighted Average Price: the running cumulative(price * volume) /
cumulative(volume) benchmark intraday traders anchor entries/exits to.
"""
from __future__ import annotations


def vwap_series(points: list[tuple[float, int]]) -> list[float]:
    """``points``: (price, volume) in chronological order. Returns the
    running VWAP after each point (same length as input) — index 0 is just
    that minute's price, the last index is the full-session VWAP so far.
    """
    cum_pv = 0.0
    cum_volume = 0
    out: list[float] = []
    for price, volume in points:
        cum_pv += price * volume
        cum_volume += volume
        out.append(cum_pv / cum_volume if cum_volume > 0 else price)
    return out
