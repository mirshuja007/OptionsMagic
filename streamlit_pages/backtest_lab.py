"""Backtest Lab: runs a strategy's entry/exit rules across many historical
trading days and tallies how it would have done — win rate, P&L,
drawdown — rather than only ever evaluating "what does today look like"
the way every other page in this app does.

Opening Volume Breakout is the first (and currently only) strategy wired
in here; see ``app.backtest.ovb_backtest`` for the engine and every
backtest-specific assumption it had to make that the live page never did
(same-bar stop/target tie-breaks, entry fill convention, forced end-of-day
close). This is a genuinely slow operation against live Kite data (one
Historical Data API call per symbol per day, cached per run but not across
runs) — hence the explicit "Run Backtest" button rather than recomputing on
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
        "This replays historical price/volume data through the strategy's own documented rules — it is not "
        "a guarantee of future results, and it makes a few backtest-specific assumptions the live page never "
        "had to (see app.backtest.ovb_backtest's module docstring: conservative same-bar stop/target "
        "tie-breaks, entry fills exactly at the candle's trigger level). Educational reference only, not "
        "investment advice."
    )

    from app.analytics import opening_volume_breakout as ovb

    strategy = st.selectbox("Strategy", ["Opening Volume Breakout (System 1)"])
    symbol = st.selectbox("Symbol", ovb.WATCHLIST)

    col1, col2, col3, col4 = st.columns(4)
    lookback = col1.number_input("Lookback (trading days)", min_value=5, max_value=250, value=30, step=5)
    capital = col2.number_input("Capital (₹)", min_value=0.0, value=100_000.0, step=1_000.0)
    risk_pct = col3.number_input("Risk per trade (%)", min_value=0.1, max_value=10.0, value=1.0, step=0.1) / 100.0
    target_label = col4.selectbox("Target", ["1:2 (min)", "1:3 (ideal)"])
    target_choice = "min" if target_label.startswith("1:2") else "ideal"

    if not st.button("Run Backtest", type="primary"):
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
    mcols = st.columns(5)
    mcols[0].metric("Trades", result.total_trades)
    mcols[1].metric("Win rate", f"{result.win_rate * 100:.1f}%", f"{result.wins}W / {result.losses}L")
    mcols[2].metric("Total P&L", fmt_currency(result.total_pnl))
    mcols[3].metric("Avg P&L / trade", fmt_currency(result.avg_pnl))
    mcols[4].metric("Max drawdown", fmt_currency(result.max_drawdown))

    skip_counts: dict[str, int] = {}
    for s in result.skipped_days:
        skip_counts[s.reason] = skip_counts.get(s.reason, 0) + 1
    skip_summary = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in skip_counts.items())
    st.caption(
        f"{len(result.skipped_days)} of {lookback} days had no trade"
        + (f" ({skip_summary})" if skip_summary else "")
        + "."
    )

    if result.trades:
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
