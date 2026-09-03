import pytest

from app.data import feed
from app.data.kite_client import KiteAuthError, reset_kite_client


def test_default_provider_is_mock(monkeypatch):
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    assert feed.get_active_provider() == "mock"


def test_explicit_mock_provider(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    assert feed.get_active_provider() == "mock"


def test_explicit_kite_provider(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kite")
    assert feed.get_active_provider() == "kite"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "bloomberg")
    with pytest.raises(RuntimeError):
        feed.get_active_provider()


def test_mock_provider_dispatches_to_mock_feed(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    chain = feed.generate_option_chain("NIFTY")
    assert chain.symbol == "NIFTY"
    assert chain.risk_free_rate == feed.get_risk_free_rate()


def test_kite_provider_dispatches_to_kite_feed_and_surfaces_auth_error(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kite")
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    reset_kite_client()
    with pytest.raises(KiteAuthError):
        feed.generate_option_chain("NIFTY")
    reset_kite_client()


def test_generate_minute_series_mock_provider(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    series = feed.generate_minute_series("NIFTY", minutes=10)
    assert len(series) == 10


def test_futures_snapshot_mock_provider_shape(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    snap = feed.futures_snapshot("NIFTY")
    assert snap["symbol"] == "NIFTY"
    assert snap["last_price"] > 0
    assert snap["prev_close"] > 0
    assert snap["day_low"] <= snap["last_price"] <= snap["day_high"]
    assert snap["day_low"] <= snap["day_open"] <= snap["day_high"]
    assert snap["change_pts"] == pytest.approx(snap["last_price"] - snap["prev_close"])


def test_futures_snapshot_kite_provider_surfaces_auth_error(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kite")
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    reset_kite_client()
    with pytest.raises(KiteAuthError):
        feed.futures_snapshot("NIFTY")
    reset_kite_client()


def test_futures_minute_series_mock_provider_shape(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    series = feed.futures_minute_series("NIFTY", minutes=10)
    assert len(series) == 10
    assert all(p > 0 for _, p, _ in series)


def test_futures_minute_series_differs_from_spot_minute_series(monkeypatch):
    """Regression: futures_minute_series must be seeded separately from
    generate_minute_series — sharing a seed would make the Futures Monitor
    chart a pixel-identical copy of the (different-instrument) spot series.
    """
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    spot_series = feed.generate_minute_series("NIFTY", minutes=10)
    futures_series = feed.futures_minute_series("NIFTY", minutes=10)
    assert [p for _, p, _ in spot_series] != [p for _, p, _ in futures_series]


def test_futures_minute_series_kite_provider_surfaces_auth_error(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kite")
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    reset_kite_client()
    with pytest.raises(KiteAuthError):
        feed.futures_minute_series("NIFTY")
    reset_kite_client()
