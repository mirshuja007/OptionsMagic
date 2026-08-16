import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.data.kite_client import reset_kite_client

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data_provider"] == "mock"


def test_list_instruments():
    r = client.get("/api/instruments")
    assert r.status_code == 200
    symbols = {i["symbol"] for i in r.json()}
    assert "NIFTY" in symbols
    assert "BANKNIFTY" in symbols


def test_option_chain_endpoint():
    r = client.get("/api/option-chain/NIFTY")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "NIFTY"
    assert len(body["rows"]) > 0


def test_option_chain_unknown_symbol_404():
    r = client.get("/api/option-chain/NOTASYMBOL")
    assert r.status_code == 404


def test_analytics_endpoints():
    for path in ["max-pain", "pcr", "oi", "volatility", "straddle"]:
        r = client.get(f"/api/analytics/{path}/NIFTY")
        assert r.status_code == 200, path


def test_strategy_discover_endpoint():
    payload = {
        "symbol": "NIFTY",
        "constraints": {
            "min_probability_of_profit": 0.6,
            "min_yield_pct": 0.005,
            "max_profit_cap": 50000,
            "max_loss_cap": 20000,
            "margin_cap": 500000,
        },
    }
    r = client.post("/api/strategy/discover", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "NIFTY"
    assert len(body["results"]) <= 3


def test_instruments_endpoint_includes_commodities_with_asset_class():
    r = client.get("/api/instruments")
    assert r.status_code == 200
    by_symbol = {i["symbol"]: i for i in r.json()}
    assert by_symbol["CRUDEOIL"]["asset_class"] == "commodity"
    assert by_symbol["GOLD"]["asset_class"] == "commodity"
    assert by_symbol["NIFTY"]["asset_class"] == "index"
    assert by_symbol["RELIANCE"]["asset_class"] == "equity"


def test_strategy_discover_endpoint_for_commodity():
    payload = {
        "symbol": "CRUDEOIL",
        "constraints": {
            "min_probability_of_profit": 0.55,
            "min_yield_pct": 0.005,
            "max_profit_cap": 200000,
            "max_loss_cap": 100000,
            "margin_cap": 500000,
        },
    }
    r = client.post("/api/strategy/discover", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "CRUDEOIL"
    assert len(body["results"]) <= 3


def test_backtest_and_execution_flow():
    chain = client.get("/api/option-chain/NIFTY").json()
    strikes = sorted(row["strike"] for row in chain["rows"])
    spot = chain["spot"]
    short_strike = min((s for s in strikes if s > spot), default=strikes[-1])
    long_strike = max((s for s in strikes if s > short_strike), default=strikes[-1])

    legs = [
        {"option_type": "CE", "strike": short_strike, "side": "short", "quantity_lots": 1},
        {"option_type": "CE", "strike": long_strike, "side": "long", "quantity_lots": 1},
    ]
    expiry = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()

    r = client.post("/api/backtest/run", json={"symbol": "NIFTY", "legs": legs, "expiry": expiry})
    assert r.status_code == 200
    assert len(r.json()["snapshots"]) > 0

    r = client.post("/api/execution/place-basket", json={"symbol": "NIFTY", "legs": legs})
    assert r.status_code == 200
    assert r.json()["all_filled"] is True

    r = client.get("/api/execution/positions")
    assert r.status_code == 200
    assert len(r.json()) >= 2

    r = client.get("/api/execution/margin")
    assert r.status_code == 200
    assert "available_margin" in r.json()


def test_live_margin_requires_kite_auth(monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    reset_kite_client()

    legs = [{"option_type": "CE", "strike": 24800, "side": "short", "quantity_lots": 1}]
    r = client.post("/api/margin/live", json={"symbol": "NIFTY", "legs": legs})
    assert r.status_code == 401
    reset_kite_client()


def test_live_margin_rejects_legs_from_mock_feed(monkeypatch):
    import app.margin.kite_margin as kite_margin_mod

    class FakeKiteMargin:
        def basket_order_margins(self, orders, consider_positions=True, mode=None):
            return {"final": {"total": 1234.0}}

    monkeypatch.setattr(kite_margin_mod, "get_kite_client", lambda: FakeKiteMargin())

    legs = [{"option_type": "CE", "strike": 24800, "side": "short", "quantity_lots": 1}]
    r = client.post("/api/margin/live", json={"symbol": "NIFTY", "legs": legs})
    # NIFTY's chain here is the default mock feed, so legs never get a
    # tradingsymbol -> the margin lookup should reject them with a clear 422.
    assert r.status_code == 422
    assert "tradingsymbol" in r.json()["detail"]


def test_live_margin_happy_path_with_kite_feed(monkeypatch):
    import app.data.kite_feed as kite_feed_mod
    import app.margin.kite_margin as kite_margin_mod
    from tests.test_kite_feed import FakeKite, _build_fake_chain_fixtures

    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kite")
    spot = 24810.0
    nfo_rows, quote_map, expiry = _build_fake_chain_fixtures(spot=spot)
    fake_feed_client = FakeKite(nfo_rows, spot, quote_map)
    monkeypatch.setattr(kite_feed_mod, "get_kite_client", lambda: fake_feed_client)
    kite_feed_mod.clear_instrument_cache()

    class FakeKiteMargin:
        def basket_order_margins(self, orders, consider_positions=True, mode=None):
            return {"final": {"total": 9047.38, "span": 8000.0, "exposure": 1047.38, "option_premium": 0.0}}

    monkeypatch.setattr(kite_margin_mod, "get_kite_client", lambda: FakeKiteMargin())

    chain_resp = client.get("/api/option-chain/NIFTY")
    assert chain_resp.status_code == 200
    strikes = sorted(row["strike"] for row in chain_resp.json()["rows"])

    legs = [{"option_type": "CE", "strike": strikes[-2], "side": "short", "quantity_lots": 1}]
    r = client.post("/api/margin/live", json={"symbol": "NIFTY", "legs": legs})
    assert r.status_code == 200
    body = r.json()
    assert body["total_margin"] == 9047.38
    assert body["span_margin"] == 8000.0
