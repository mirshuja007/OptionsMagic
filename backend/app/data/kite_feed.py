"""Live option-chain adapter backed by Zerodha Kite Connect.

Produces the same ``OptionChain``/``ChainRow``/``LegQuote`` shape as
``app.data.mock_feed``, so it's a drop-in replacement selected via
``app.data.feed`` (``MARKET_DATA_PROVIDER=kite``) — nothing downstream
(analytics, the strategy generator, the solver, margin, backtest) changes.

Kite gives us LTP/OI/volume/bid-ask depth per contract but not IV or Greeks
directly — those are computed here with our own ``core.black_scholes``,
solving implied vol from the live LTP and feeding it back through the same
Greeks formulas used everywhere else in the platform, so Research Mode
numbers are internally consistent regardless of which feed is active.

Known gaps, tracked rather than hidden:
  * Kite's quote payload has point-in-time OI but not OI *change* — that
    needs a daily OI snapshot cache to diff against, which isn't wired up
    yet, so ``LegQuote.oi_change`` is always 0 from this adapter.
  * ``generate_minute_series`` calls the Historical Data API, a separate
    paid add-on on top of the base Connect subscription (see README).
  * Index tradingsymbols (``Instrument.kite_spot_tradingsymbol`` /
    ``kite_underlying_name``) are best-effort and must be verified against
    a live ``kite.instruments()`` pull before going live — a mismatch here
    raises ``KiteFeedError`` rather than silently returning wrong data.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import requests
from kiteconnect.exceptions import KiteException

_KiteTransportError = (KiteException, requests.exceptions.RequestException)

from app.core.black_scholes import Greeks, OptionType, greeks as bs_greeks, implied_volatility
from app.data.instruments import Instrument, get_instrument
from app.data.kite_client import get_kite_client
from app.data.models import ChainRow, LegQuote, OptionChain

RISK_FREE_RATE = 0.065

DEFAULT_IV_FALLBACK = 0.20  # used only if a strike's IV can't be solved (e.g. zero/stale LTP)

_INSTRUMENT_CACHE_TTL_SECONDS = 12 * 3600
_instrument_cache: dict[str, tuple[float, list[dict]]] = {}


class KiteFeedError(RuntimeError):
    """Raised when Kite Connect can't serve a usable chain: auth failure,
    network error, or a symbol-mapping mismatch producing an empty result.
    """


def clear_instrument_cache() -> None:
    """Drop the cached NFO/BFO instrument dump, forcing a refetch on next use."""
    _instrument_cache.clear()


def _instrument_dump(exchange: str) -> list[dict]:
    now = time.time()
    cached = _instrument_cache.get(exchange)
    if cached is not None and now - cached[0] < _INSTRUMENT_CACHE_TTL_SECONDS:
        return cached[1]

    kite = get_kite_client()
    try:
        rows = kite.instruments(exchange)
    except _KiteTransportError as exc:
        raise KiteFeedError(f"Failed to fetch {exchange} instrument dump: {exc}") from exc

    _instrument_cache[exchange] = (now, rows)
    return rows


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _option_rows_for(instrument: Instrument) -> list[dict]:
    dump = _instrument_dump(instrument.kite_options_exchange)
    rows = [
        r
        for r in dump
        if r.get("name") == instrument.kite_underlying_name and r.get("instrument_type") in ("CE", "PE")
    ]
    if not rows:
        raise KiteFeedError(
            f"No {instrument.kite_options_exchange} option contracts found for "
            f"name='{instrument.kite_underlying_name}'. Verify Instrument.kite_underlying_name "
            f"against a live kite.instruments('{instrument.kite_options_exchange}') pull."
        )
    return rows


def _effective_expiry_cutoff_date(instrument: Instrument, as_of: datetime) -> date:
    """Once today's session has closed, today's own expiry (if it has one)
    is already settled, not a live tradeable contract — picking it as the
    "default/nearest" expiry after the close produces a near-zero
    time-to-expiry, which makes IV solving numerically degenerate (wild
    implied vols, decay curves flattened to intrinsic value only). Treat
    "today" as already past once the session has ended.
    """
    if as_of.time() >= instrument.session_end:
        return as_of.date() + timedelta(days=1)
    return as_of.date()


def available_expiries(symbol: str) -> list[date]:
    """Every expiry currently listed for this underlying, ascending —
    excluding one that's dated today but already past today's session
    close (see ``_effective_expiry_cutoff_date``), so the frontend's expiry
    selector doesn't default to an already-settled contract.
    """
    instrument = get_instrument(symbol)
    rows = _option_rows_for(instrument)
    all_expiries = sorted({_as_date(r["expiry"]) for r in rows})
    cutoff = _effective_expiry_cutoff_date(instrument, datetime.now())
    upcoming = [e for e in all_expiries if e >= cutoff]
    return upcoming or all_expiries


def _pick_expiry(expiries: list[date], requested: date | None, as_of: date) -> date:
    if requested is not None:
        if requested not in expiries:
            raise KiteFeedError(f"Expiry {requested} not listed. Available: {expiries[:5]}")
        return requested
    upcoming = [e for e in expiries if e >= as_of]
    if not upcoming:
        latest = expiries[-1] if expiries else "none"
        raise KiteFeedError(f"No upcoming expiries found; latest listed was {latest}")
    return upcoming[0]


def _select_strike_rows(rows: list[dict], expiry: date, spot: float, num_strikes: int) -> dict[float, dict[str, dict]]:
    """{strike: {"CE": instrument_row, "PE": instrument_row}} for the
    ``num_strikes`` strikes nearest the money, restricted to ``expiry``.
    """
    by_strike: dict[float, dict[str, dict]] = {}
    for r in rows:
        if _as_date(r["expiry"]) != expiry:
            continue
        by_strike.setdefault(float(r["strike"]), {})[r["instrument_type"]] = r

    complete_strikes = [k for k, v in by_strike.items() if "CE" in v and "PE" in v]
    if not complete_strikes:
        raise KiteFeedError(f"No complete CE/PE strike pairs found for expiry {expiry}")

    nearest = sorted(complete_strikes, key=lambda k: abs(k - spot))[: max(num_strikes, 1)]
    return {k: by_strike[k] for k in sorted(nearest)}


def _quote_key(instrument: Instrument, tradingsymbol: str) -> str:
    return f"{instrument.kite_options_exchange}:{tradingsymbol}"


def _quote_underlying(kite, key: str) -> tuple[float, float]:
    """(last_price, previous_close) for a spot/futures instrument.
    ``kite.quote()`` (unlike ``kite.ltp()``) includes ``ohlc.close``, which
    is the *previous trading day's* close — exactly what's needed for a
    day-over-day change figure, and not derivable from LTP alone.
    """
    try:
        resp = kite.quote(key)
    except _KiteTransportError as exc:
        raise KiteFeedError(f"Failed to fetch quote for {key}: {exc}") from exc
    try:
        entry = resp[key]
        return float(entry["last_price"]), float(entry["ohlc"]["close"])
    except KeyError as exc:
        raise KiteFeedError(f"Unexpected quote response shape for {key}: {resp}") from exc


def _futures_price(kite, instrument: Instrument, options_expiry: date) -> tuple[float, float]:
    """Commodity options are options on a futures contract, not a spot
    index — resolve the futures contract this options expiry references
    (the nearest one expiring on/after the options expiry) and use its
    quote (last price, previous close) as the pricing underlying.
    """
    dump = _instrument_dump(instrument.kite_spot_exchange)
    fut_rows = [
        r for r in dump if r.get("name") == instrument.kite_underlying_name and r.get("instrument_type") == "FUT"
    ]
    if not fut_rows:
        raise KiteFeedError(
            f"No FUT contracts found for name='{instrument.kite_underlying_name}' on "
            f"{instrument.kite_spot_exchange}; verify Instrument.kite_underlying_name."
        )
    candidates = sorted(fut_rows, key=lambda r: _as_date(r["expiry"]))
    match = next((r for r in candidates if _as_date(r["expiry"]) >= options_expiry), candidates[-1])
    key = f"{instrument.kite_spot_exchange}:{match['tradingsymbol']}"
    return _quote_underlying(kite, key)


def _underlying_price(kite, instrument: Instrument, resolved_expiry: date) -> tuple[float, float]:
    if instrument.is_commodity:
        return _futures_price(kite, instrument, resolved_expiry)
    key = f"{instrument.kite_spot_exchange}:{instrument.kite_spot_tradingsymbol}"
    return _quote_underlying(kite, key)


def _quote_to_leg(
    quote: dict,
    spot: float,
    strike: float,
    t: float,
    r: float,
    option_type: OptionType,
    q: float = 0.0,
    tradingsymbol: str = "",
) -> LegQuote:
    ltp = float(quote.get("last_price") or 0.0)
    depth = quote.get("depth") or {}
    buy_levels = depth.get("buy") or []
    sell_levels = depth.get("sell") or []
    bid = float(buy_levels[0]["price"]) if buy_levels and buy_levels[0].get("price") else 0.0
    ask = float(sell_levels[0]["price"]) if sell_levels and sell_levels[0].get("price") else 0.0
    if bid <= 0:
        bid = max(ltp - 0.05, 0.05)
    if ask <= 0:
        ask = ltp + 0.05 if ltp > 0 else bid + 0.10

    mid = (bid + ask) / 2.0
    iv = implied_volatility(mid, spot, strike, t, r, option_type, q) if mid > 0 and t > 0 else None
    if iv is None:
        iv = DEFAULT_IV_FALLBACK
    g = bs_greeks(spot, strike, t, r, iv, option_type, q) if t > 0 else Greeks(0.0, 0.0, 0.0, 0.0, 0.0)

    return LegQuote(
        option_type=option_type,
        ltp=round(ltp, 2),
        bid=round(bid, 2),
        ask=round(ask, 2),
        oi=int(quote.get("oi") or 0),
        oi_change=0,  # see module docstring: not available from quote() alone
        volume=int(quote.get("volume") or 0),
        iv=round(iv, 4),
        greeks=g,
        tradingsymbol=tradingsymbol,
    )


def generate_option_chain(
    symbol: str,
    expiry: date | None = None,
    num_strikes: int = 21,
    as_of: datetime | None = None,
) -> OptionChain:
    instrument = get_instrument(symbol)
    kite = get_kite_client()
    as_of = as_of or datetime.now()

    option_rows = _option_rows_for(instrument)
    expiries = sorted({_as_date(r["expiry"]) for r in option_rows})
    # _pick_expiry ignores this cutoff when `expiry` is explicitly requested
    # (it just validates the requested date is listed) — only the "pick the
    # nearest upcoming" default path needs the session-aware cutoff.
    cutoff = _effective_expiry_cutoff_date(instrument, as_of)
    resolved_expiry = _pick_expiry(expiries, expiry, cutoff)

    spot, prev_close = _underlying_price(kite, instrument, resolved_expiry)
    strike_map = _select_strike_rows(option_rows, resolved_expiry, spot, num_strikes)

    expiry_dt = datetime.combine(resolved_expiry, instrument.session_end)
    t = max((expiry_dt - as_of).total_seconds() / (365.0 * 24 * 3600), 1e-6)
    q = RISK_FREE_RATE if instrument.pricing_carry_rate_equals_risk_free else 0.0

    token_map: dict[str, tuple[float, str, str]] = {}
    for strike, legs in strike_map.items():
        for side in ("CE", "PE"):
            tradingsymbol = legs[side]["tradingsymbol"]
            key = _quote_key(instrument, tradingsymbol)
            token_map[key] = (strike, side, tradingsymbol)

    try:
        quotes = kite.quote(list(token_map.keys()))
    except _KiteTransportError as exc:
        raise KiteFeedError(f"Failed to fetch quotes for {instrument.symbol} chain: {exc}") from exc

    rows_by_strike: dict[float, dict[str, LegQuote]] = {}
    for key, (strike, side, tradingsymbol) in token_map.items():
        quote = quotes.get(key)
        if quote is None:
            continue
        option_type = OptionType.CALL if side == "CE" else OptionType.PUT
        rows_by_strike.setdefault(strike, {})[side] = _quote_to_leg(
            quote, spot, strike, t, RISK_FREE_RATE, option_type, q, tradingsymbol
        )

    rows = [
        ChainRow(strike=strike, call=legs["CE"], put=legs["PE"])
        for strike, legs in sorted(rows_by_strike.items())
        if "CE" in legs and "PE" in legs
    ]
    if not rows:
        raise KiteFeedError(f"Kite returned no usable quotes for {instrument.symbol} {resolved_expiry}")

    return OptionChain(
        symbol=instrument.symbol,
        spot=spot,
        expiry=resolved_expiry,
        timestamp=as_of,
        time_to_expiry_years=t,
        risk_free_rate=RISK_FREE_RATE,
        prev_close=prev_close,
        rows=rows,
    )


def generate_minute_series(
    symbol: str,
    session_date: date | None = None,
    minutes: int = 375,
) -> list[tuple[datetime, float, int]]:
    """Real minute-by-minute (underlying close, volume) for a past session,
    via Kite's Historical Data API (a separate paid add-on — see README).
    """
    instrument = get_instrument(symbol)
    kite = get_kite_client()
    session_date = session_date or date.today()

    if instrument.is_commodity:
        # No single static underlying instrument — use whichever futures
        # contract was front-month as of session_date.
        dump = _instrument_dump(instrument.kite_spot_exchange)
        fut_rows = [
            r for r in dump if r.get("name") == instrument.kite_underlying_name and r.get("instrument_type") == "FUT"
        ]
        candidates = sorted(fut_rows, key=lambda r: _as_date(r["expiry"]))
        match = next((r for r in candidates if _as_date(r["expiry"]) >= session_date), candidates[-1] if candidates else None)
    else:
        dump = _instrument_dump(instrument.kite_spot_exchange)
        match = next((r for r in dump if r.get("tradingsymbol") == instrument.kite_spot_tradingsymbol), None)

    if match is None:
        raise KiteFeedError(
            f"No underlying instrument found for {symbol} on {instrument.kite_spot_exchange}; "
            f"verify Instrument.kite_underlying_name / kite_spot_tradingsymbol."
        )

    start = datetime.combine(session_date, instrument.session_start)
    end = datetime.combine(session_date, instrument.session_end)
    if session_date == date.today():
        # Requesting a still-in-progress (or not-yet-started) session's full
        # end-of-day range returns no candles at all — Kite has nothing to
        # give for minutes that haven't happened yet. Cap the request at
        # "now" instead, and give a clear, specific error if the session
        # hasn't opened yet rather than a confusing empty result.
        now = datetime.now()
        if now < start:
            raise KiteFeedError(
                f"Market hasn't opened yet today for {symbol} (session starts {instrument.session_start.strftime('%H:%M')}); "
                "no intraday minute data available until then."
            )
        end = min(end, now)

    try:
        candles = kite.historical_data(match["instrument_token"], start, end, interval="minute")
    except _KiteTransportError as exc:
        raise KiteFeedError(f"Historical data request failed for {symbol} on {session_date}: {exc}") from exc

    if not candles:
        raise KiteFeedError(f"No historical minute data returned for {symbol} on {session_date}")

    return [(c["date"].replace(tzinfo=None), float(c["close"]), int(c.get("volume") or 0)) for c in candles[:minutes]]
