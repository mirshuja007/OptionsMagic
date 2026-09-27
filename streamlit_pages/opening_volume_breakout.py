"""Opening Volume Breakout: System 1 from the same rule-based-trading
webinar as Strangle Windows (Darshan Rathod / Multyfi) — trade only when
the first 5-minute candle's volume beats each of the last 3 days' opening
candles, then trade the break of its high or low. See
``app.analytics.opening_volume_breakout`` for the full rule set and every
place a genuinely ambiguous rule was confirmed rather than assumed.

Like Strangle Windows, this is a live paper-tracking dashboard, not a
backtester or an order-placing system: it resolves today's setup and
shows live levels, and lets you manually confirm your own fills so it can
track each symbol's daily trade count — every order is placed by you.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from streamlit_pages.common import AMBER, GREEN, RED, fmt, safe_call

POLL_SECONDS = 20


def render() -> None:
    st.title("Opening Volume Breakout")
    st.caption(
        "System 1: trade only when the first 5-minute candle's volume beats each of the last 3 days' "
        "opening candles, then trade the break of its high or low. See this page's module docstring / "
        "app.analytics.opening_volume_breakout for the full rule set and every confirmed interpretation."
    )
    st.warning(
        "This page tracks the rules; it does not place orders. Two source-material gaps worth knowing: "
        "the -0.4%-to-0% opening-bias band is undefined in the deck (resolved here, per confirmation, as "
        "\"both directions allowed\") and the deck's \"large stop\" / \"poor risk-reward\" skip conditions "
        "give no numeric threshold — this page shows the stop distance and the risk-reward achievable at "
        "the current price instead of guessing a cutoff it was never given. Educational reference only, "
        "not investment advice."
    )

    auto_refresh = st.toggle(f"Live refresh ({POLL_SECONDS}s)", value=True)
    if auto_refresh:
        _live_panel()
    else:
        _render(in_fragment=False)


@st.fragment(run_every=f"{POLL_SECONDS}s")
def _live_panel() -> None:
    _render(in_fragment=True)


def _render(in_fragment: bool) -> None:
    from app.core.timezone import IST
    from app.data.feed import get_active_provider

    live = get_active_provider() == "kite"
    badge = "🟢 refreshing live" if live else "🟠 refreshing (simulated data)"
    now = datetime.now(IST)
    st.caption(f"{badge} · now {now.strftime('%H:%M:%S')} IST · every {POLL_SECONDS}s")

    from app.analytics import opening_volume_breakout as ovb

    bias, bias_pct, bias_err = _market_bias(now)
    if bias_err:
        st.error(f"Couldn't compute the market bias filter: {bias_err}")
        return

    bcol1, bcol2 = st.columns(2)
    bcol1.metric("Market opening move (NIFTY)", f"{bias_pct:+.2f}%")
    bcol2.metric("Allowed directions today", "Both" if bias == "both" else "Short only")

    if now.time() >= ovb.NO_FRESH_TRADES_AFTER:
        st.info(
            f"Past {ovb.NO_FRESH_TRADES_AFTER.strftime('%H:%M')} — no fresh entries for the rest of "
            "today per the system's own flat-by rule."
        )

    for symbol in ovb.WATCHLIST:
        with st.expander(symbol, expanded=(symbol == ovb.BIAS_SYMBOL)):
            _symbol_panel(symbol, now, bias, in_fragment)


def _market_bias(now: datetime):
    from app.analytics import opening_volume_breakout as ovb
    from app.data.feed import generate_option_chain, minute_ohlc_series

    cache_key = f"ovb_bias_{now.date().isoformat()}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    bars, err = safe_call(minute_ohlc_series, ovb.BIAS_SYMBOL, now.date())
    if err or not bars:
        return None, None, err or "no data yet"
    if len(bars) < ovb.OPENING_CANDLE_MINUTES:
        return None, None, "waiting for the 9:15-9:20 opening candle to close"
    candle = ovb.aggregate_opening_candle(bars)

    chain, chain_err = safe_call(generate_option_chain, ovb.BIAS_SYMBOL)
    if chain_err or chain is None:
        return None, None, chain_err or "couldn't load the NIFTY chain for previous close"

    change_pct = (candle.close - chain.prev_close) / chain.prev_close * 100.0
    bias = ovb.opening_bias(change_pct)
    result = (bias, change_pct, None)
    st.session_state[cache_key] = result  # fixed for the day once the opening candle closes
    return result


def _cached_prior_volumes(symbol: str, today):
    from app.analytics import opening_volume_breakout as ovb
    from app.data.feed import minute_ohlc_series

    cache_key = f"ovb_priorvol_{symbol}_{today.isoformat()}"
    if cache_key in st.session_state:
        return st.session_state[cache_key], None

    volumes = []
    for day in ovb.prior_trading_days(today):
        bars, err = safe_call(minute_ohlc_series, symbol, day)
        if err or not bars:
            return None, err or f"no data for {day}"
        if len(bars) < ovb.OPENING_CANDLE_MINUTES:
            return None, f"incomplete opening-candle data for {day}"
        volumes.append(ovb.aggregate_opening_candle(bars).volume)

    st.session_state[cache_key] = volumes  # doesn't change intraday — fetched once per day
    return volumes, None


def _symbol_panel(symbol: str, now: datetime, bias: str, in_fragment: bool) -> None:
    from app.analytics import opening_volume_breakout as ovb
    from app.data.feed import minute_ohlc_series

    prior_volumes, prior_err = _cached_prior_volumes(symbol, now.date())
    if prior_err:
        st.error(f"Couldn't load prior-day volumes: {prior_err}")
        return

    bars, err = safe_call(minute_ohlc_series, symbol, now.date())
    if err or not bars:
        st.info(err or "No data yet today.")
        return
    if len(bars) < ovb.OPENING_CANDLE_MINUTES:
        st.info("Waiting for the 9:15-9:20 opening candle to close...")
        return

    candle = ovb.aggregate_opening_candle(bars)
    current_price = bars[-1][4]
    passes = ovb.volume_filter_passes(candle.volume, prior_volumes)

    cols = st.columns(3)
    cols[0].metric("Opening volume", f"{candle.volume:,}")
    cols[1].metric("Volume filter", "✅ Pass" if passes else "❌ Fail")
    cols[2].metric("Current price", fmt(current_price))
    st.caption(f"Prior 3 days' opening volume: {', '.join(f'{v:,}' for v in prior_volumes)}")

    if not passes:
        st.caption("No setup today — opening volume doesn't beat each of the prior 3 days' opening candles.")
        return

    st.caption(f"Opening candle (9:15-9:20): High {fmt(candle.high)} · Low {fmt(candle.low)}")

    trigger = ovb.trigger_status(current_price, candle)
    if trigger is None:
        st.caption("No breakout yet — price is inside the opening candle's range.")
        return

    if not ovb.direction_allowed(trigger, bias):
        st.warning(
            f"{trigger.capitalize()} breakout triggered, but today's bias only allows "
            f"{'short' if bias == 'short_only' else 'both'} trades — skip."
        )
        return

    levels = ovb.trade_levels(candle, trigger)
    rr_now = ovb.achievable_rr(current_price, levels.stop, levels.target_min)

    lcols = st.columns(3)
    lcols[0].metric("Direction", trigger.capitalize())
    lcols[1].metric("Stop", fmt(levels.stop))
    lcols[2].metric(
        "R:R entering now", f"{rr_now:.2f}",
        "below 1:2 — your call" if rr_now < ovb.MIN_RR else "at/above 1:2",
    )
    st.caption(f"Target — 1:2 {fmt(levels.target_min)} · 1:3 {fmt(levels.target_ideal)}")

    _entry_tracker(symbol, now, trigger, levels, in_fragment)


def _entry_tracker(strategy_symbol: str, now: datetime, direction: str, levels, in_fragment: bool) -> None:
    from app.analytics import opening_volume_breakout as ovb

    rerun_scope = "fragment" if in_fragment else "app"
    day = now.date().isoformat()
    count_key = f"ovb_{strategy_symbol}_{day}_trade_count"
    active_key = f"ovb_{strategy_symbol}_{day}_active"

    completed = st.session_state.get(count_key, 0)
    active = st.session_state.get(active_key)

    if active is None and completed >= ovb.DAILY_TRADE_LIMIT:
        st.info(f"Daily limit reached ({ovb.DAILY_TRADE_LIMIT} completed trades) — no more entries today.")
        return

    if active is None:
        st.markdown("**Position sizing** — risk a fixed % of capital, never a fixed quantity")
        pcol1, pcol2 = st.columns(2)
        capital = pcol1.number_input(
            "Capital (₹)", min_value=0.0, value=100_000.0, step=1_000.0, key=f"ovb_{strategy_symbol}_capital"
        )
        risk_pct = pcol2.number_input(
            "Risk per trade (%)", min_value=0.1, max_value=10.0, value=1.0, step=0.1,
            key=f"ovb_{strategy_symbol}_risk_pct",
        ) / 100.0
        size = ovb.position_size(capital, risk_pct, levels.entry, levels.stop)
        st.caption(
            f"Risk ₹{size.risk_amount:,.0f} · quantity {size.quantity} · capital deployed ₹{size.capital_deployed:,.0f}"
        )

        if st.button(f"Mark {direction} entered", key=f"ovb_{strategy_symbol}_enter_btn"):
            st.session_state[active_key] = {
                "direction": direction, "entry": levels.entry, "stop": levels.stop,
                "target_min": levels.target_min, "target_ideal": levels.target_ideal,
            }
            st.rerun(scope=rerun_scope)
        return

    st.success(
        f"Active {active['direction']} trade — entry {fmt(active['entry'])}, stop {fmt(active['stop'])}, "
        f"target {fmt(active['target_min'])}"
    )
    ccol1, ccol2, ccol3 = st.columns(3)
    if ccol1.button("Mark target hit", key=f"ovb_{strategy_symbol}_target_btn"):
        st.session_state[count_key] = completed + 1
        st.session_state[active_key] = None
        st.rerun(scope=rerun_scope)
    if ccol2.button("Mark SL hit", key=f"ovb_{strategy_symbol}_sl_btn"):
        st.session_state[count_key] = completed + 1
        st.session_state[active_key] = None
        st.rerun(scope=rerun_scope)
    if ccol3.button("Close flat (3:15pm)", key=f"ovb_{strategy_symbol}_flat_btn"):
        st.session_state[count_key] = completed + 1
        st.session_state[active_key] = None
        st.rerun(scope=rerun_scope)
