"""Research Mode: option chain, technical/OI analytics, and the Expiry
Outlook commentary — a Streamlit port of frontend/app/research/page.tsx and
its component tree (OiChart, IvSkewChart, StraddleDecayChart,
IntradayPriceChart, OptionChainTable, CommentaryBox), calling the backend's
analytics modules directly instead of over HTTP.

The data/render section below is an @st.fragment(run_every=...) — Streamlit
Cloud has no server-push mechanism of its own, so "live" here means the
same thing it means in the Next.js frontend: polling on an interval
(POLL_INTERVAL_MS there, POLL_SECONDS here), not a Kite Ticker websocket
subscription. The fragment reruns *only itself* on that timer, not the
whole page — the symbol/expiry selectors above it don't re-render or lose
focus every cycle.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from streamlit_pages.common import fmt, safe_call

GREEN = "#22c55e"
RED = "#ef4444"
AMBER = "#f59e0b"
POLL_SECONDS = 15


def _dark_layout(fig: go.Figure, **kwargs) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        **kwargs,
    )
    return fig


def render() -> None:
    from app.data.feed import available_expiries, get_active_provider
    from app.data.instruments import ALL_INSTRUMENTS

    st.title("Research Mode")

    provider = get_active_provider()
    live = provider == "kite"

    symbols = sorted(ALL_INSTRUMENTS.keys())
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        symbol = st.selectbox("Symbol", symbols, index=symbols.index("NIFTY") if "NIFTY" in symbols else 0)
    expiries, expiry_err = safe_call(available_expiries, symbol)
    with col2:
        if expiry_err or not expiries:
            st.selectbox("Expiry", ["—"], disabled=True)
            st.error(expiry_err or "No expiries available")
            return
        expiry = st.selectbox("Expiry", expiries, format_func=lambda d: d.strftime("%a, %d %b %Y"))
    with col3:
        st.write("")  # vertical spacer to align the toggle with the selectboxes
        auto_refresh = st.toggle(f"Live refresh ({POLL_SECONDS}s)", value=True)

    if auto_refresh:
        _live_panel(symbol, expiry)
    else:
        _render(symbol, expiry, live)


@st.fragment(run_every=f"{POLL_SECONDS}s")
def _live_panel(symbol: str, expiry) -> None:
    from app.data.feed import get_active_provider

    _render(symbol, expiry, get_active_provider() == "kite")


def _render(symbol: str, expiry, live: bool) -> None:
    from app.data.feed import generate_minute_series, generate_option_chain

    badge = "🟢 refreshing live" if live else "🟠 refreshing (simulated data)"
    st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')} · {badge} every {POLL_SECONDS}s")

    chain, chain_err = safe_call(generate_option_chain, symbol, expiry=expiry)
    if chain_err or chain is None:
        st.error(chain_err or "Couldn't load the option chain.")
        return

    from app.analytics import commentary as commentary_mod
    from app.analytics import max_pain as max_pain_mod
    from app.analytics import oi as oi_mod
    from app.analytics import pcr as pcr_mod
    from app.analytics import straddle as straddle_mod
    from app.analytics import volatility as volatility_mod
    from app.analytics import vwap as vwap_mod

    max_pain_result = max_pain_mod.compute_max_pain(chain)
    pcr_result = pcr_mod.compute_pcr(chain)
    oi_by_strike = oi_mod.multistrike_oi(chain)
    smart_oi = oi_mod.smart_oi_score(chain)
    gex = oi_mod.gamma_exposure(chain)
    atm_iv_value = volatility_mod.atm_iv(chain)
    skew = volatility_mod.volatility_skew(chain)
    grid = volatility_mod.iv_grid(chain)
    straddle_curve = straddle_mod.premium_decay_curve(chain)
    atm_straddle = straddle_mod.atm_straddle(chain)
    sr = commentary_mod.support_resistance(chain)
    band = commentary_mod.expiry_band_probability(chain.spot, atm_iv_value, chain.time_to_expiry_years)

    oi_change_available = not live

    series, series_err = safe_call(generate_minute_series, symbol)
    vwap_value = None
    if series and not series_err:
        vwaps = vwap_mod.vwap_series([(p, v) for _, p, v in series])
        if vwaps:
            vwap_value = round(vwaps[-1], 2)
        fig = go.Figure()
        times = [t.strftime("%H:%M") for t, _, _ in series]
        fig.add_trace(go.Scatter(x=times, y=[p for _, p, _ in series], name="Spot", line=dict(color=GREEN, width=2)))
        fig.add_trace(go.Scatter(x=times, y=vwaps, name="VWAP", line=dict(color=AMBER, width=1.5, dash="dash")))
        _dark_layout(fig, title=f"{symbol} — Intraday Spot Price vs. VWAP", height=380)
        st.plotly_chart(fig, use_container_width=True)
    elif series_err:
        st.info(f"Intraday chart unavailable: {series_err}")

    change = chain.spot - chain.prev_close
    change_pct = (change / chain.prev_close * 100) if chain.prev_close else 0.0
    sign = "+" if change >= 0 else ""

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Spot", fmt(chain.spot), f"{sign}{fmt(change)} ({sign}{fmt(change_pct)}%)")
    c2.metric("Expiry", chain.expiry.strftime("%a, %d %b"), chain.expiry.isoformat())
    c3.metric("Max Pain", f"{max_pain_result.max_pain_strike:.0f}")
    c4.metric("PCR (OI)", fmt(pcr_result.pcr_oi))
    c5.metric("ATM IV", f"{atm_iv_value * 100:.1f}%")

    d1, d2, d3, d4 = st.columns(4)
    if oi_change_available:
        d1.metric("Smart OI Bias", smart_oi["bias"], f"score {smart_oi['score']:.2f}")
    else:
        d1.metric("Smart OI Bias", "n/a", "OI-change unavailable on live feed")
    d2.metric("Gamma Exposure (GEX)", gex["regime"].replace("_", " "), f"net {gex['net_gex'] / 1e7:.2f} Cr")
    d3.metric("Volatility Skew (25d)", fmt(skew["skew"], 4))
    if vwap_value is not None:
        diff = chain.spot - vwap_value
        d4.metric("VWAP", fmt(vwap_value), "spot at VWAP" if abs(diff) < 0.01 else f"spot {'above' if diff > 0 else 'below'}")
    else:
        d4.metric("VWAP", "—")

    # --- Expiry Outlook commentary --------------------------------------
    st.subheader("Expiry Outlook")
    pcr_lean = "bullish (more put OI than call OI)" if pcr_result.pcr_oi > 1 else "bearish (more call OI than put OI)"
    lines = [
        f"**{symbol}** is trading at **{fmt(chain.spot)}**, "
        f"{'up' if change >= 0 else 'down'} **{fmt(abs(change_pct))}%** from the previous close of {fmt(chain.prev_close)}. "
        f"OI-based support sits at **{sr.support_strike:.0f}** (put OI {sr.support_put_oi:,}), "
        f"with resistance at **{sr.resistance_strike:.0f}** (call OI {sr.resistance_call_oi:,}).",
    ]
    if oi_change_available:
        lines.append(
            f"Smart OI flow reads **{smart_oi['bias']}** (score {smart_oi['score']:.2f}), "
            f"and the OI-weighted PCR of {fmt(pcr_result.pcr_oi)} leans {pcr_lean}."
        )
    else:
        lines.append(
            f"OI-change-based signals (Smart OI, buildup) aren't available on the live feed — "
            f"PCR (OI) alone reads {fmt(pcr_result.pcr_oi)}, leaning {'bullish' if pcr_result.pcr_oi > 1 else 'bearish'}."
        )
    if vwap_value is None:
        lines.append("VWAP isn't available yet for today's session.")
    else:
        diff = chain.spot - vwap_value
        if abs(diff) < 0.01:
            lines.append(f"Spot is trading right at the session VWAP of {fmt(vwap_value)}.")
        else:
            lines.append(
                f"Spot is trading {'above' if diff > 0 else 'below'} the session VWAP of {fmt(vwap_value)} "
                f"by {fmt(abs(diff))} points, a mildly {'bullish' if diff > 0 else 'bearish'} intraday signal."
            )
    lines.append(
        f"Max Pain stands at **{max_pain_result.max_pain_strike:.0f}** — option writers are collectively "
        "positioned for the index to settle near this level into expiry."
    )
    days_to_expiry = chain.time_to_expiry_years * 365
    band_pct_label = f"{band['band_pct'] * 100:.1f}"
    prob_text = "not defined (no time value remaining)" if band["probability"] is None else f"**{band['probability'] * 100:.1f}%**"
    lines.append(
        f"Based on an ATM IV of {atm_iv_value * 100:.1f}% and {days_to_expiry:.1f} days to expiry, the "
        f"model-estimated probability of {symbol} settling within +{band_pct_label}%/-{band_pct_label}% of spot "
        f"({fmt(band['lower'])} – {fmt(band['upper'])}) at expiry is {prob_text}."
    )
    st.markdown("\n\n".join(lines))

    # --- MultiStrike Open Interest ---------------------------------------
    st.subheader("MultiStrike Open Interest")
    oi_fig = go.Figure()
    strikes = [s.strike for s in oi_by_strike]
    oi_fig.add_trace(go.Bar(x=strikes, y=[s.call_oi for s in oi_by_strike], name="Call OI", marker_color=GREEN))
    oi_fig.add_trace(go.Bar(x=strikes, y=[s.put_oi for s in oi_by_strike], name="Put OI", marker_color=RED))
    _dark_layout(oi_fig, barmode="group", height=340)
    st.plotly_chart(oi_fig, use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        iv_fig = go.Figure()
        iv_fig.add_trace(go.Scatter(x=[g.strike for g in grid], y=[g.call_iv * 100 for g in grid], name="Call IV", line=dict(color=GREEN, width=2)))
        iv_fig.add_trace(go.Scatter(x=[g.strike for g in grid], y=[g.put_iv * 100 for g in grid], name="Put IV", line=dict(color=RED, width=2)))
        _dark_layout(iv_fig, title="Implied Volatility Skew", height=320)
        st.plotly_chart(iv_fig, use_container_width=True)
    with col_b:
        decay_fig = go.Figure()
        decay_fig.add_trace(
            go.Scatter(
                x=list(range(len(straddle_curve))),
                y=[p["combined_premium"] for p in straddle_curve],
                name="Combined Premium",
                line=dict(color=AMBER, width=2),
            )
        )
        _dark_layout(decay_fig, title=f"ATM Straddle Premium Decay ({atm_straddle.strike:.0f})", height=320, xaxis_title="Today -> Expiry")
        st.plotly_chart(decay_fig, use_container_width=True)

    # --- Option chain table -----------------------------------------------
    st.subheader("Option Chain")
    atm_strike = min(chain.rows, key=lambda r: abs(r.strike - chain.spot)).strike
    rows = []
    for row in chain.rows:
        rows.append(
            {
                "Call OI": row.call.oi,
                "Call ChgOI": row.call.oi_change,
                "Call Vol": row.call.volume,
                "Call IV%": round(row.call.iv * 100, 1),
                "Call Delta": round(row.call.greeks.delta, 2),
                "Call LTP": row.call.ltp,
                "Strike": row.strike,
                "Put LTP": row.put.ltp,
                "Put Delta": round(row.put.greeks.delta, 2),
                "Put IV%": round(row.put.iv * 100, 1),
                "Put Vol": row.put.volume,
                "Put ChgOI": row.put.oi_change,
                "Put OI": row.put.oi,
            }
        )
    df = pd.DataFrame(rows)

    def _highlight_atm(row):
        return ["background-color: rgba(34,197,94,0.12)" if row["Strike"] == atm_strike else "" for _ in row]

    st.dataframe(
        df.style.apply(_highlight_atm, axis=1).format(precision=2),
        use_container_width=True,
        hide_index=True,
        height=520,
    )
