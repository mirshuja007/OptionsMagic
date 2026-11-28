"""Custom Strategy (Live): the automated paper-tracker for a user-assembled
single-leg strategy built from the condition toolkit
(``app.analytics.conditions``) and ``app.backtest.custom_strategy_backtest``
— the live counterpart to Backtest Lab's "Custom (Condition Builder)"
strategy, reusing the exact same condition-row widgets
(``streamlit_pages.condition_builder``) so there's one place that knows
how to turn rows into a ``Condition`` tree.

Same automated, no-manual-click design as the VIX + Gap Ratio live page:
``custom_strategy_backtest.live_status`` runs the same ``simulate_day``
a backtest uses, fed today's bars so far — one rule engine behind both.
The condition/action widgets are configured once, outside the
auto-refreshing fragment; only the live status re-evaluates on each
refresh tick, against whatever was last configured.
"""
from __future__ import annotations

import streamlit as st

from streamlit_pages.common import fmt_currency, safe_call

POLL_SECONDS = 20


def render() -> None:
    from app.analytics import opening_volume_breakout as ovb
    from app.analytics.conditions import AlwaysFalse, AlwaysTrue
    from app.analytics.custom_strategy import LegAction, StrikeSelection
    from app.core.black_scholes import OptionType
    from app.strategy.legs import Side
    from streamlit_pages.condition_builder import render_condition_set

    st.title("Custom Strategy (Live)")
    st.caption(
        "Automated paper-tracker for a custom single-leg strategy assembled from your own entry/exit "
        "conditions. No manual confirmation — re-evaluated from live (or simulated) data on every refresh."
    )
    st.warning(
        "Paper only — nothing here places a real order. Known simplifications (see "
        "app.backtest.custom_strategy_backtest's module docstring): option premiums come from a "
        "Black-Scholes reprice, the entry condition is checked once at the 9:15-9:20 opening candle "
        "(not continuously re-armed), and whatever's open is force-closed at 3:15pm regardless of the "
        "exit condition. Educational reference only, not investment advice."
    )

    symbol = st.selectbox("Symbol", ovb.WATCHLIST, key="custom_live_symbol")

    st.markdown("### Action — what to trade when the entry condition fires")
    a1, a2, a3, a4 = st.columns(4)
    option_type = (
        OptionType.CALL if a1.selectbox("Option", ["Call", "Put"], key="custom_live_opt") == "Call"
        else OptionType.PUT
    )
    side = Side.SHORT if a2.selectbox("Side", ["Sell", "Buy"], key="custom_live_side") == "Sell" else Side.LONG
    strike_method_label = a3.selectbox("Strike", ["ATM", "OTM by points", "OTM by SD"], key="custom_live_strike_method")
    lots = a4.number_input("Lots", min_value=1, max_value=50, value=1, step=1, key="custom_live_lots")

    if strike_method_label == "ATM":
        strike_selection = StrikeSelection("atm")
    elif strike_method_label == "OTM by points":
        points = st.number_input("Points OTM", min_value=0.0, value=100.0, step=10.0, key="custom_live_strike_points")
        strike_selection = StrikeSelection("otm_points", points)
    else:
        sd = st.number_input("SD multiple OTM", min_value=0.0, value=1.0, step=0.1, key="custom_live_strike_sd")
        strike_selection = StrikeSelection("otm_sd", sd)

    leg_action = LegAction(option_type, side, strike_selection, lots=int(lots))

    st.divider()
    entry_condition = render_condition_set(
        key_prefix="custom_live_entry",
        title="Entry conditions",
        help_text=(
            "Checked once, right when the 9:15-9:20 opening candle closes. No rows = enter every day "
            "(no screener). \"Option premium change\" here should reference today's open or yesterday's "
            "close, not entry — there's no position yet to compare against at this point."
        ),
        premium_references=["day_open", "prev_close"],
        default_if_empty=AlwaysTrue(),
    )

    st.divider()
    exit_condition = render_condition_set(
        key_prefix="custom_live_exit",
        title="Exit conditions",
        help_text=(
            "Checked every minute after entry; whatever's still open gets flattened at 3:15pm regardless. "
            "No rows = ride to that flat-by cutoff with no condition-based exit at all. \"Option premium "
            "change\" here normally references entry (e.g. entry, >=, 100 = close once the premium has "
            "doubled from your own fill)."
        ),
        premium_references=["entry", "day_open", "prev_close"],
        default_if_empty=AlwaysFalse(),
    )

    st.divider()
    auto_refresh = st.toggle(f"Live refresh ({POLL_SECONDS}s)", value=True, key="custom_live_autorefresh")
    if auto_refresh:
        _live_panel(symbol, leg_action, entry_condition, exit_condition)
    else:
        _render(symbol, leg_action, entry_condition, exit_condition)


@st.fragment(run_every=f"{POLL_SECONDS}s")
def _live_panel(symbol, leg_action, entry_condition, exit_condition) -> None:
    _render(symbol, leg_action, entry_condition, exit_condition)


def _render(symbol, leg_action, entry_condition, exit_condition) -> None:
    from datetime import datetime

    from app.core.timezone import IST
    from app.data.feed import get_active_provider, india_vix
    from app.strategy.legs import Side

    live = get_active_provider() == "kite"
    badge = "🟢 refreshing live" if live else "🟠 refreshing (simulated data)"
    now = datetime.now(IST)
    st.caption(f"{badge} · now {now.strftime('%H:%M:%S')} IST · every {POLL_SECONDS}s")

    vix, vix_err = safe_call(india_vix)
    st.metric("India VIX", f"{vix:.2f}" if vix is not None else "—")
    if vix_err:
        st.error(f"Couldn't read India VIX: {vix_err}")
        return

    from app.backtest.custom_strategy_backtest import live_status

    try:
        trade, reason = live_status(symbol, leg_action, entry_condition, exit_condition)
    except Exception as exc:  # noqa: BLE001 — surface any feed/auth error with context, not a raw traceback
        st.error(f"Couldn't evaluate today's setup: {exc}")
        return

    if trade is None:
        st.info(f"No position today — {reason}.")
        return

    st.divider()
    side_word = "Sell" if trade.side == Side.SHORT else "Buy"
    status_word = "OPEN" if trade.is_open else "CLOSED"
    st.markdown(f"**Today's position — {side_word} {trade.option_type.value} {trade.strike:.0f} x{trade.lots}, {status_word}**")

    mcols = st.columns(4)
    mcols[0].metric("Entry time", trade.entry_time.strftime("%H:%M"))
    mcols[1].metric("Entry premium", f"{trade.entry_premium:.2f}")
    current_label = "Current premium" if trade.is_open else "Exit premium"
    mcols[2].metric(current_label, f"{trade.current_premium:.2f}")
    mcols[3].metric("Status", "Open" if trade.is_open else trade.exit_reason.replace("_", " ").title())

    st.markdown(f"**P&L: {fmt_currency(trade.pnl_rupees)}**" + (" (live mark-to-market)" if trade.is_open else " (realized)"))
