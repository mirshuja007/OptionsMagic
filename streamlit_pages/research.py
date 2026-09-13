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

from streamlit_pages.common import AMBER, GREEN, RED, dark_layout, fmt, safe_call
from streamlit_pages.kite_login import render_kite_login_panel

POLL_SECONDS = 15


def render() -> None:
    from app.data.feed import available_expiries, get_active_provider
    from app.data.instruments import ALL_INSTRUMENTS

    st.title("Research Mode")
    render_kite_login_panel()

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
        dark_layout(fig, title=f"{symbol} — Intraday Spot Price vs. VWAP", height=380)
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

    _dsrd_panel(symbol, chain, live)

    # --- MultiStrike Open Interest ---------------------------------------
    st.subheader("MultiStrike Open Interest")
    oi_fig = go.Figure()
    strikes = [s.strike for s in oi_by_strike]
    oi_fig.add_trace(go.Bar(x=strikes, y=[s.call_oi for s in oi_by_strike], name="Call OI", marker_color=GREEN))
    oi_fig.add_trace(go.Bar(x=strikes, y=[s.put_oi for s in oi_by_strike], name="Put OI", marker_color=RED))
    dark_layout(oi_fig, barmode="group", height=340)
    st.plotly_chart(oi_fig, use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        iv_fig = go.Figure()
        iv_fig.add_trace(go.Scatter(x=[g.strike for g in grid], y=[g.call_iv * 100 for g in grid], name="Call IV", line=dict(color=GREEN, width=2)))
        iv_fig.add_trace(go.Scatter(x=[g.strike for g in grid], y=[g.put_iv * 100 for g in grid], name="Put IV", line=dict(color=RED, width=2)))
        dark_layout(iv_fig, title="Implied Volatility Skew", height=320)
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
        dark_layout(decay_fig, title=f"ATM Straddle Premium Decay ({atm_straddle.strike:.0f})", height=320, xaxis_title="Today -> Expiry")
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


# --- DSRD: Direction / Support / Resistance / Delta -----------------------
# Weekly-expiry credit-spread checklist, scoped to NIFTY/SENSEX per user
# request — the delta band (0.07-0.15) and Strategy Matrix were given
# specifically for weekly index options, not stocks/commodities with
# different expiry cadences. See app.analytics.dsrd for the full framework
# and app.analytics.technicals for the underlying indicator math.
DSRD_SYMBOLS = {"NIFTY", "SENSEX"}
DSRD_HISTORY_DAYS = 500  # ~2 years of daily bars — enough for the monthly S/R lookback


def _dsrd_panel(symbol: str, chain, live: bool) -> None:
    if symbol not in DSRD_SYMBOLS:
        return

    from app.analytics import dsrd
    from app.analytics import technicals as tech
    from app.data.feed import daily_series

    st.subheader("Options Sell Strategy (DSRD)")
    st.caption(
        "Direction / Support / Resistance / Delta — a weekly-expiry credit-spread checklist built from "
        "dual-timeframe RSI, multi-timeframe support/resistance, Bollinger squeeze, RSI divergence, and "
        "candlestick reversal patterns at a level. Every reading below traces to a specific indicator or "
        "the live option chain — the conviction score is a documented point system (see module docstring), "
        "not a validated predictive model."
    )
    if not live:
        st.warning(
            "Running on simulated data (no Kite login) — every number below is a random-walk stand-in, "
            "not real price history. Log in to Kite (see the panel above) for real support/resistance "
            "levels and RSI readings before acting on this."
        )

    raw_bars, err = safe_call(daily_series, symbol, DSRD_HISTORY_DAYS)
    if err or not raw_bars:
        st.error(err or f"Couldn't load daily history for {symbol}.")
        return

    bars = [tech.DailyBar(d, o, h, l, c, v) for d, o, h, l, c, v in raw_bars]
    daily_closes = [b.close for b in bars]
    weekly_closes = [b.close for b in tech.resample_weekly(bars)]

    direction = dsrd.direction_signal(daily_closes, weekly_closes)
    sr = dsrd.multi_timeframe_support_resistance(bars, chain.spot, direction.bias)
    signals = dsrd.confirmation_signals(bars, sr["daily"].support, sr["daily"].resistance)
    conviction = dsrd.score_conviction(direction, signals)
    # Weekly S/R for strike alignment — matches the trade's own expiry horizon.
    strikes = dsrd.select_sell_strikes(chain, sr["weekly"].support, sr["weekly"].resistance)
    checklist = dsrd.build_checklist(direction, sr, strikes)

    dcol1, dcol2, dcol3 = st.columns(3)
    dcol1.metric("Weekly RSI(14)", fmt(direction.weekly_rsi) if direction.weekly_rsi is not None else "n/a", direction.weekly_zone)
    dcol2.metric("Daily RSI(14)", fmt(direction.daily_rsi) if direction.daily_rsi is not None else "n/a", direction.daily_zone)
    dcol3.metric(direction.condition, direction.strategy)

    st.markdown("**Multi-timeframe support / resistance**")
    sr_rows = [
        {
            "Timeframe": tf.capitalize(),
            "Support": fmt(level.support),
            "Resistance": fmt(level.resistance),
            "Target": fmt(level.target) if level.target is not None else "—",
        }
        for tf, level in sr.items()
    ]
    st.dataframe(pd.DataFrame(sr_rows), hide_index=True, use_container_width=True)

    tier_color = {"High": GREEN, "Moderate": AMBER, "Low": AMBER, "Avoid/Wait": RED}[conviction.tier]
    st.markdown(
        f"**Conviction:** <span style='color:{tier_color};font-weight:700'>{conviction.tier}</span> (score {conviction.score})",
        unsafe_allow_html=True,
    )
    factor_rows = [
        {"Factor": f.name, "Reading": f.reading, "Contribution": f.contribution, "Note": f.note}
        for f in conviction.factors
    ]
    st.dataframe(pd.DataFrame(factor_rows), hide_index=True, use_container_width=True)

    st.markdown("**Pre-trade checklist**")
    checklist_rows = [
        {"Step": c.step, "Question": c.question, "Reading": c.reading, "Pass": "✅" if c.passed else "❌"}
        for c in checklist
    ]
    st.dataframe(pd.DataFrame(checklist_rows), hide_index=True, use_container_width=True)

    st.markdown(f"**Sell-strike candidates** — short leg only, delta {dsrd.DELTA_LOW:.2f}–{dsrd.DELTA_HIGH:.2f}")
    scol1, scol2 = st.columns(2)
    with scol1:
        st.caption("Calls to sell (resistance side — bear call spread's short leg)")
        _render_strike_candidates(strikes["calls"])
    with scol2:
        st.caption("Puts to sell (support side — bull put spread's short leg)")
        _render_strike_candidates(strikes["puts"])
    st.caption(
        "\"Aligned w/ S-R\" means the strike sits beyond the identified weekly support/resistance level — "
        "the DSRD checklist's own \"right side of S/R\" test. The hedge/long leg that turns this into a "
        "credit spread isn't picked here: its distance is a risk-sizing choice (tighter = less premium, "
        "less risk) the source framework doesn't specify a rule for."
    )


def _render_strike_candidates(candidates) -> None:
    if not candidates:
        st.info("No strike in the target delta band.")
        return
    rows = [
        {
            "Strike": c.strike,
            "Symbol": c.tradingsymbol or "—",
            "LTP": fmt(c.ltp),
            "Delta": fmt(c.delta, 3),
            "PoP": f"{c.probability_of_profit * 100:.1f}%",
            "Dist %": fmt(c.distance_pct),
            "Aligned w/ S-R": "✅" if c.aligned_with_sr else "❌",
        }
        for c in candidates[:5]
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
