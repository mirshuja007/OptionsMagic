import datetime

from fastapi.testclient import TestClient

from app.main import app

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
