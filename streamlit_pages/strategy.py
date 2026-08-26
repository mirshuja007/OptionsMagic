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

OPTION_TYPE_OPTIONS = [("CE", "Call"), ("PE", "Put")]
SIDE_OPTIONS = [("BUY", "Buy"), ("SELL", "Sell")]

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
    st.title("Strategy Command Mode")
    mode = st.radio("Mode", ["Discover", "Manual Builder"], horizontal=True, label_visibility="collapsed")
    st.divider()
    if mode == "Discover":
        _render_discover()
    else:
        _render_manual_builder()


def _render_discover() -> None:
    from app.data.feed import available_expiries, generate_minute_series
    from app.data.instruments import ALL_INSTRUMENTS

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


def _render_manual_builder() -> None:
    """Leg-by-leg strategy builder: pick strike/type/side/qty, Add Position,
    see payoff/Greeks/PoP/margin/breakevens update live, and optionally
    Optimize — a nearby-strike search (app.strategy.solver.optimize_legs)
    for a variant with higher max profit at no worse max loss or margin.
    """
    from app.core.black_scholes import OptionType
    from app.data.feed import available_expiries, generate_option_chain
    from app.data.instruments import ALL_INSTRUMENTS, get_instrument
    from app.strategy.generator import build_leg, carry_rate, row_by_strike
    from app.strategy.legs import Leg, Side, breakeven_points, portfolio_greeks
    from app.strategy.solver import evaluate_strategy, optimize_legs

    st.caption(
        "Assemble a strategy leg by leg. Payoff, Greeks, probability of profit, margin, and breakevens "
        "update as you build; Optimize searches nearby strikes for a variant with higher max profit at "
        "no worse max loss or margin."
    )

    symbols = sorted(ALL_INSTRUMENTS.keys())
    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox(
            "Symbol", symbols, index=symbols.index("NIFTY") if "NIFTY" in symbols else 0, key="manual_symbol"
        )
    expiries, expiry_err = safe_call(available_expiries, symbol)
    with col2:
        if expiry_err or not expiries:
            st.error(expiry_err or "No expiries available")
            return
        expiry = st.selectbox(
            "Expiry", expiries, format_func=lambda d: d.strftime("%a, %d %b %Y"), key="manual_expiry"
        )

    chain, chain_err = safe_call(generate_option_chain, symbol, expiry=expiry)
    if chain_err or chain is None:
        st.error(chain_err or "Couldn't load the option chain.")
        return
    instrument = get_instrument(symbol)

    # Legs are tied to one chain's strikes/quotes — reset the builder if the
    # underlying or expiry changed out from under it, since mixing chains is
    # meaningless (stale entry prices/IV, possibly a different lot size).
    context_key = (symbol, expiry)
    if st.session_state.get("manual_builder_context") != context_key:
        st.session_state["manual_builder_context"] = context_key
        st.session_state["manual_legs"] = []
        st.session_state["manual_optimize_results"] = None

    legs: list[Leg] = st.session_state.setdefault("manual_legs", [])
    by_strike = row_by_strike(chain)
    strikes = sorted(by_strike.keys())
    q = carry_rate(chain)
    atm_strike = min(strikes, key=lambda s: abs(s - chain.spot))

    st.markdown(f"**{symbol}** · Spot {chain.spot:.2f} · Expiry {chain.expiry}")

    # A plain selectbox's own widget state doesn't reliably survive the
    # rerun triggered by st.form_submit_button (observed: Type/Side kept
    # reverting to their first option on every Add, which breaks the common
    # workflow of adding a second leg of the same option_type for a
    # spread). Tracking the pending values in our own session_state keys
    # and feeding them back in as `index` sidesteps that entirely — after
    # Add, we update the tracker (a plain dict entry, not a widget's own
    # key), which the next run reads to pick the widget's default.
    pending = st.session_state.setdefault(
        "manual_pending_leg", {"strike": atm_strike, "type": "CE", "side": "BUY", "qty": 1}
    )
    if pending["strike"] not in strikes:
        pending["strike"] = atm_strike

    type_keys = [k for k, _ in OPTION_TYPE_OPTIONS]
    side_keys = [k for k, _ in SIDE_OPTIONS]

    with st.form("add_leg_form", clear_on_submit=False):
        c1, c2, c3, c4, c5 = st.columns([2, 1.2, 1, 1, 1.2])
        strike = c1.selectbox("Strike", strikes, index=strikes.index(pending["strike"]))
        option_type_key = c2.selectbox(
            "Type", type_keys, index=type_keys.index(pending["type"]), format_func=dict(OPTION_TYPE_OPTIONS).get
        )
        side_key = c3.selectbox(
            "Side", side_keys, index=side_keys.index(pending["side"]), format_func=dict(SIDE_OPTIONS).get
        )
        qty = c4.number_input("Lots", min_value=1, value=pending["qty"], step=1)
        c5.write("")  # vertical spacer to align the submit button with the inputs
        add_clicked = c5.form_submit_button("Add Position", use_container_width=True)

    if add_clicked:
        # legs.append mutates st.session_state["manual_legs"] in place — the
        # rest of this run already sees it, so no st.rerun() needed here.
        option_type = OptionType.CALL if option_type_key == "CE" else OptionType.PUT
        side = Side.LONG if side_key == "BUY" else Side.SHORT
        legs.append(build_leg(by_strike[strike], option_type, side, int(qty), q))
        st.session_state["manual_pending_leg"] = {"strike": strike, "type": option_type_key, "side": side_key, "qty": int(qty)}
        st.session_state["manual_optimize_results"] = None

    if not legs:
        st.info("Add at least one position to see the analysis.")
        return

    st.subheader("Positions")
    for i, leg in enumerate(legs):
        lc1, lc2, lc3, lc4, lc5, lc6 = st.columns([1, 1, 1, 1, 1, 0.6])
        lc1.write("Buy" if leg.side == Side.LONG else "Sell")
        lc2.write("Call" if leg.option_type == OptionType.CALL else "Put")
        lc3.write(f"{leg.strike:.0f}")
        lc4.write(f"{leg.quantity_lots} lot(s)")
        lc5.write(f"₹{leg.entry_price:.2f}")
        if lc6.button("✕", key=f"remove_leg_{i}", help="Remove this position"):
            legs.pop(i)
            st.session_state["manual_legs"] = legs
            st.session_state["manual_optimize_results"] = None
            st.rerun()

    if st.button("Reset all positions"):
        st.session_state["manual_legs"] = []
        st.session_state["manual_optimize_results"] = None
        st.rerun()

    st.divider()

    with st.spinner("Analyzing…"):
        result = evaluate_strategy(legs, chain, instrument, n_paths=20_000)
    breakevens = breakeven_points(legs, instrument.lot_size)
    greeks = portfolio_greeks(legs, chain.spot, chain.time_to_expiry_years, chain.risk_free_rate, instrument.lot_size)

    max_profit_display = "Unlimited" if result.payoff.max_profit > UNLIMITED_THRESHOLD else fmt_currency(result.payoff.max_profit)
    max_loss_display = "Unlimited" if result.payoff.max_loss < -UNLIMITED_THRESHOLD else fmt_currency(result.payoff.max_loss)
    breakevens_display = ", ".join(f"{b:.0f}" for b in breakevens) if breakevens else "—"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Prob. of Profit", f"{result.probability_of_profit * 100:.1f}%")
    m2.metric("Max Profit", max_profit_display)
    m3.metric("Max Loss", max_loss_display)
    m4.metric("Breakeven(s)", breakevens_display)

    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Margin (est.)", fmt_currency(result.margin.total_margin))
    m6.metric("Net Entry", fmt_currency(result.margin.net_entry_credit))
    m7.metric("Expected Value", fmt_currency(result.expected_value))
    m8.metric("Sharpe", f"{result.sharpe:.3f}")

    g1, g2, g3, g4, g5 = st.columns(5)
    g1.metric("Delta", f"{greeks.delta:.2f}")
    g2.metric("Gamma", f"{greeks.gamma:.4f}")
    g3.metric("Theta", f"{greeks.theta:.2f}")
    g4.metric("Vega", f"{greeks.vega:.2f}")
    g5.metric("Rho", f"{greeks.rho:.2f}")

    _render_payoff_chart(legs, instrument.lot_size, chain.spot, breakevens)

    st.divider()
    st.subheader("Optimize")
    st.caption(
        "Searches nearby strikes for the same strategy shape (same option types/sides/quantities) that "
        "increases max profit while keeping max loss no worse and margin within 10% of what's built above."
    )
    if st.button("Suggest Improvements", type="primary"):
        with st.spinner("Searching nearby strikes…"):
            st.session_state["manual_optimize_results"] = optimize_legs(
                legs, chain, instrument, strike_range=3, n_paths=3000, max_combos=300, top_n=5
            )

    alternatives = st.session_state.get("manual_optimize_results")
    if alternatives:
        for i, alt in enumerate(alternatives):
            with st.container(border=True):
                ac1, ac2 = st.columns([3, 1])
                alt_profit_display = "Unlimited" if alt.payoff.max_profit > UNLIMITED_THRESHOLD else fmt_currency(alt.payoff.max_profit)
                alt_loss_display = "Unlimited" if alt.payoff.max_loss < -UNLIMITED_THRESHOLD else fmt_currency(alt.payoff.max_loss)
                if alt.payoff.max_profit > UNLIMITED_THRESHOLD or result.payoff.max_profit > UNLIMITED_THRESHOLD:
                    gain_display = ""  # a delta against/from "Unlimited" isn't a meaningful number
                else:
                    gain_display = f" (+{fmt_currency(alt.payoff.max_profit - result.payoff.max_profit)})"
                ac1.markdown(f"**Alternative #{i + 1}** — Max Profit {alt_profit_display}{gain_display}")
                if ac2.button("Apply", key=f"apply_alt_{i}", use_container_width=True):
                    st.session_state["manual_legs"] = list(alt.legs)
                    st.session_state["manual_optimize_results"] = None
                    st.rerun()
                strikes_str = ", ".join(f"{leg.strike:.0f}" for leg in alt.legs)
                st.caption(
                    f"Strikes: {strikes_str} · Max Loss {alt_loss_display} · "
                    f"Margin {fmt_currency(alt.margin.total_margin)} · PoP {alt.probability_of_profit * 100:.1f}%"
                )
    elif alternatives is not None:
        st.info("No nearby-strike alternative improves max profit without worsening max loss or margin.")


def _render_payoff_chart(legs, lot_size: int, spot: float, breakevens: list[float]) -> None:
    import numpy as np
    import plotly.graph_objects as go

    from app.strategy.legs import payoff_at_expiry
    from streamlit_pages.common import AMBER, GREEN, dark_layout

    strikes = sorted({leg.strike for leg in legs})
    lo = min(strikes[0], spot) * 0.85
    hi = max(strikes[-1], spot) * 1.15
    xs = np.linspace(lo, hi, 200)
    ys = payoff_at_expiry(legs, lot_size, xs)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="P&L at Expiry", line=dict(color=GREEN, width=2)))
    fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.3)", width=1))
    fig.add_vline(x=spot, line=dict(color=AMBER, width=1, dash="dash"), annotation_text="Spot", annotation_position="top")
    for b in breakevens:
        fig.add_vline(x=b, line=dict(color="rgba(255,255,255,0.4)", width=1, dash="dot"))
    dark_layout(fig, title="Payoff at Expiry", height=380, xaxis_title="Underlying Price", yaxis_title="P&L (₹)")
    st.plotly_chart(fig, use_container_width=True)
