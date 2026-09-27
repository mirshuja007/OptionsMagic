"""Strangle Windows: a live paper-tracking dashboard for the "2 Hour
Trading Strategy" — 4 fixed-window NIFTY/SENSEX short strangles from a
rule-based-trading webinar. See ``app.analytics.strangle_system`` for the
full rule set and every place an ambiguous rule was confirmed rather than
assumed (SL basis, re-entry mechanics, DTE gating).

This is deliberately NOT a backtester (needs historical per-strike option
premiums — a separate, heavier data build) and NOT an order-placing
system (no Kite order calls anywhere here). It resolves today's
eligibility and strikes, shows live premiums, and lets you manually
confirm your own fills so it can track each leg's stop and its one
allowed re-entry — every order is placed by you, on your own broker
terminal, exactly as the source material describes.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from streamlit_pages.common import AMBER, GREEN, RED, fmt, safe_call

POLL_SECONDS = 20
_OVERLAP_KEYS = ("nifty_d5hj", "sensex_a5x")


def render() -> None:
    st.title("Strangle Windows")
    st.caption(
        "Live tracker for the “2 Hour Trading Strategy” — 4 fixed-window NIFTY/SENSEX short "
        "strangles, transcribed from a rule-based-trading webinar (see this page's module docstring / "
        "app.analytics.strangle_system for the full rule set)."
    )
    st.warning(
        "All four legs here are **naked short options — sold with no hedge leg**, per the source "
        "system. There is **no portfolio-level stop loss**; a bad day can run all four to their "
        "independent leg stops — size your lots for that outcome. This page tracks the rules; it "
        "does not place orders. Educational reference only, not investment advice."
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
    from app.core.timezone import IST
    from app.data.feed import get_active_provider

    live = get_active_provider() == "kite"
    badge = "🟢 refreshing live" if live else "🟠 refreshing (simulated data)"
    now = datetime.now(IST)
    st.caption(f"{badge} · now {now.strftime('%H:%M:%S')} IST · every {POLL_SECONDS}s")

    from app.analytics import strangle_system as ss

    states = {strategy.key: _strategy_card(strategy, now) for strategy in ss.STRATEGIES}

    if all(states.get(key) == "live" for key in _OVERLAP_KEYS):
        _overlap_margin_note(live)


def _strategy_card(strategy, now: datetime) -> str | None:
    from app.analytics import strangle_system as ss
    from app.data.feed import available_expiries, generate_option_chain
    from app.data.instruments import get_instrument

    st.subheader(strategy.label)

    expiries, err = safe_call(available_expiries, strategy.symbol)
    if err or not expiries:
        st.error(err or f"No expiries available for {strategy.symbol}.")
        return None
    expiry = expiries[0]
    dte = ss.dte_for(now.date(), expiry)
    eligible = ss.is_dte_eligible(strategy, dte)

    meta_cols = st.columns(4)
    meta_cols[0].metric("Window", f"{strategy.window_start.strftime('%H:%M')}–{strategy.window_end.strftime('%H:%M')}")
    meta_cols[1].metric("Strikes", strategy.strike_rule)
    meta_cols[2].metric("SL / leg", f"{strategy.sl_pct * 100:.0f}%")
    if strategy.dte_allowed is None:
        meta_cols[3].metric("DTE today", f"{dte}", "no DTE gate")
    else:
        meta_cols[3].metric("DTE today", f"{dte}", "eligible" if eligible else f"needs {sorted(strategy.dte_allowed)}")

    if not eligible:
        st.info(f"Not eligible today — DTE is {dte}; this strategy only trades on DTE {sorted(strategy.dte_allowed)}.")
        return None

    state = ss.window_status(now.time(), strategy.window_start, strategy.window_end)
    st.caption({"before": "🕒 Before window", "live": "🟢 Window live", "after": "⚪ Window closed"}[state])

    chain, chain_err = safe_call(generate_option_chain, strategy.symbol, expiry)
    if chain_err or chain is None:
        st.error(chain_err or f"Couldn't load the option chain for {strategy.symbol}.")
        return state

    instrument = get_instrument(strategy.symbol)
    call_strike, put_strike = ss.resolve_leg_strikes(chain.spot, instrument.strike_step, strategy.strike_rule)
    call_row = next((r for r in chain.rows if r.strike == call_strike), None)
    put_row = next((r for r in chain.rows if r.strike == put_strike), None)

    leg_cols = st.columns(2)
    with leg_cols[0]:
        _leg_widget(strategy, "CE", call_strike, call_row.call if call_row else None, now)
    with leg_cols[1]:
        _leg_widget(strategy, "PE", put_strike, put_row.put if put_row else None, now)

    return state


def _leg_widget(strategy, side_label: str, strike: float, leg, now: datetime) -> None:
    from app.analytics import strangle_system as ss

    st.markdown(f"**{side_label} {fmt(strike, 0)}**")
    if leg is None:
        st.error("Strike not found in the chain.")
        return
    st.caption(f"{leg.tradingsymbol or '—'} · LTP {fmt(leg.ltp)} · IV {leg.iv * 100:.1f}% · Δ {fmt(leg.greeks.delta, 3)}")

    key_prefix = f"strangle_{strategy.key}_{side_label}_{now.date().isoformat()}"
    entered_key = f"{key_prefix}_entered"
    entry_key = f"{key_prefix}_entry_premium"
    reentered_key = f"{key_prefix}_reentered"
    closed_key = f"{key_prefix}_closed"

    if st.session_state.get(closed_key, False):
        st.success("Leg closed for today.")
        return

    if not st.session_state.get(entered_key, False):
        st.number_input("Your entry premium", min_value=0.0, value=float(leg.ltp), step=0.05, key=entry_key)
        if st.button("Mark entered", key=f"{key_prefix}_enter_btn"):
            st.session_state[entered_key] = True
            st.rerun()
        return

    entry_premium = st.session_state[entry_key]
    status = ss.leg_status(entry_premium, leg.ltp, strategy.sl_pct)
    move_color = RED if status.pct_move >= 0 else GREEN
    st.markdown(
        f"Entry **{fmt(entry_premium)}** → now **{fmt(leg.ltp)}** "
        f"(<span style='color:{move_color}'>{status.pct_move * 100:+.1f}%</span>), SL at {fmt(status.sl_threshold)}",
        unsafe_allow_html=True,
    )

    if not status.sl_hit:
        st.success("🟢 Within stop")
        if st.button("Mark leg closed", key=f"{key_prefix}_close_btn"):
            st.session_state[closed_key] = True
            st.rerun()
        return

    st.error("🔴 SL hit — buy this leg back now")
    if st.session_state.get(reentered_key, False):
        st.info("Re-entry already used — this leg is done for today.")
        if st.button("Mark leg closed", key=f"{key_prefix}_close_btn2"):
            st.session_state[closed_key] = True
            st.rerun()
        return

    if ss.re_entry_ready(entry_premium, leg.ltp):
        st.markdown(
            f"<span style='color:{AMBER}'>Re-entry ready — premium has decayed back to your original "
            f"{fmt(entry_premium)}.</span>",
            unsafe_allow_html=True,
        )
        if st.button("Mark re-entered", key=f"{key_prefix}_reenter_btn"):
            st.session_state[reentered_key] = True
            st.rerun()
    else:
        st.caption(f"One re-entry allowed at your original entry price ({fmt(entry_premium)}) — not yet reached.")
        if st.button("Skip re-entry, leg done", key=f"{key_prefix}_skip_btn"):
            st.session_state[closed_key] = True
            st.rerun()


def _overlap_margin_note(live: bool) -> None:
    st.markdown("**1:30–2:30 PM overlap** — NIFTY D5HJ and SENSEX A5X are both live.")
    if not live:
        st.caption("Real margin lookup needs a Kite login (Research Mode) — unavailable on simulated data.")
        return

    from app.analytics import strangle_system as ss
    from app.data.feed import generate_option_chain
    from app.data.instruments import get_instrument
    from app.margin.kite_margin import fetch_basket_margin
    from app.strategy.legs import Leg, Side

    try:
        total = 0.0
        for symbol in ("NIFTY", "SENSEX"):
            instrument = get_instrument(symbol)
            chain = generate_option_chain(symbol)
            call_strike, put_strike = ss.resolve_leg_strikes(chain.spot, instrument.strike_step, "ATM")
            call_row = next(r for r in chain.rows if r.strike == call_strike)
            put_row = next(r for r in chain.rows if r.strike == put_strike)
            legs = [
                Leg(call_row.call.option_type, call_strike, Side.SHORT, 1, call_row.call.ltp, call_row.call.iv, tradingsymbol=call_row.call.tradingsymbol),
                Leg(put_row.put.option_type, put_strike, Side.SHORT, 1, put_row.put.ltp, put_row.put.iv, tradingsymbol=put_row.put.tradingsymbol),
            ]
            total += fetch_basket_margin(legs, instrument).total_margin
        st.metric("Approx. combined margin (NIFTY + SENSEX)", f"₹{total:,.0f}")
        st.caption("Sum of two separate broker margin lookups, not one true combined basket call — an approximation.")
    except Exception as exc:
        st.caption(f"Margin lookup unavailable right now: {exc}")
