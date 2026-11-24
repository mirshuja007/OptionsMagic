"""VIX + Gap Ratio (Live): the automated paper-tracker for the user-designed
NIFTY weekly-options strategy in ``app.analytics.vix_gap_ratio`` /
``app.backtest.vix_gap_ratio_backtest``.

Unlike Strangle Windows and Opening Volume Breakout, this page asks for no
manual "Mark entered" / "Mark target hit" clicks — it was explicitly
requested as automated. ``vix_gap_ratio_backtest.live_status`` runs the
exact same ``simulate_day`` function a backtest uses, fed today's bars so
far instead of a complete historical day, so there is exactly one rule
engine behind both backtest and live — they cannot quietly drift apart.
"""
from __future__ import annotations

import streamlit as st

from streamlit_pages.common import fmt, fmt_currency, safe_call

POLL_SECONDS = 20


def render() -> None:
    st.title("VIX + Gap Ratio (Live)")
    st.caption(
        "Automated paper-tracker for the user-designed VIX + opening-gap put/call ratio strategy on NIFTY "
        "weekly options. No manual confirmation — this page decides entry/exit itself from live (or "
        "simulated) data and shows the result, re-evaluated on every refresh."
    )
    st.warning(
        "Paper only — nothing here places a real order. Known simplifications (see "
        "app.backtest.vix_gap_ratio_backtest's module docstring): option premiums are Black-Scholes "
        "reprices using realized volatility as a stand-in IV, not real historical/live option quotes; "
        "stops/target are checked at each minute's close, not intrabar extremes. Educational reference "
        "only, not investment advice."
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
    from datetime import datetime

    from app.core.timezone import IST
    from app.data.feed import get_active_provider, india_vix

    live = get_active_provider() == "kite"
    badge = "🟢 refreshing live" if live else "🟠 refreshing (simulated data)"
    now = datetime.now(IST)
    st.caption(f"{badge} · now {now.strftime('%H:%M:%S')} IST · every {POLL_SECONDS}s")

    vix, vix_err = safe_call(india_vix)
    vcol1, vcol2 = st.columns(2)
    vcol1.metric("India VIX", f"{vix:.2f}" if vix is not None else "—")
    vcol2.metric("Entry gate", "VIX > 15 required")
    if vix_err:
        st.error(f"Couldn't read India VIX: {vix_err}")
        return

    from app.backtest.vix_gap_ratio_backtest import live_status

    try:
        trade, reason = live_status()
    except Exception as exc:  # noqa: BLE001 — surface any feed/auth error with context, not a raw traceback
        st.error(f"Couldn't evaluate today's setup: {exc}")
        return

    if trade is None:
        st.info(f"No position today — {reason}.")
        return

    st.divider()
    status_word = "OPEN" if trade.is_open else "CLOSED"
    st.markdown(f"**Today's position — {trade.direction.upper()} structure, {status_word}**")
    mcols = st.columns(4)
    mcols[0].metric("VIX at entry", f"{trade.vix:.2f}")
    mcols[1].metric("Gap", f"{trade.gap_pct:+.2f}%")
    mcols[2].metric("Expected move (1 SD)", f"{trade.expected_move_pct * 100:.2f}%")
    mcols[3].metric("IV used", f"{trade.iv_used * 100:.1f}%")

    st.markdown(f"**P&L: {fmt_currency(trade.pnl_rupees)}**" + (" (live mark-to-market)" if trade.is_open else " (realized)"))

    for label, leg, lots in (
        ("Naked short (1 lot)", trade.naked, 1),
        ("Covered short (3 lots)", trade.covered_short, 3),
        ("Covered long / hedge (1 lot)", trade.covered_long, 1),
    ):
        with st.container(border=True):
            lcol1, lcol2, lcol3, lcol4 = st.columns(4)
            lcol1.metric("Leg", f"{leg.option_type.value} {leg.strike:.0f}")
            lcol2.metric("Entry premium", fmt(leg.entry_premium))
            current_label = "Current premium" if leg.is_open else "Exit premium"
            lcol3.metric(current_label, fmt(leg.current_premium))
            lcol4.metric("Status", "Open" if leg.is_open else leg.exit_reason.replace("_", " ").title())
            st.caption(label)
