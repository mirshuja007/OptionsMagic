from datetime import date, datetime, timedelta

import pytest

from app.core.black_scholes import OptionType, price as bs_price
from app.data import kite_feed
from app.data.kite_client import KiteAuthError, get_kite_client, reset_kite_client
from app.data.kite_feed import (
    DEFAULT_IV_FALLBACK,
    KiteFeedError,
    _as_date,
    _pick_expiry,
    _quote_to_leg,
    _select_strike_rows,
)


# ---------------------------------------------------------------------------
# Pure transform functions — no network, no fake client needed
# ---------------------------------------------------------------------------


def test_as_date_handles_date_datetime_and_string():
    d = date(2026, 8, 20)
    assert _as_date(d) == d
    assert _as_date(datetime(2026, 8, 20, 15, 30)) == d
    assert _as_date("2026-08-20") == d


def test_pick_expiry_returns_requested_when_listed():
    expiries = [date(2026, 8, 20), date(2026, 8, 27)]
    assert _pick_expiry(expiries, date(2026, 8, 27), date(2026, 8, 16)) == date(2026, 8, 27)


def test_pick_expiry_rejects_unlisted_requested_expiry():
    expiries = [date(2026, 8, 20)]
    with pytest.raises(KiteFeedError):
        _pick_expiry(expiries, date(2026, 9, 1), date(2026, 8, 16))


def test_pick_expiry_picks_nearest_upcoming_when_none_requested():
    expiries = [date(2026, 8, 13), date(2026, 8, 20), date(2026, 8, 27)]
    assert _pick_expiry(expiries, None, date(2026, 8, 16)) == date(2026, 8, 20)


def test_pick_expiry_raises_when_all_expiries_are_past():
    expiries = [date(2026, 8, 1)]
    with pytest.raises(KiteFeedError):
        _pick_expiry(expiries, None, date(2026, 8, 16))


def _instrument_row(strike: float, instrument_type: str, expiry: date, tradingsymbol: str) -> dict:
    return {
        "tradingsymbol": tradingsymbol,
        "name": "NIFTY",
        "expiry": expiry,
        "strike": strike,
        "instrument_type": instrument_type,
    }


def test_select_strike_rows_filters_expiry_and_picks_nearest_to_spot():
    expiry = date(2026, 8, 20)
    other_expiry = date(2026, 8, 27)
    rows = []
    for strike in [24600, 24700, 24800, 24900, 25000]:
        rows.append(_instrument_row(strike, "CE", expiry, f"NIFTY26820{strike}CE"))
        rows.append(_instrument_row(strike, "PE", expiry, f"NIFTY26820{strike}PE"))
    # A different expiry's strikes must be excluded even if closer to spot.
    rows.append(_instrument_row(24750, "CE", other_expiry, "NIFTY26827_24750CE"))
    rows.append(_instrument_row(24750, "PE", other_expiry, "NIFTY26827_24750PE"))

    selected = _select_strike_rows(rows, expiry, spot=24810.0, num_strikes=3)

    assert set(selected.keys()) == {24700.0, 24800.0, 24900.0}
    for strike, legs in selected.items():
        assert legs["CE"]["expiry"] == expiry
        assert legs["PE"]["expiry"] == expiry


def test_select_strike_rows_requires_complete_ce_pe_pairs():
    expiry = date(2026, 8, 20)
    rows = [_instrument_row(24800, "CE", expiry, "NIFTY24800CE")]  # no matching PE
    with pytest.raises(KiteFeedError):
        _select_strike_rows(rows, expiry, spot=24800.0, num_strikes=3)


def test_quote_to_leg_solves_iv_consistent_with_black_scholes():
    spot, strike, t, r = 24800.0, 24800.0, 0.05, 0.065
    true_iv = 0.15
    theo = bs_price(spot, strike, t, r, true_iv, OptionType.CALL)
    quote = {
        "last_price": theo,
        "oi": 12345,
        "volume": 6789,
        "depth": {
            "buy": [{"price": theo - 0.5}],
            "sell": [{"price": theo + 0.5}],
        },
    }
    leg = _quote_to_leg(quote, spot, strike, t, r, OptionType.CALL)
    assert leg.iv == pytest.approx(true_iv, abs=0.01)
    assert leg.oi == 12345
    assert leg.volume == 6789
    assert leg.oi_change == 0  # documented gap: not available from quote()
    assert leg.greeks.delta > 0


def test_quote_to_leg_falls_back_to_default_iv_when_price_unsolvable():
    quote = {"last_price": 0.0, "oi": 0, "volume": 0, "depth": {}}
    leg = _quote_to_leg(quote, spot=24800.0, strike=24800.0, t=0.05, r=0.065, option_type=OptionType.CALL)
    assert leg.iv == DEFAULT_IV_FALLBACK


def test_quote_to_leg_falls_back_to_ltp_when_depth_missing():
    quote = {"last_price": 100.0, "oi": 10, "volume": 5, "depth": {}}
    leg = _quote_to_leg(quote, spot=24800.0, strike=24800.0, t=0.05, r=0.065, option_type=OptionType.PUT)
    assert leg.bid > 0
    assert leg.ask > leg.bid


# ---------------------------------------------------------------------------
# End-to-end generate_option_chain / generate_minute_series against a fake client
# ---------------------------------------------------------------------------


class FakeKite:
    def __init__(self, nfo_rows, spot_ltp, quote_map, historical_candles=None):
        self._nfo_rows = nfo_rows
        self._spot_ltp = spot_ltp
        self._quote_map = quote_map
        self._historical_candles = historical_candles or []

    def instruments(self, exchange):
        if exchange == "NFO":
            return self._nfo_rows
        return [{"tradingsymbol": "NIFTY 50", "instrument_token": 999, "name": "NIFTY"}]

    def ltp(self, key):
        return {key: {"last_price": self._spot_ltp}}

    def quote(self, keys):
        return {k: self._quote_map[k] for k in keys if k in self._quote_map}

    def historical_data(self, instrument_token, from_date, to_date, interval):
        return self._historical_candles


def _build_fake_chain_fixtures(spot=24800.0, expiry=None):
    expiry = expiry or (date.today() + timedelta(days=4))
    strikes = [24700, 24750, 24800, 24850, 24900]
    nfo_rows = []
    quote_map = {}
    for strike in strikes:
        for side in ("CE", "PE"):
            symbol = f"NIFTY_{strike}_{side}"
            nfo_rows.append(_instrument_row(strike, side, expiry, symbol))
            key = f"NFO:{symbol}"
            option_type = OptionType.CALL if side == "CE" else OptionType.PUT
            theo = bs_price(spot, strike, 0.05, 0.065, 0.15, option_type)
            quote_map[key] = {
                "last_price": theo,
                "oi": 1000,
                "volume": 500,
                "depth": {"buy": [{"price": theo - 0.5}], "sell": [{"price": theo + 0.5}]},
            }
    return nfo_rows, quote_map, expiry


def test_generate_option_chain_end_to_end(monkeypatch):
    spot = 24810.0
    nfo_rows, quote_map, expiry = _build_fake_chain_fixtures(spot=spot)
    fake = FakeKite(nfo_rows, spot, quote_map)
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    chain = kite_feed.generate_option_chain("NIFTY", num_strikes=3)

    assert chain.symbol == "NIFTY"
    assert chain.spot == spot
    assert chain.expiry == expiry
    assert len(chain.rows) == 3
    for row in chain.rows:
        assert row.call.iv > 0
        assert row.put.iv > 0
        assert row.call.oi_change == 0


def test_generate_option_chain_raises_kite_feed_error_on_unmapped_underlying(monkeypatch):
    fake = FakeKite(nfo_rows=[], spot_ltp=100.0, quote_map={})
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    with pytest.raises(KiteFeedError):
        kite_feed.generate_option_chain("NIFTY")


def test_generate_minute_series_end_to_end(monkeypatch):
    session_date = date.today()
    candles = [
        {"date": datetime.combine(session_date, datetime.min.time()) + timedelta(hours=9, minutes=15 + i), "close": 24800.0 + i}
        for i in range(5)
    ]
    fake = FakeKite(nfo_rows=[], spot_ltp=0.0, quote_map={}, historical_candles=candles)
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    series = kite_feed.generate_minute_series("NIFTY", session_date=session_date, minutes=5)

    assert len(series) == 5
    assert series[0][1] == 24800.0
    assert series[0][0].tzinfo is None


# ---------------------------------------------------------------------------
# kite_client auth
# ---------------------------------------------------------------------------


def test_get_kite_client_raises_auth_error_without_env_vars(monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    reset_kite_client()
    with pytest.raises(KiteAuthError):
        get_kite_client()
    reset_kite_client()


def test_get_kite_client_succeeds_and_caches_with_env_vars(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "test_key")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "test_token")
    reset_kite_client()
    client_a = get_kite_client()
    client_b = get_kite_client()
    assert client_a is client_b  # cached for process lifetime
    reset_kite_client()
