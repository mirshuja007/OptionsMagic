"""Futures Monitor: live NIFTY and SENSEX index-futures readings — current
price, change vs. previous close, and the session's high/low range.

Replaced an earlier "CAS Monitor" (SEBI's Closing Auction Session, live on
NSE/BSE from Aug 3, 2026) that tracked 49 individual stocks' prices
relative to their own 3:00-3:15pm CAS reference price. That measure has a
real blind spot: on 2026-09-03, SENSEX fell as much as ~1600 points
intraday before recovering to close down ~400 — and because most of the
stock-level reference prices got set only after most of the drop had
already happened, the recovery in the final minutes read as "upward" on
the old signal even though the day was still deeply negative overall. The
signal was answering "how did the last 20 minutes go relative to a
baseline set near the bottom," not "how did today go" — a real gap
between what it measured and what a glance at it suggested, confirmed
directly by the user hitting it live and asking for this rebuild.

Index futures avoid that blind spot entirely: no reference-window
baseline to distort the read, and the day's high/low range alone would
have shown that -1600-to-400 swing at a glance. One factual note worth
being clear-eyed about: NIFTY/SENSEX futures don't go through CAS at
all — CAS is a per-stock mechanism for Category I (F&O-eligible) stocks
only, indices and their futures are untouched by it. This page is a
better *index-direction* gauge than the old one, but it isn't itself
reading anything CAS-specific — see ``app.data.kite_feed.futures_snapshot``
/ ``app.data.mock_feed.futures_snapshot`` for the data source.
"""
from __future__ import annotations

from datetime import datetime, time as dtime

import plotly.graph_objects as go
import streamlit as st

from streamlit_pages.common import GREEN, RED, dark_layout, fmt, safe_call

POLL_SECONDS = 15

# SEBI's Closing Auction Session reference window on NSE/BSE — highlighted
# on the chart since it's the specific window the old CAS Monitor's signal
# was blind to (see the module docstring above).
CAS_WINDOW_START = dtime(15, 0)
CAS_WINDOW_END = dtime(15, 15)


def render() -> None:
    st.title("Futures Monitor")
    st.caption(
        "Live NIFTY and SENSEX index-futures readings: current price, change vs. previous close, and the "
        "session's high/low range. Replaced the earlier stock-level CAS Monitor, whose reference-price-"
        "relative signal could read \"upward\" during a sharp late-session recovery even on a day that "
        "closed deeply negative overall — see this page's module docstring for the specific 2026-09-03 case "
        "that prompted the rebuild."
    )

    auto_refresh = st.toggle(f"Live refresh ({POLL_SECONDS}s)", value=True)
    if auto_refresh:
        _live_panel()
    else:
        _render()


@st.fragment(run_every=f"{POLL_SECONDS}s")
def _live_panel() -> None:
    _render()


def _render() -> None:
    from app.data.feed import get_active_provider

    live = get_active_provider() == "kite"
    badge = "🟢 refreshing live" if live else "🟠 refreshing (simulated data)"
    st.caption(badge)

    col1, col2 = st.columns(2)
    with col1:
        _futures_panel("NIFTY", "NIFTY Futures")
    with col2:
        _futures_panel("SENSEX", "SENSEX Futures")


def _futures_panel(symbol: str, label: str) -> None:
    from app.data.feed import futures_snapshot

    st.subheader(label)
    snap, err = safe_call(futures_snapshot, symbol)
    if err or not snap:
        st.error(err or f"Couldn't load a futures snapshot for {symbol}.")
        return

    color = GREEN if snap["change_pts"] >= 0 else RED
    arrow = "▲" if snap["change_pts"] >= 0 else "▼"
    st.markdown(
        f"<span style='font-size:2rem;font-weight:600'>{fmt(snap['last_price'])}</span> "
        f"<span style='color:{color};font-size:1.1rem'>{arrow} {fmt(abs(snap['change_pts']))} "
        f"({snap['change_pct']:+.2f}%)</span>",
        unsafe_allow_html=True,
    )
    st.caption(f"{snap['tradingsymbol']} · expiry {snap['expiry']}")

    st.metric("Prev Close", fmt(snap["prev_close"]))
    st.markdown(
        f"**Day's range:** {fmt(snap['day_low'])} – {fmt(snap['day_high'])} "
        f"({fmt(snap['day_high'] - snap['day_low'])} points top-to-bottom)"
    )
    st.caption(
        "The swing a reference-price-relative signal could miss entirely — this shows both extremes directly."
    )

    _futures_chart(symbol, label)


def _futures_chart(symbol: str, label: str) -> None:
    from app.data.feed import futures_minute_series

    series, err = safe_call(futures_minute_series, symbol)
    if err or not series:
        st.info(f"Intraday chart unavailable: {err or 'no data yet'}")
        return

    times = [t for t, _, _ in series]
    prices = [p for _, p, _ in series]
    day_open, day_close = prices[0], prices[-1]
    line_color = GREEN if day_close >= day_open else RED

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=times, y=prices, mode="lines", name=label, line=dict(color=line_color, width=2)))

    session_date = times[0].date()
    cas_start = _combine(session_date, CAS_WINDOW_START)
    cas_end = _combine(session_date, CAS_WINDOW_END)
    if cas_start <= times[-1]:
        fig.add_vrect(
            x0=cas_start,
            x1=min(cas_end, times[-1]),
            fillcolor="rgba(245, 158, 11, 0.15)",
            line_width=0,
            annotation_text="CAS window (3:00–3:15pm)",
            annotation_position="top left",
            annotation=dict(font_size=10),
        )

    dark_layout(fig, title=f"{label} — Intraday Price", height=320, xaxis_title="Time", yaxis_title="Price")
    st.plotly_chart(fig, use_container_width=True, key=f"futures_chart_{symbol}")
    st.caption(
        f"Live-updating every {POLL_SECONDS}s as new minute bars complete — Streamlit Cloud has no server-push "
        "mechanism, so this is high-frequency polling, not a raw websocket tick stream. The amber band marks "
        "the 3:00–3:15pm CAS reference window."
    )


def _combine(session_date, t: dtime) -> datetime:
    return datetime.combine(session_date, t)
