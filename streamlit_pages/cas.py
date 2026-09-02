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
actual 3:15-3:35pm IST window with a working Kite session — for a single
stock, or (new) all tracked stocks at once for a definitive yes/no across
the whole universe. Deliberately still a diagnostic, not a data column:
even confirming Kite exposes *something* CAS-specific wouldn't be enough to
add a real "equilibrium price" column, since replicating NSE's own
equilibrium-price algorithm (maximum executable volume across the full
order book) needs full order-book depth, and Kite's quote API caps
``depth`` at 5 bid/ask levels — not something to approximate and label as
real.

The "Constituent Overview" table and its Bias Signal (see
``app.data.cas.compute_bias_signal``) are deliberately NOT a prediction of
index direction — there's no statistically validated or backtested model
behind them, just a transparent readout of how far the tracked stocks are
currently trading from their own reference prices, either treating every
stock equally or weighted by real NIFTY 50 / SENSEX 30 index weight (see
``app.data.index_weights`` for that data's provenance — a user-supplied
snapshot, not scraped). See ``compute_bias_signal``'s docstring before
changing how this is presented; the honesty of that framing is the point,
not an accident.

The "CAS History" section below it is the one place this page *does* build
toward a real probability estimate — by logging actual (reference price,
settled close) outcomes over time via ``app.data.cas_history`` and reading
off the empirical distribution once enough sessions have accumulated. It
starts empty; there is no shortcut around actually waiting for the data.
"""
from __future__ import annotations

import pandas as pd
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

# Every field a plain (non-CAS) Kite quote response is known to carry. Any
# field outside this set, seen live during the 3:15-3:35pm auction window,
# is a candidate CAS-specific field (reference price / indicative close /
# imbalance quantity) worth investigating — see _render_live_auction_diagnostic.
_KNOWN_QUOTE_FIELDS = {
    "instrument_token", "last_price", "last_quantity", "last_trade_time", "ohlc", "volume", "buy_quantity",
    "sell_quantity", "average_price", "oi", "depth", "oi_day_high", "oi_day_low", "net_change",
    "lower_circuit_limit", "upper_circuit_limit", "timestamp",
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

    _render_constituent_overview(STOCKS)
    _render_cas_history(STOCKS)

    if get_active_provider() != "kite":
        return

    _render_live_auction_diagnostic(STOCKS, symbol)


def _render_constituent_overview(stocks: dict) -> None:
    """All tracked stocks' reference price/band/current-price/deviation in
    one table, plus the honest bias-signal readout — see the module
    docstring's note on why that signal is framed the way it is. Gated
    behind a button rather than auto-loading: this fans out to one
    ``generate_minute_series`` call per stock (49, currently — the full
    deduplicated NIFTY 50 + SENSEX 30 union), which on live Kite data means
    49 Historical Data API calls — a separately rate-limited, paid add-on
    (see kite_feed.py's module docstring) — not something to fire on every
    page load/rerun.
    """
    from app.data.cas import compute_bias_signal, constituent_snapshot
    from app.data.feed import generate_minute_series
    from app.data.index_weights import NIFTY50_WEIGHTS_PCT, SENSEX30_WEIGHTS_PCT

    st.divider()
    st.subheader("Constituent Overview")
    st.caption(
        f"Reference price, auction band, current price, and deviation for all {len(stocks)} tracked stocks "
        "at once. Loads on demand (one data call per stock) rather than automatically — see the button "
        "below before assuming this is live-refreshing."
    )

    if st.button(f"Load all {len(stocks)} constituents", type="primary"):
        snapshots = []
        with st.spinner(f"Loading {len(stocks)} constituents…"):
            for sym, instrument in sorted(stocks.items()):
                series, _err = safe_call(generate_minute_series, sym)
                snapshots.append(constituent_snapshot(sym, instrument.display_name, series or []))
        st.session_state["cas_constituent_snapshots"] = snapshots

    snapshots = st.session_state.get("cas_constituent_snapshots")
    if not snapshots:
        return

    rows = [
        {
            "Symbol": s.symbol,
            "Company": s.display_name,
            "Reference Price": fmt(s.reference_price) if s.reference_price is not None else "—",
            "Band Lower": fmt(s.band_lower) if s.band_lower is not None else "—",
            "Band Upper": fmt(s.band_upper) if s.band_upper is not None else "—",
            "Current Price": fmt(s.current_price) if s.current_price is not None else "—",
            "Deviation": f"{s.deviation_pct:+.2f}%" if s.deviation_pct is not None else "—",
        }
        for s in snapshots
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(
        "No \"equilibrium price\" column — that would need Kite's live in-auction order-book data, which "
        "isn't confirmed to be available via the API yet (see \"Live auction data (diagnostic)\" below)."
    )

    unweighted = compute_bias_signal(snapshots)
    if unweighted is None:
        st.info("No reference prices available across the tracked constituents yet — check back after 3:00pm IST.")
        return

    st.markdown("#### Constituent Bias Signal")
    st.caption(
        "An honest, transparent readout of how the tracked constituents are trading relative to their own "
        "CAS reference prices right now. This is **not** a prediction, **not** a probability, and has no "
        "statistical or backtested validity as a forecast of index movement. It's exactly what the numbers "
        "below say, and nothing more."
    )

    basis = st.radio(
        "Weighting basis",
        ["Equal-weighted", "NIFTY 50-weighted", "SENSEX 30-weighted"],
        horizontal=True,
        help="Equal-weighted treats every tracked stock the same regardless of size. The two index-weighted "
        "options use real NIFTY 50 / SENSEX 30 free-float weights (a 2026-09-01 snapshot — see "
        "app.data.index_weights) so a heavyweight like RELIANCE counts for more than a small one — that's "
        "closer to how the actual index moves. NIFTY 50-weighted covers all tracked stocks (every one is a "
        "NIFTY 50 member); SENSEX 30-weighted only covers the subset of tracked stocks that are also SENSEX "
        "30 members (30 of them, not all 49) — the rest are silently excluded from that view, not zero-weighted.",
    )
    if basis == "NIFTY 50-weighted":
        signal = compute_bias_signal(snapshots, weights=NIFTY50_WEIGHTS_PCT, weighting_label=basis)
    elif basis == "SENSEX 30-weighted":
        sensex_snapshots = [s for s in snapshots if s.symbol in SENSEX30_WEIGHTS_PCT]
        signal = compute_bias_signal(sensex_snapshots, weights=SENSEX30_WEIGHTS_PCT, weighting_label=basis)
    else:
        signal = unweighted

    if signal is None:
        st.info("None of the SENSEX 30-member stocks among the tracked set have a reference price yet.")
        return

    b1, b2, b3 = st.columns(3)
    b1.metric("Direction", signal.direction)
    b2.metric("Magnitude", signal.magnitude_bucket)
    b3.metric("Avg. Deviation", f"{signal.average_deviation_pct:+.2f}%")
    b4, b5 = st.columns(2)
    b4.metric(
        "Breadth",
        f"{signal.breadth_pct:.0f}%",
        help="Equal-weighted: share of constituents-with-data agreeing with the average direction. "
        "Index-weighted: share of tracked index *weight* (not stock count) agreeing with it.",
    )
    b5.metric("Up / Down / Flat", f"{signal.n_up} / {signal.n_down} / {signal.n_flat}",
              help="Raw stock counts — unaffected by the weighting basis above.")
    if signal.n_with_data < signal.n_total:
        st.caption(f"Based on {signal.n_with_data} of {signal.n_total} constituents with a reference price so far.")


def _render_cas_history(stocks: dict) -> None:
    """A persistent log of real CAS outcomes (reference price vs. actual
    settled close) — see ``app.data.cas_history``'s module docstring. This
    is the only honest path to ever answering "what's the probability of a
    move like today's": a real empirical distribution built from actually
    logged sessions, not a fabricated formula. Starts empty; grows one row
    per symbol per click of "Log today's CAS outcome", once the auction has
    genuinely settled (after 3:35pm IST).
    """
    from app.data.cas_history import DEFAULT_LOG_PATH, load_records, record_session_outcome, summarize_history

    st.divider()
    st.subheader("CAS History")
    st.caption(
        "A persistent log of every session's actual outcome — the 3:00-3:15pm reference price vs. the day's "
        "real settled close — for NIFTY, SENSEX, and all tracked stocks. This is the only honest foundation "
        "for a real probability estimate: an empirical distribution built from actual logged sessions, not a "
        "fitted or backtested model. It starts empty and only grows when you log a session."
    )
    st.caption(
        "⚠️ On Streamlit Community Cloud, local files don't survive a redeploy or an idle sleep/wake cycle — "
        "use \"Download log (CSV)\" below periodically to keep a real backup, or this log can vanish."
    )

    log_symbols = ["NIFTY", "SENSEX"] + sorted(stocks.keys())
    if st.button(f"Log today's CAS outcome ({len(log_symbols)} symbols)"):
        logged, errors = [], []
        with st.spinner(f"Logging {len(log_symbols)} symbols…"):
            for sym in log_symbols:
                try:
                    record_session_outcome(sym)
                    logged.append(sym)
                except ValueError as exc:
                    errors.append(str(exc))
        if logged:
            st.success(f"Logged {len(logged)} symbol(s): {', '.join(logged)}")
        for msg in sorted(set(errors))[:3]:
            st.warning(msg)

    all_records = load_records()
    if not all_records:
        st.info("Nothing logged yet.")
        return

    if DEFAULT_LOG_PATH.exists():
        st.download_button(
            "Download log (CSV)", DEFAULT_LOG_PATH.read_bytes(),
            file_name="cas_session_log.csv", mime="text/csv",
        )

    logged_symbols = sorted({r.symbol for r in all_records})
    hist_symbol = st.selectbox(
        "Symbol", logged_symbols, index=logged_symbols.index("NIFTY") if "NIFTY" in logged_symbols else 0,
    )
    summary = summarize_history(hist_symbol)
    if summary is None:
        return

    h1, h2, h3 = st.columns(3)
    h1.metric("Sessions logged", summary.n_sessions)
    h2.metric("Mean move", f"{summary.mean_move_pct:+.2f}%")
    h3.metric(
        "Std dev", f"{summary.std_move_pct:.2f}%" if summary.std_move_pct is not None else "—",
        help="Needs at least 2 logged sessions.",
    )
    h4, h5, h6 = st.columns(3)
    h4.metric("Upside", f"{summary.pct_upside:.0f}%")
    h5.metric("Downside", f"{summary.pct_downside:.0f}%")
    h6.metric("Flat", f"{summary.pct_flat:.0f}%")
    st.caption(f"Range so far: {summary.min_move_pct:+.2f}% to {summary.max_move_pct:+.2f}%.")

    bucket_df = pd.DataFrame(
        {"Move size": list(summary.bucket_counts.keys()), "Sessions": list(summary.bucket_counts.values())}
    ).set_index("Move size")
    st.bar_chart(bucket_df)

    if summary.n_sessions < 20:
        st.caption(
            f"Only {summary.n_sessions} session(s) logged — nowhere near enough for a reliable distribution "
            "yet. Treat every number above as \"what's happened so far,\" not a forecast."
        )


def _render_live_auction_diagnostic(stocks: dict, symbol: str) -> None:
    """Whether Kite Connect's *API* exposes the same CAS-specific fields
    (reference price / indicative close / imbalance quantity) that Kite
    Web's own UI shows during the 3:15-3:35pm auction — genuinely
    unconfirmed (see the module docstring), and NOT something to guess at:
    there's no "equilibrium price" column in the Constituent Overview table
    above because computing a real one needs full order-book depth across
    the whole +/-3% band, which Kite's quote API doesn't provide even where
    it does expose *some* live auction data (its ``depth`` field is capped
    at 5 bid/ask levels) — replicating NSE's own equilibrium-price algorithm
    isn't possible from this data even with full field access confirmed.
    So this stays a diagnostic, not a data column: it tells you whether
    Kite exposes anything CAS-specific at all, not what the number is.
    """
    with st.expander("Live auction data (diagnostic)"):
        st.caption(
            "Attempts a raw Kite Connect quote for a stock's spot instrument, to check whether the API "
            "exposes any field beyond the usual quote shape — only meaningful between 3:15-3:35pm IST on a "
            "trading day. Even if it does, replicating NSE's own equilibrium-price calculation (maximum "
            "executable volume across the full order book) isn't possible from a 5-level depth snapshot, so "
            "this can at most confirm *whether* Kite exposes CAS data, not compute an equilibrium price "
            "column from it."
        )
        if st.button("Fetch live quote for the selected stock"):
            from app.data.kite_client import KiteAuthError
            from app.data.kite_feed import KiteFeedError, raw_underlying_quote

            try:
                quote = raw_underlying_quote(symbol)
            except (KiteFeedError, KiteAuthError) as exc:
                st.error(f"Couldn't fetch a live quote: {exc}")
            else:
                st.json(quote)
                extra_fields = set(quote.keys()) - _KNOWN_QUOTE_FIELDS
                if extra_fields:
                    st.success(f"Unrecognized field(s) present — worth checking if these are CAS data: {sorted(extra_fields)}")
                else:
                    st.warning(
                        "Only the usual quote fields are present — no CAS-specific field (reference price / "
                        "indicative close / imbalance quantity) found in this response."
                    )

        st.divider()
        st.caption(
            f"One stock at a time only tells you about that stock — Kite could expose CAS fields for some "
            f"tracked stocks and not others. This checks all {len(stocks)} at once (that many quote calls, "
            "rate-limited on the live plan) for a definitive answer across the whole tracked universe."
        )
        if st.button(f"Check all {len(stocks)} constituents for CAS fields"):
            from app.data.kite_client import KiteAuthError
            from app.data.kite_feed import KiteFeedError, raw_underlying_quote

            found: dict[str, list[str]] = {}
            errors: list[str] = []
            with st.spinner(f"Checking {len(stocks)} constituents…"):
                for sym in sorted(stocks):
                    try:
                        quote = raw_underlying_quote(sym)
                    except (KiteFeedError, KiteAuthError) as exc:
                        errors.append(f"{sym}: {exc}")
                        continue
                    extra_fields = sorted(set(quote.keys()) - _KNOWN_QUOTE_FIELDS)
                    if extra_fields:
                        found[sym] = extra_fields

            if found:
                st.success(f"Unrecognized field(s) found in {len(found)} of {len(stocks)} symbol(s):")
                st.json(found)
            else:
                st.warning(
                    f"No unrecognized fields found across all {len(stocks) - len(errors)} symbols that "
                    "returned a quote — Kite's API doesn't appear to expose CAS-specific fields, at least "
                    "not at this moment."
                )
            if errors:
                with st.expander(f"{len(errors)} symbol(s) failed to fetch"):
                    for msg in errors:
                        st.text(msg)
