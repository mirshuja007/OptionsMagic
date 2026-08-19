from datetime import date, datetime, time as dtime, timedelta

import pytest

from app.core.black_scholes import OptionType, price as bs_price
from app.data import kite_feed
from app.data.kite_client import KiteAuthError, get_kite_client, reset_kite_client
from app.data.instruments import get_instrument
from app.data.kite_feed import (
    DEFAULT_IV_FALLBACK,
    KiteFeedError,
    _as_date,
    _effective_expiry_cutoff_date,
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


def test_effective_expiry_cutoff_before_session_end_is_today():
    nifty = get_instrument("NIFTY")  # session_end 15:30
    as_of = datetime(2026, 8, 18, 10, 0)  # 10:00 AM, well before close
    assert _effective_expiry_cutoff_date(nifty, as_of) == date(2026, 8, 18)


def test_effective_expiry_cutoff_after_session_end_rolls_to_tomorrow():
    nifty = get_instrument("NIFTY")
    as_of = datetime(2026, 8, 18, 18, 41)  # 6:41 PM, well after the 15:30 close
    assert _effective_expiry_cutoff_date(nifty, as_of) == date(2026, 8, 19)


def test_effective_expiry_cutoff_exactly_at_session_end_rolls_to_tomorrow():
    nifty = get_instrument("NIFTY")
    as_of = datetime(2026, 8, 18, 15, 30)
    assert _effective_expiry_cutoff_date(nifty, as_of) == date(2026, 8, 19)


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
    def __init__(self, nfo_rows, spot_ltp, quote_map, historical_candles=None, extra_dumps=None, ltp_map=None):
        self._nfo_rows = nfo_rows
        self._spot_ltp = spot_ltp
        self._quote_map = quote_map
        self._historical_candles = historical_candles or []
        self._extra_dumps = extra_dumps or {}
        self._ltp_map = ltp_map or {}

    def instruments(self, exchange):
        if exchange in self._extra_dumps:
            return self._extra_dumps[exchange]
        if exchange == "NFO":
            return self._nfo_rows
        return [{"tradingsymbol": "NIFTY 50", "instrument_token": 999, "name": "NIFTY"}]

    def ltp(self, key):
        return {key: {"last_price": self._ltp_map.get(key, self._spot_ltp)}}

    def quote(self, keys):
        # Real kite.quote() accepts either a single instrument string or a
        # list of them; _quote_underlying calls it with a single string.
        key_list = [keys] if isinstance(keys, str) else keys
        return {k: self._quote_map[k] for k in key_list if k in self._quote_map}

    def historical_data(self, instrument_token, from_date, to_date, interval):
        self.last_historical_call = {"from_date": from_date, "to_date": to_date, "interval": interval}
        return self._historical_candles


def _build_fake_chain_fixtures(spot=24800.0, expiry=None, prev_close=24700.0):
    expiry = expiry or (date.today() + timedelta(days=4))
    strikes = [24700, 24750, 24800, 24850, 24900]
    nfo_rows = []
    quote_map = {"NSE:NIFTY 50": {"last_price": spot, "ohlc": {"close": prev_close}}}
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


def test_generate_option_chain_skips_todays_expiry_after_session_close(monkeypatch):
    """Regression test: if today (2026-08-18) is itself a listed expiry but
    the session has already closed (as_of is 18:41, well past NIFTY's 15:30
    close), the default (no explicit expiry requested) pick must roll to
    the next listed expiry, not settle on today's already-closed contract
    (which would otherwise floor time-to-expiry near zero and make IV
    solving degenerate).
    """
    today_expiry = date(2026, 8, 18)
    next_expiry = date(2026, 8, 25)
    spot = 24150.0
    strikes = [24100, 24150, 24200]
    nfo_rows = []
    quote_map = {"NSE:NIFTY 50": {"last_price": spot, "ohlc": {"close": 24100.0}}}
    for expiry in (today_expiry, next_expiry):
        for strike in strikes:
            for side in ("CE", "PE"):
                symbol = f"NIFTY_{expiry.isoformat()}_{strike}_{side}"
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

    fake = FakeKite(nfo_rows, spot, quote_map)
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    as_of = datetime(2026, 8, 18, 18, 41)  # well after today's 15:30 close
    chain = kite_feed.generate_option_chain("NIFTY", as_of=as_of)

    assert chain.expiry == next_expiry


def test_generate_option_chain_end_to_end(monkeypatch):
    spot = 24810.0
    nfo_rows, quote_map, expiry = _build_fake_chain_fixtures(spot=spot, prev_close=24700.0)
    fake = FakeKite(nfo_rows, spot, quote_map)
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    chain = kite_feed.generate_option_chain("NIFTY", num_strikes=3)

    assert chain.symbol == "NIFTY"
    assert chain.spot == spot
    assert chain.prev_close == 24700.0
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
    # A fixed past date, not date.today() — generate_minute_series caps
    # "today" requests at the current time, which would make this test's
    # outcome depend on what time of day it happens to run.
    session_date = date(2026, 8, 17)
    candles = [
        {
            "date": datetime.combine(session_date, datetime.min.time()) + timedelta(hours=9, minutes=15 + i),
            "close": 24800.0 + i,
            "volume": 1000 + i,
        }
        for i in range(5)
    ]
    fake = FakeKite(nfo_rows=[], spot_ltp=0.0, quote_map={}, historical_candles=candles)
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    series = kite_feed.generate_minute_series("NIFTY", session_date=session_date, minutes=5)

    assert len(series) == 5
    assert series[0][1] == 24800.0
    assert series[0][2] == 1000
    assert series[0][0].tzinfo is None


def test_generate_minute_series_defaults_volume_to_zero_when_absent(monkeypatch):
    session_date = date(2026, 8, 17)  # fixed past date — see comment above
    candles = [
        {"date": datetime.combine(session_date, datetime.min.time()) + timedelta(hours=9, minutes=15), "close": 24800.0}
    ]
    fake = FakeKite(nfo_rows=[], spot_ltp=0.0, quote_map={}, historical_candles=candles)
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    series = kite_feed.generate_minute_series("NIFTY", session_date=session_date, minutes=1)
    assert series[0][2] == 0


class _FrozenDateTime(datetime):
    """A ``datetime`` subclass whose ``now()`` returns a fixed instant —
    used to test kite_feed's "cap the historical_data request at now"
    behavior without depending on when the test suite actually runs.
    """

    _frozen_now: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen_now


def test_generate_minute_series_raises_clear_error_before_market_open(monkeypatch):
    today = date.today()
    _FrozenDateTime._frozen_now = datetime.combine(today, dtime(8, 0))  # NIFTY opens 09:15
    monkeypatch.setattr(kite_feed, "datetime", _FrozenDateTime)

    fake = FakeKite(nfo_rows=[], spot_ltp=0.0, quote_map={}, historical_candles=[])
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    with pytest.raises(KiteFeedError, match="hasn't opened yet"):
        kite_feed.generate_minute_series("NIFTY", session_date=today)


def test_generate_minute_series_caps_request_at_now_for_todays_in_progress_session(monkeypatch):
    today = date.today()
    frozen_now = datetime.combine(today, dtime(10, 30))  # mid-session, well before 15:30 close
    _FrozenDateTime._frozen_now = frozen_now
    monkeypatch.setattr(kite_feed, "datetime", _FrozenDateTime)

    candles = [{"date": datetime.combine(today, dtime(9, 15)), "close": 24800.0, "volume": 100}]
    fake = FakeKite(nfo_rows=[], spot_ltp=0.0, quote_map={}, historical_candles=candles)
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    kite_feed.generate_minute_series("NIFTY", session_date=today)

    assert fake.last_historical_call["to_date"] == frozen_now
    assert fake.last_historical_call["to_date"] < datetime.combine(today, dtime(15, 30))


def test_available_expiries_returns_sorted_unique_listed_dates(monkeypatch):
    nfo_rows, _, _ = _build_fake_chain_fixtures()
    later_expiry = date.today() + timedelta(days=11)
    nfo_rows.append(_instrument_row(24800, "CE", later_expiry, "NIFTY_24800_CE_2"))
    fake = FakeKite(nfo_rows=nfo_rows, spot_ltp=24800.0, quote_map={})
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    expiries = kite_feed.available_expiries("NIFTY")
    assert expiries == sorted(expiries)
    assert later_expiry in expiries


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


# ---------------------------------------------------------------------------
# Commodity (options-on-futures) underlying resolution
# ---------------------------------------------------------------------------


def _fut_row(expiry: date, tradingsymbol: str, token: int) -> dict:
    return {"tradingsymbol": tradingsymbol, "name": "CRUDEOIL", "expiry": expiry, "instrument_type": "FUT", "instrument_token": token}


def test_futures_price_picks_nearest_contract_on_or_after_options_expiry(monkeypatch):
    from app.data.instruments import get_instrument
    from app.data.kite_feed import _futures_price

    near_expiry = date(2026, 8, 19)
    far_expiry = date(2026, 9, 19)
    fut_rows = [_fut_row(near_expiry, "CRUDEOIL26AUGFUT", 1), _fut_row(far_expiry, "CRUDEOIL26SEPFUT", 2)]
    fake = FakeKite(
        nfo_rows=[], spot_ltp=0.0,
        quote_map={
            "MCX:CRUDEOIL26AUGFUT": {"last_price": 6100.0, "ohlc": {"close": 6050.0}},
            "MCX:CRUDEOIL26SEPFUT": {"last_price": 6250.0, "ohlc": {"close": 6200.0}},
        },
        extra_dumps={"MCX": fut_rows},
    )
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    instrument = get_instrument("CRUDEOIL")
    # An options expiry between the two contracts should resolve to the September future.
    result = _futures_price(fake, instrument, date(2026, 8, 25))
    assert result == (6250.0, 6200.0)

    kite_feed.clear_instrument_cache()
    result_near = _futures_price(fake, instrument, date(2026, 8, 10))
    assert result_near == (6100.0, 6050.0)


def _build_fake_commodity_fixtures(spot=6200.0, expiry=None, prev_close=6150.0):
    from app.data.instruments import get_instrument

    instrument = get_instrument("CRUDEOIL")
    expiry = expiry or (date.today() + timedelta(days=15))
    strikes = [6100, 6150, 6200, 6250, 6300]
    mcx_rows = [_fut_row(expiry + timedelta(days=5), "CRUDEOIL26AUGFUT", 1)]
    quote_map = {"MCX:CRUDEOIL26AUGFUT": {"last_price": spot, "ohlc": {"close": prev_close}}}
    for strike in strikes:
        for side in ("CE", "PE"):
            symbol = f"CRUDEOIL_{strike}_{side}"
            mcx_rows.append(_instrument_row(strike, side, expiry, symbol))
            mcx_rows[-1]["name"] = instrument.kite_underlying_name
            key = f"MCX:{symbol}"
            option_type = OptionType.CALL if side == "CE" else OptionType.PUT
            theo = bs_price(spot, strike, 0.05, 0.065, 0.35, option_type, q=0.065)
            quote_map[key] = {
                "last_price": theo,
                "oi": 500,
                "volume": 200,
                "depth": {"buy": [{"price": theo - 0.5}], "sell": [{"price": theo + 0.5}]},
            }
    return mcx_rows, quote_map, expiry


def test_generate_option_chain_for_commodity_uses_futures_underlying(monkeypatch):
    spot = 6210.0
    mcx_rows, quote_map, expiry = _build_fake_commodity_fixtures(spot=spot, prev_close=6150.0)
    fake = FakeKite(
        nfo_rows=[], spot_ltp=0.0, quote_map=quote_map,
        extra_dumps={"MCX": mcx_rows},
    )
    monkeypatch.setattr(kite_feed, "get_kite_client", lambda: fake)
    kite_feed.clear_instrument_cache()

    chain = kite_feed.generate_option_chain("CRUDEOIL", num_strikes=3)

    assert chain.symbol == "CRUDEOIL"
    assert chain.spot == spot
    assert chain.prev_close == 6150.0
    assert len(chain.rows) == 3
    for row in chain.rows:
        # Loose tolerance: the fixture's quotes were built at a fixed t while
        # the chain computes its own t from expiry - now, so exact IV
        # round-tripping isn't the point here (test_commodity_pricing.py
        # covers Black-76 correctness precisely) — this just checks the
        # futures-underlying wiring produces a sane, positive IV.
        assert 0.1 < row.call.iv < 0.6
