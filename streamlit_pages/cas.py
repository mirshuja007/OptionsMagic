"""CAS Monitor: SEBI's Closing Auction Session (live from Aug 3, 2026) for
NSE/BSE F&O-eligible stocks — reference price, +/-3% auction band, and
where things stand in today's timeline.

CAS replaces the old last-30-minutes-VWAP closing-price method, for stocks
that have listed F&O contracts, with a 20-minute call auction: buy/sell
orders are collected (not executed) from 3:15-3:35pm and matched at the
single price that maximizes executable volume. There is no per-index CAS —
an index's close is still the weighted sum of its constituents' closes,
those constituents' closes are just set by CAS now instead of VWAP. See
``app.data.cas``'s module docstring for the full mechanism and source.

The reference price and band shown here are computed independently from
ordinary minute-bar data (VWAP of the 3:00-3:15pm bars) — they don't depend
on Kite exposing any CAS-specific fields. Whether Kite Connect's API *also*
exposes the live in-auction indicative-close/imbalance-quantity fields that
Kite Web shows is unconfirmed; the "Live auction data (diagnostic)" section
below is how that gets checked, live, the next time this runs during the
actual 3:15-3:35pm IST window with a working Kite session.
"""
from __future__ import annotations

import streamlit as st

from streamlit_pages.common import fmt, safe_call

_PHASE_ICON = {
    "pre_open": "⚪",
    "continuous": "🔵",
    "reference_window": "🟡",
    "auction": "🟢",
    "transition": "🟠",
    "post_close": "🟠",
    "closed": "⚪",
}


def render() -> None:
    from app.data.instruments import STOCKS

    st.title("CAS Monitor")
    st.caption(
        "SEBI's Closing Auction Session (live on NSE/BSE from Aug 3, 2026) for stocks with listed F&O "
        "contracts. Reference price is the VWAP of trades between 3:00-3:15pm; the auction (3:15-3:35pm) "
        "matches collected buy/sell orders at the price that clears the most volume, within +/-3% of that "
        "reference. There's no per-index CAS — an index's close is still the weighted sum of its "
        "constituents' closes, just set by CAS now instead of the old VWAP method for those that have it."
    )

    symbols = sorted(STOCKS.keys())
    symbol = st.selectbox(
        "Stock", symbols, index=symbols.index("RELIANCE") if "RELIANCE" in symbols else 0,
        help="Only stocks with listed F&O contracts go through CAS — this list is this app's known set, "
        "not the full NSE/BSE F&O universe.",
    )

    from app.data.cas import cas_window_status, reference_band, reference_price
    from app.data.feed import generate_minute_series, get_active_provider

    status = cas_window_status()
    icon = _PHASE_ICON.get(status.phase, "⚪")
    st.subheader(f"{icon} {status.label}")
    st.caption(f"As of {status.now_ist.strftime('%H:%M:%S')} IST · {symbol}")

    series, series_err = safe_call(generate_minute_series, symbol)
    if series_err or not series:
        st.error(series_err or "Couldn't load today's minute series.")
        return

    ref_price = reference_price(series)
    c1, c2, c3 = st.columns(3)
    if ref_price is None:
        c1.metric("Reference Price", "—", help="Available once today's session reaches 3:00pm IST.")
        c2.metric("Auction Band (Lower)", "—")
        c3.metric("Auction Band (Upper)", "—")
        st.info(
            "Reference price isn't available yet — it's the VWAP of trades between 3:00-3:15pm IST, "
            "so this fills in once today's session reaches that window."
        )
    else:
        lower, upper = reference_band(ref_price)
        c1.metric("Reference Price", fmt(ref_price))
        c2.metric("Auction Band (Lower)", fmt(lower))
        c3.metric("Auction Band (Upper)", fmt(upper))

    with st.expander("How CAS determines the closing price"):
        st.markdown(
            "1. **9:15am-3:15pm** — normal continuous trading for CAS-eligible stocks.\n"
            "2. **3:00-3:15pm** — the reference price is set: the VWAP of trades in this 15-minute window.\n"
            "3. **3:15-3:35pm** — the Closing Auction Session itself. Only market and limit orders are "
            "accepted (no stop-loss, no disclosed-quantity/iceberg orders); orders are collected, not "
            "executed immediately, within +/-3% of the reference price.\n"
            "4. **At 3:35pm** — the exchange picks the single price that lets the most volume trade "
            "(maximum executable quantity). Ties break toward the smallest leftover unmatched quantity, "
            "then toward whichever candidate price is closer to the reference price. That price becomes "
            "the stock's official close for the day.\n"
            "5. **3:35-3:50pm** — transition period. **3:50-4:00pm** — post-close session.\n\n"
            "Non-CAS stocks (no listed F&O contracts) still close the old way: VWAP of the last 30 minutes "
            "of continuous trading (3:00-3:30pm)."
        )

    if get_active_provider() != "kite":
        return

    with st.expander("Live auction data (diagnostic)"):
        st.caption(
            "Attempts a raw Kite Connect quote for this stock's spot instrument, to check whether the API "
            "exposes the same reference price / indicative close / imbalance quantity fields Kite Web shows "
            "during the auction window — this is the actual verification step, only meaningful between "
            "3:15-3:35pm IST on a trading day."
        )
        if st.button("Fetch live quote"):
            from app.data.kite_client import KiteAuthError
            from app.data.kite_feed import KiteFeedError, raw_underlying_quote

            try:
                quote = raw_underlying_quote(symbol)
            except (KiteFeedError, KiteAuthError) as exc:
                st.error(f"Couldn't fetch a live quote: {exc}")
            else:
                st.json(quote)
                known_fields = {"instrument_token", "last_price", "last_quantity", "last_trade_time", "ohlc",
                                 "volume", "buy_quantity", "sell_quantity", "average_price", "oi", "depth",
                                 "oi_day_high", "oi_day_low", "net_change", "lower_circuit_limit",
                                 "upper_circuit_limit", "timestamp"}
                extra_fields = set(quote.keys()) - known_fields
                if extra_fields:
                    st.success(f"Unrecognized field(s) present — worth checking if these are CAS data: {sorted(extra_fields)}")
                else:
                    st.warning(
                        "Only the usual quote fields are present — no CAS-specific field (reference price / "
                        "indicative close / imbalance quantity) found in this response."
                    )
