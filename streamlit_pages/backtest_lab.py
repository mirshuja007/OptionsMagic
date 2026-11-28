"""Backtest Lab: runs a strategy's entry/exit rules across many historical
trading days and tallies how it would have done — win rate, P&L,
drawdown — rather than only ever evaluating "what does today look like"
the way every other page in this app does.

Three strategies are wired in: Opening Volume Breakout
(``app.backtest.ovb_backtest``), the VIX + opening-gap put/call ratio
strategy (``app.backtest.vix_gap_ratio_backtest``), and a general,
user-assembled Custom strategy (``app.backtest.custom_strategy_backtest``)
built from the condition toolkit in ``app.analytics.conditions`` — see
each module's docstring for its own backtest-specific assumptions
(same-bar stop/target tie-breaks, entry fill conventions, how option
premiums get repriced when no real historical quote is resolvable). This
is a genuinely slow operation against live Kite data (one Historical
Data API call per symbol per day, cached per run but not across runs) —
hence the explicit "Run Backtest" button rather than recomputing on
every widget change.
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from streamlit_pages.common import GREEN, RED, dark_layout, fmt_currency


def render() -> None:
    st.title("Backtest Lab")
    st.caption(
        "Run a strategy's entry/exit rules across many historical trading days and see how it would have "
        "done — win rate, total/average P&L, drawdown — instead of only ever checking today's setup."
    )
    st.warning(
        "This replays historical price/volume data through each strategy's own documented rules — it is "
        "not a guarantee of future results, and every strategy here makes a few backtest-specific "
        "assumptions the live page never had to (see the relevant app.backtest module's docstring). "
        "Educational reference only, not investment advice."
    )

    strategy = st.selectbox(
        "Strategy",
        [
            "Opening Volume Breakout (System 1)",
            "VIX + Gap Put/Call Ratio (NIFTY weekly)",
            "Custom (Condition Builder)",
        ],
    )

    if strategy.startswith("Opening Volume Breakout"):
        _render_ovb()
    elif strategy.startswith("VIX + Gap"):
        _render_vix_gap_ratio()
    else:
        _render_custom()


def _render_ovb() -> None:
    from app.analytics import opening_volume_breakout as ovb

    symbol = st.selectbox("Symbol", ovb.WATCHLIST)

    col1, col2, col3, col4 = st.columns(4)
    lookback = col1.number_input("Lookback (trading days)", min_value=5, max_value=250, value=30, step=5)
    capital = col2.number_input("Capital (₹)", min_value=0.0, value=100_000.0, step=1_000.0)
    risk_pct = col3.number_input("Risk per trade (%)", min_value=0.1, max_value=10.0, value=1.0, step=0.1) / 100.0
    target_label = col4.selectbox("Target", ["1:2 (min)", "1:3 (ideal)"])
    target_choice = "min" if target_label.startswith("1:2") else "ideal"

    if not st.button("Run Backtest", type="primary", key="ovb_run"):
        return

    from app.backtest.ovb_backtest import run_backtest

    with st.spinner(f"Replaying the last {lookback} trading days for {symbol}..."):
        result, err = None, None
        try:
            result = run_backtest(
                symbol, lookback_trading_days=lookback, capital=capital, risk_pct=risk_pct,
                target_choice=target_choice,
            )
        except Exception as exc:  # noqa: BLE001 — surface any feed/auth error with context, not a raw traceback
            err = str(exc)

    if err:
        st.error(f"Backtest failed: {err}")
        return

    st.divider()
    st.markdown(f"**Results — {symbol}, last {lookback} trading days**")
    _summary_metrics(result)
    _skip_caption(result, lookback)

    if result.trades:
        _equity_chart(result)
        st.markdown("**Trade log**")
        rows = [
            {
                "Date": t.day.isoformat(),
                "Direction": t.direction.capitalize(),
                "Entry": f"{t.entry_time.strftime('%H:%M')} @ {t.entry_price:.2f}",
                "Exit": f"{t.exit_time.strftime('%H:%M')} @ {t.exit_price:.2f}",
                "Reason": t.exit_reason.replace("_", " "),
                "Qty": t.quantity,
                "P&L": fmt_currency(t.pnl_rupees),
            }
            for t in result.trades
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No trades fired in this window — the volume filter, bias filter, or trade-limit rules screened everything out.")


def _render_vix_gap_ratio() -> None:
    st.caption(
        "NIFTY weekly options only. Sells 3 lots + buys 1 lot on the gap's own side (the \"covered\" group), "
        "sells 1 naked lot on the opposite side — see app.analytics.vix_gap_ratio's module docstring for the "
        "full confirmed rule set (VIX > 15, gap 0-0.5%, SD = 30-day realized vol scaled to expiry, etc.)."
    )

    col1, col2 = st.columns(2)
    lookback = col1.number_input("Lookback (trading days)", min_value=5, max_value=250, value=30, step=5, key="vgr_lookback")
    lot_multiplier = col2.number_input("Lot multiplier (X)", min_value=1, max_value=20, value=1, step=1, key="vgr_lots")

    if not st.button("Run Backtest", type="primary", key="vgr_run"):
        return

    from app.backtest.vix_gap_ratio_backtest import run_backtest

    with st.spinner(f"Replaying the last {lookback} trading days for NIFTY..."):
        result, err = None, None
        try:
            result = run_backtest(lookback_trading_days=lookback, lot_multiplier=lot_multiplier)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)

    if err:
        st.error(f"Backtest failed: {err}")
        return

    st.divider()
    st.markdown(f"**Results — NIFTY, last {lookback} trading days**")
    _summary_metrics(result)
    _skip_caption(result, lookback)

    if result.trades:
        _equity_chart(result)
        st.markdown("**Trade log**")
        rows = []
        for t in result.trades:
            rows.append({
                "Date": t.day.isoformat(),
                "Dir": t.direction.capitalize(),
                "VIX": f"{t.vix:.1f}",
                "Gap%": f"{t.gap_pct:+.2f}%",
                "Naked": f"{t.naked.option_type.value} {t.naked.strike:.0f}: {t.naked.entry_premium:.2f}→{(t.naked.exit_premium or 0):.2f} ({t.naked.exit_reason})",
                "Covered short (x3)": f"{t.covered_short.option_type.value} {t.covered_short.strike:.0f}: {t.covered_short.entry_premium:.2f}→{(t.covered_short.exit_premium or 0):.2f} ({t.covered_short.exit_reason})",
                "Covered long (x1)": f"{t.covered_long.option_type.value} {t.covered_long.strike:.0f}: {t.covered_long.entry_premium:.2f}→{(t.covered_long.exit_premium or 0):.2f}",
                "P&L": fmt_currency(t.pnl_rupees),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No trades fired in this window — the VIX/gap entry gate screened out every day.")


def _render_custom() -> None:
    from app.analytics import opening_volume_breakout as ovb
    from app.analytics.conditions import AlwaysFalse, AlwaysTrue
    from app.analytics.custom_strategy import LegAction, StrikeSelection
    from app.core.black_scholes import OptionType
    from app.strategy.legs import Side
    from streamlit_pages.condition_builder import render_condition_set

    st.caption(
        "Assemble your own entry and exit rules from the condition toolkit (gap, VIX, ATM straddle %, "
        "option premium change, greeks, IV regime) and a single option leg to trade when they fire — "
        "see app.analytics.conditions and app.backtest.custom_strategy_backtest's module docstrings for "
        "exactly how each condition is evaluated and what's deliberately single-leg/same-day about this."
    )

    symbol = st.selectbox("Symbol", ovb.WATCHLIST, key="custom_symbol")
    lookback = st.number_input(
        "Lookback (trading days)", min_value=5, max_value=250, value=30, step=5, key="custom_lookback",
    )

    st.markdown("### Action — what to trade when the entry condition fires")
    a1, a2, a3, a4 = st.columns(4)
    option_type = OptionType.CALL if a1.selectbox("Option", ["Call", "Put"], key="custom_opt") == "Call" else OptionType.PUT
    side = Side.SHORT if a2.selectbox("Side", ["Sell", "Buy"], key="custom_side") == "Sell" else Side.LONG
    strike_method_label = a3.selectbox("Strike", ["ATM", "OTM by points", "OTM by SD"], key="custom_strike_method")
    lots = a4.number_input("Lots", min_value=1, max_value=50, value=1, step=1, key="custom_lots")

    if strike_method_label == "ATM":
        strike_selection = StrikeSelection("atm")
    elif strike_method_label == "OTM by points":
        points = st.number_input("Points OTM", min_value=0.0, value=100.0, step=10.0, key="custom_strike_points")
        strike_selection = StrikeSelection("otm_points", points)
    else:
        sd = st.number_input("SD multiple OTM", min_value=0.0, value=1.0, step=0.1, key="custom_strike_sd")
        strike_selection = StrikeSelection("otm_sd", sd)

    leg_action = LegAction(option_type, side, strike_selection, lots=int(lots))

    st.divider()
    entry_condition = render_condition_set(
        key_prefix="custom_entry",
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
        key_prefix="custom_exit",
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
    if not st.button("Run Backtest", type="primary", key="custom_run"):
        return

    from app.backtest.custom_strategy_backtest import run_backtest

    with st.spinner(f"Replaying the last {lookback} trading days for {symbol}..."):
        result, err = None, None
        try:
            result = run_backtest(
                symbol, leg_action, entry_condition, exit_condition, lookback_trading_days=lookback,
            )
        except Exception as exc:  # noqa: BLE001
            err = str(exc)

    if err:
        st.error(f"Backtest failed: {err}")
        return

    st.divider()
    st.markdown(f"**Results — {symbol}, last {lookback} trading days**")
    _summary_metrics(result)
    _skip_caption(result, lookback)

    if result.trades:
        _equity_chart(result)
        st.markdown("**Trade log**")
        rows = [
            {
                "Date": t.day.isoformat(),
                "Leg": f"{'Sell' if t.side == Side.SHORT else 'Buy'} {t.option_type.value} {t.strike:.0f} x{t.lots}",
                "Entry": f"{t.entry_time.strftime('%H:%M')} @ {t.entry_premium:.2f}",
                "Exit": (
                    f"{t.exit_time.strftime('%H:%M')} @ {t.current_premium:.2f}" if t.exit_time else "still open"
                ),
                "Reason": t.exit_reason.replace("_", " "),
                "P&L": fmt_currency(t.pnl_rupees),
            }
            for t in result.trades
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No trades fired in this window — the entry condition screened out every day.")


def _summary_metrics(result) -> None:
    mcols = st.columns(5)
    mcols[0].metric("Trades", result.total_trades)
    mcols[1].metric("Win rate", f"{result.win_rate * 100:.1f}%", f"{result.wins}W / {result.losses}L")
    mcols[2].metric("Total P&L", fmt_currency(result.total_pnl))
    mcols[3].metric("Avg P&L / trade", fmt_currency(result.avg_pnl))
    mcols[4].metric("Max drawdown", fmt_currency(result.max_drawdown))


def _skip_caption(result, lookback: int) -> None:
    skip_counts: dict[str, int] = {}
    for s in result.skipped_days:
        skip_counts[s.reason] = skip_counts.get(s.reason, 0) + 1
    skip_summary = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in skip_counts.items())
    st.caption(
        f"{len(result.skipped_days)} of {lookback} days had no trade"
        + (f" ({skip_summary})" if skip_summary else "")
        + "."
    )


def _equity_chart(result) -> None:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(range(1, len(result.equity_curve) + 1)), y=result.equity_curve,
            mode="lines+markers", name="Cumulative P&L",
            line=dict(color=GREEN if result.total_pnl >= 0 else RED, width=2),
        )
    )
    dark_layout(fig, title="Equity Curve", height=360, xaxis_title="Trade #", yaxis_title="Cumulative P&L (₹)")
    st.plotly_chart(fig, use_container_width=True)
