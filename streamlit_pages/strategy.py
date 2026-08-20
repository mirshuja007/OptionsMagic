"""Strategy Command Mode: the Constraint-Solving Strategy Discovery Engine's
UI, a Streamlit port of frontend/app/strategy/page.tsx + StrategyForm.tsx +
StrategyResults.tsx, calling app.strategy.solver.discover_strategies()
directly instead of over HTTP.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from streamlit_pages.common import fmt_currency, safe_call, strategy_label

UNLIMITED_THRESHOLD = 1e11  # matches app.strategy.legs._UNLIMITED_RISK_SENTINEL (1e12), with margin for float noise

STRATEGY_TYPE_OPTIONS = [
    ("bull_put_spread", "Bull Put Spread"),
    ("bear_call_spread", "Bear Call Spread"),
    ("iron_condor", "Iron Condor"),
    ("iron_fly", "Iron Fly"),
    ("ratio_spread_call", "Ratio Spread (Call)"),
    ("ratio_spread_put", "Ratio Spread (Put)"),
]

RANKING_MODE_OPTIONS = [
    ("yield", "Yield-first — chase raw return on margin"),
    ("balanced", "Balanced — yield, PoP, and Sharpe evenly"),
    ("safety", "Safety-first — favor PoP and risk-adjusted quality"),
]

DIRECTION_BIAS_OPTIONS = [
    ("auto", "Auto — follow Smart OI / VWAP"),
    ("bullish", "Force bullish"),
    ("bearish", "Force bearish"),
    ("neutral", "Force neutral"),
]


def render() -> None:
    from app.data.feed import available_expiries, generate_minute_series
    from app.data.instruments import ALL_INSTRUMENTS

    st.title("Strategy Command Mode")
    st.caption(
        "The Constraint-Solving Strategy Discovery Engine sweeps the option chain for credit spreads, iron "
        "condors, iron flies, and ratio spreads that satisfy your risk/capital/probability boundaries, and "
        "ranks the survivors by a tunable yield/PoP/Sharpe blend with an optional Research-signal nudge."
    )

    symbols = sorted(ALL_INSTRUMENTS.keys())
    with st.sidebar:
        st.header("Constraint Inputs")
        symbol = st.selectbox("Symbol", symbols, index=symbols.index("NIFTY") if "NIFTY" in symbols else 0)
        expiries, expiry_err = safe_call(available_expiries, symbol)
        if expiry_err or not expiries:
            st.error(expiry_err or "No expiries available")
            return
        expiry = st.selectbox("Expiry", expiries, format_func=lambda d: d.strftime("%a, %d %b %Y"))

        min_pop = st.number_input("Minimum Probability of Profit (%)", 1, 99, 80)
        min_yield = st.number_input("Target Yield on Margin (%)", 0.0, value=1.0, step=0.1)

        max_profit_unlimited = st.checkbox("Max Profit Ceiling: Unlimited")
        max_profit = st.number_input("Max Profit Ceiling (₹)", 0, value=5000, step=100, disabled=max_profit_unlimited)

        max_loss_unlimited = st.checkbox("Max Loss Cap: Unlimited")
        max_loss = st.number_input("Max Loss Cap (₹)", 0, value=3000, step=100, disabled=max_loss_unlimited)

        margin_cap = st.number_input("Margin Blocked Cap (₹)", 0, value=500000, step=1000)

        with st.expander("Advanced: Ranking & Research Signals"):
            ranking_mode = st.selectbox("Ranking Mode", [k for k, _ in RANKING_MODE_OPTIONS], format_func=dict(RANKING_MODE_OPTIONS).get)
            use_research_signals = st.checkbox("Use Research Mode signals", value=True)
            if use_research_signals:
                st.caption(
                    "Nudges ranking toward candidates whose short strikes sit beyond OI-based support/resistance "
                    "(or, for iron condors/flies, centered on Max Pain) and whose directional lean matches Smart "
                    "OI / VWAP. Capped at +/-15% of the base score — it never overrides the constraints above."
                )
            direction_bias = st.selectbox(
                "Direction Bias", [k for k, _ in DIRECTION_BIAS_OPTIONS], format_func=dict(DIRECTION_BIAS_OPTIONS).get,
                disabled=not use_research_signals,
            )
            enabled_types = st.multiselect(
                "Strategy Types",
                [k for k, _ in STRATEGY_TYPE_OPTIONS],
                default=[k for k, _ in STRATEGY_TYPE_OPTIONS],
                format_func=dict(STRATEGY_TYPE_OPTIONS).get,
            )
            st.caption(
                "Ratio spreads carry undefined risk on the excess short leg, so a finite Max Loss Cap almost "
                "always excludes them by design — check \"Unlimited\" on Max Loss Cap above to see any."
            )

        run_clicked = st.button("Discover Strategies", type="primary", use_container_width=True)

    if not run_clicked:
        st.info("Set your constraints and click Discover Strategies.")
        return
    if not enabled_types:
        st.error("Select at least one strategy type.")
        return

    from app.data.feed import generate_option_chain, get_active_provider
    from app.data.instruments import get_instrument
    from app.strategy.solver import StrategyConstraints, build_research_context, discover_strategies

    chain, chain_err = safe_call(generate_option_chain, symbol, expiry=expiry)
    if chain_err or chain is None:
        st.error(chain_err or "Couldn't load the option chain.")
        return
    instrument = get_instrument(symbol)

    constraints = StrategyConstraints(
        min_pop=min_pop / 100,
        min_yield_pct=min_yield / 100,
        max_profit_cap=None if max_profit_unlimited else float(max_profit),
        max_loss_cap=None if max_loss_unlimited else float(max_loss),
        margin_cap=float(margin_cap),
        ranking_mode=ranking_mode,
        strategy_types=frozenset(enabled_types),
        use_research_signals=use_research_signals,
        direction_bias=direction_bias,
    )

    research_ctx = None
    if use_research_signals:
        from app.analytics import vwap as vwap_mod

        series, series_err = safe_call(generate_minute_series, symbol)
        vwap_value = None
        if series and not series_err:
            vwaps = vwap_mod.vwap_series([(p, v) for _, p, v in series])
            if vwaps:
                vwap_value = round(vwaps[-1], 2)
        research_ctx = build_research_context(chain, vwap=vwap_value)

    with st.spinner("Solving…"):
        results = discover_strategies(chain, instrument, constraints, research_ctx=research_ctx)

    st.caption(f"{symbol} · Spot {chain.spot:.2f} · Expiry {chain.expiry} · {len(results)} strategies matched")

    if not results:
        st.warning(
            "No strategies matched your constraints. Try relaxing the min probability of profit, max loss cap "
            "(often the binding one — check \"Unlimited\" to remove it entirely), target yield, max profit "
            "ceiling (a high target yield on a large-margin trade needs a large max profit — Unlimited helps "
            "there too), or margin cap."
        )
        return

    provider = get_active_provider()
    for rank, r in enumerate(results, start=1):
        max_profit_display = "Unlimited" if r.payoff.max_profit > UNLIMITED_THRESHOLD else fmt_currency(r.payoff.max_profit)
        max_loss_display = "Unlimited" if r.payoff.max_loss < -UNLIMITED_THRESHOLD else fmt_currency(r.payoff.max_loss)
        yield_display = "Unlimited" if r.payoff.max_profit > UNLIMITED_THRESHOLD else f"{r.yield_pct * 100:.2f}%"

        with st.container(border=True):
            head_l, head_r = st.columns([3, 1])
            head_l.markdown(f"**#{rank}  {strategy_label(r.strategy_type)}**")
            head_r.markdown(f"<div style='text-align:right'>EV {fmt_currency(r.expected_value)} &nbsp; `Score {r.composite_score:.3f}`</div>", unsafe_allow_html=True)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("PoP", f"{r.probability_of_profit * 100:.1f}%")
            m2.metric("Yield on Margin", yield_display)
            m3.metric("Max Profit", max_profit_display)
            m4.metric("Max Loss", max_loss_display)

            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Margin Blocked (est.)", fmt_currency(r.margin.total_margin))
            m6.metric("Net Entry Credit", fmt_currency(r.margin.net_entry_credit))
            m7.metric("Sharpe", f"{r.sharpe:.3f}")
            m8.metric("Signal Alignment", f"{r.technical_alignment * 100:.0f}%")

            legs_df = pd.DataFrame(
                [
                    {
                        "Side": leg.side.value,
                        "Type": leg.option_type.value,
                        "Strike": leg.strike,
                        "Qty (lots)": leg.quantity_lots,
                        "Entry": leg.entry_price,
                        "IV": f"{leg.iv * 100:.1f}%",
                    }
                    for leg in r.legs
                ]
            )
            st.dataframe(legs_df, hide_index=True, use_container_width=True)

            if provider == "kite":
                if st.button("Verify Real Margin (Kite)", key=f"verify_margin_{rank}"):
                    from app.data.kite_client import KiteAuthError
                    from app.margin.kite_margin import KiteMarginError, fetch_basket_margin

                    try:
                        live = fetch_basket_margin(r.legs, instrument, consider_positions=False)
                        st.success(
                            f"Real margin (Kite): {fmt_currency(live.total_margin)} "
                            f"(SPAN {fmt_currency(live.span_margin or 0)}, exposure {fmt_currency(live.exposure_margin or 0)})"
                        )
                    except (KiteMarginError, KiteAuthError) as exc:
                        st.error(f"Couldn't fetch real margin: {exc}")
            else:
                st.caption("Verify Real Margin (Kite) requires MARKET_DATA_PROVIDER=kite.")
