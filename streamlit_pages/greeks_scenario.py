"""Greeks Scenario Calculator: "if the index moves X%, what happens to this
option's premium?" See ``app.analytics.greeks_scenario`` for the repricing
method and the rule-of-thumb volatility-reaction presets (clearly not a live
India VIX feed — this platform doesn't have one wired in).

Unlike the other pages, this isn't a live tracker — it's a sizing aid you
reach for *before* placing a trade (or mid-trade, to judge how much worse a
further move could get), so it's not wrapped in an auto-refresh fragment.
"""
from __future__ import annotations

import streamlit as st

from streamlit_pages.common import fmt, fmt_currency, safe_call


def render() -> None:
    st.title("Greeks Scenario Calculator")
    st.caption(
        "Pick an option you're looking at (or already sold), dial in a hypothetical index move, and see "
        "roughly how its premium would react — split into the part driven by the index move itself and "
        "the part driven by implied vol moving with it. Built so you can size entries across multiple legs "
        "instead of risking it all on one."
    )
    st.warning(
        "This platform has no live India VIX feed wired in, so the volatility reaction to a given move is a "
        "hand-picked, clearly-labeled rule of thumb (see the regime picker below) — not a calibrated model "
        "or a live quote. Override it yourself if you're watching VIX live. Educational reference only, not "
        "investment advice."
    )

    from app.analytics import greeks_scenario as gs
    from app.core.black_scholes import OptionType
    from app.data.feed import available_expiries, generate_option_chain
    from app.data.instruments import ALL_INSTRUMENTS

    symbols = sorted(ALL_INSTRUMENTS, key=lambda s: (not ALL_INSTRUMENTS[s].is_index, s))
    symbol = st.selectbox("Symbol", symbols, index=symbols.index("NIFTY") if "NIFTY" in symbols else 0)
    instrument = ALL_INSTRUMENTS[symbol]

    expiries, expiry_err = safe_call(available_expiries, symbol)
    if expiry_err or not expiries:
        st.error(expiry_err or "No expiries available.")
        return
    expiry = st.selectbox("Expiry", expiries, format_func=lambda d: d.strftime("%a, %d %b %Y"))

    chain, err = safe_call(generate_option_chain, symbol, expiry=expiry)
    if err or chain is None:
        st.error(err or "Couldn't load the option chain.")
        return
    if not chain.rows:
        st.info("No strikes available right now.")
        return

    strikes = [row.strike for row in chain.rows]
    atm_strike = min(strikes, key=lambda k: abs(k - chain.spot))
    col1, col2 = st.columns(2)
    strike = col1.selectbox("Strike", strikes, index=strikes.index(atm_strike))
    side_label = col2.radio("Side", ["CE", "PE"], horizontal=True)
    option_type = OptionType.CALL if side_label == "CE" else OptionType.PUT

    row = next(r for r in chain.rows if r.strike == strike)
    leg = row.call if option_type == OptionType.CALL else row.put
    q = chain.risk_free_rate if instrument.pricing_carry_rate_equals_risk_free else 0.0

    base = gs.reprice_scenario(
        spot=chain.spot, strike=strike, t=chain.time_to_expiry_years, r=chain.risk_free_rate,
        iv=leg.iv, option_type=option_type, move_pct=0.0, iv_change_points=0.0, q=q,
    )

    st.markdown("**Right now**")
    dte_days = chain.time_to_expiry_years * 365.0
    st.caption(
        f"Expiry: {chain.expiry.strftime('%a, %d %b %Y')} · {dte_days:.1f} days to expiry · "
        f"{instrument.expiry_cadence} cadence — pick a different one from the Expiry selector above."
    )
    mcols = st.columns(4)
    mcols[0].metric("Spot", fmt(chain.spot))
    mcols[1].metric("Model premium", fmt(base.base_price))
    mcols[2].metric("Live quote", fmt(leg.ltp))
    mcols[3].metric("IV", f"{leg.iv * 100:.1f}%")
    gcols = st.columns(4)
    gcols[0].metric("Delta", f"{leg.greeks.delta:+.3f}")
    gcols[1].metric("Gamma", f"{leg.greeks.gamma:.5f}")
    gcols[2].metric("Vega (₹/1 IV pt)", fmt(leg.greeks.vega))
    gcols[3].metric("Theta (₹/day)", fmt(leg.greeks.theta))
    st.caption(
        "Delta: ₹ premium change per 1-point index move. Vega: ₹ premium change per 1-percentage-point IV "
        "move. Theta: ₹ the premium loses per day, working in a seller's favor, all else equal. The small "
        "gap between \"model premium\" and \"live quote\" is normal bid/ask noise — the calculator below "
        "uses the model premium so a 0% move always shows exactly zero change."
    )

    st.divider()
    st.markdown("**Build your scenario**")
    regime = st.select_slider("Volatility regime", options=list(gs.VOL_REGIMES), value=gs.DEFAULT_REGIME)
    down_sens, up_sens = gs.VOL_REGIMES[regime]
    dte_mult = gs.dte_reaction_multiplier(dte_days)
    st.caption(
        f"\"{regime}\" rule of thumb at {dte_days:.1f} days to expiry: a 1% down move adds about "
        f"{down_sens * dte_mult:.1f} IV points; a 1% up move changes IV by about {up_sens * dte_mult:+.1f} "
        f"points (vol usually cools a little on rallies). Base preset {down_sens:.1f}/{up_sens:+.1f} pts, "
        f"scaled {dte_mult:.2f}x for days-to-expiry — shorter-dated options' IV typically swings harder per "
        "1% move than longer-dated ones."
    )
    move_pct = st.slider("Hypothetical index move (%)", min_value=-5.0, max_value=5.0, value=-1.0, step=0.25)

    auto_iv_change = gs.iv_change_for_move(move_pct, regime, dte_days=dte_days)
    override = st.checkbox("Override the IV change myself (e.g. you're watching VIX live)")
    iv_change = (
        st.number_input("IV change (percentage points)", value=float(round(auto_iv_change, 2)), step=0.1)
        if override else auto_iv_change
    )

    result = gs.reprice_scenario(
        spot=chain.spot, strike=strike, t=chain.time_to_expiry_years, r=chain.risk_free_rate,
        iv=leg.iv, option_type=option_type, move_pct=move_pct, iv_change_points=iv_change, q=q,
    )

    st.divider()
    st.markdown("**Result**")
    rcols = st.columns(3)
    rcols[0].metric("Shocked spot", fmt(result.shocked_spot), f"{move_pct:+.2f}%")
    rcols[1].metric("Shocked IV", f"{result.shocked_iv * 100:.1f}%", f"{iv_change:+.2f} pts")
    rcols[2].metric(
        "New premium", fmt(result.new_price),
        f"{result.price_change:+.2f} ({result.price_change_pct:+.1f}%)",
    )
    st.caption(
        f"Of that {result.price_change:+.2f} change: {result.spot_component:+.2f} comes from the index "
        f"move itself, {result.vol_component:+.2f} comes from the IV change riding along with it."
    )

    z = gs.move_in_sigma(move_pct, leg.iv)
    st.caption(
        f"A {move_pct:+.2f}% move is about {abs(z):.1f}x this option's current implied *daily* standard "
        f"deviation ({gs.daily_std_pct(leg.iv):.2f}%) — {gs.sigma_label(z)}."
    )

    st.divider()
    st.markdown("**If you sold this**")
    pcol1, pcol2 = st.columns(2)
    lots = pcol1.number_input("Lots sold", min_value=1, value=1, step=1)
    tranches = pcol2.number_input(
        "...but split across this many legs/tranches instead", min_value=1, value=3, step=1,
    )
    pnl_full = gs.seller_pnl(base.base_price, result.new_price, instrument.lot_size, lots)
    pnl_per_tranche = pnl_full / tranches
    if pnl_full < 0:
        st.error(
            f"Full size at once: you'd be down {fmt_currency(-pnl_full)}. Spread across {tranches} "
            f"legs of equal size instead, this single move only costs {fmt_currency(-pnl_per_tranche)} per "
            "leg — leaving you room to average or roll the rest instead of eating the whole hit at once."
        )
    else:
        st.success(f"Full size at once: this move would be worth {fmt_currency(pnl_full)} in your favor.")

    with st.expander("See the reaction across a range of moves"):
        st.caption(f"Using the \"{regime}\" regime's IV reaction, scaled for {dte_days:.1f} DTE, for every row below.")
        sweep_moves = [-3.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0]
        rows = []
        for m in sweep_moves:
            iv_chg = gs.iv_change_for_move(m, regime, dte_days=dte_days)
            r = gs.reprice_scenario(
                spot=chain.spot, strike=strike, t=chain.time_to_expiry_years, r=chain.risk_free_rate,
                iv=leg.iv, option_type=option_type, move_pct=m, iv_change_points=iv_chg, q=q,
            )
            seller = gs.seller_pnl(base.base_price, r.new_price, instrument.lot_size, lots)
            rows.append({
                "Index move": f"{m:+.1f}%",
                "New premium": fmt(r.new_price),
                "Premium change": f"{r.price_change_pct:+.1f}%",
                "Seller P&L": fmt_currency(seller),
            })
        st.table(rows)
