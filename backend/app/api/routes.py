from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, HTTPException

from app.analytics import max_pain as max_pain_mod
from app.analytics import oi as oi_mod
from app.analytics import pcr as pcr_mod
from app.analytics import straddle as straddle_mod
from app.analytics import volatility as volatility_mod
from app.analytics import vwap as vwap_mod
from app.api.converters import leg_in_to_domain, leg_to_out
from app.api.schemas import (
    BacktestRequest,
    BacktestResponse,
    BasketOrderResultOut,
    ChainRowOut,
    DiscoverRequest,
    DiscoverResponse,
    ExecutionRequest,
    ExpiriesResponse,
    GreeksOut,
    InstrumentOut,
    IntradayPointOut,
    IntradayResponse,
    LegQuoteOut,
    LiveMarginRequest,
    LiveMarginResponse,
    MarginOut,
    MinuteSnapshotOut,
    OptionChainOut,
    OrderFillOut,
    PayoffOut,
    RiskActionOut,
    StrategyConstraintsIn,
    StrategyResultOut,
)
from app.backtest.replay import replay_strategy
from app.broker.paper import PaperBroker
from app.data.feed import available_expiries, generate_minute_series, generate_option_chain, get_active_provider
from app.data.instruments import ALL_INSTRUMENTS, get_instrument
from app.data.kite_client import KiteAuthError
from app.data.kite_feed import KiteFeedError
from app.margin.kite_margin import KiteMarginError, fetch_basket_margin
from app.risk.automation import RiskRules
from app.strategy.solver import StrategyConstraints, discover_strategies

router = APIRouter()
_broker = PaperBroker()


def _chain_row_out(row) -> ChainRowOut:
    return ChainRowOut(
        strike=row.strike,
        call=LegQuoteOut(
            ltp=row.call.ltp, bid=row.call.bid, ask=row.call.ask, oi=row.call.oi,
            oi_change=row.call.oi_change, volume=row.call.volume, iv=row.call.iv,
            greeks=GreeksOut.from_domain(row.call.greeks),
        ),
        put=LegQuoteOut(
            ltp=row.put.ltp, bid=row.put.bid, ask=row.put.ask, oi=row.put.oi,
            oi_change=row.put.oi_change, volume=row.put.volume, iv=row.put.iv,
            greeks=GreeksOut.from_domain(row.put.greeks),
        ),
    )


@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments():
    return [
        InstrumentOut(
            symbol=i.symbol, display_name=i.display_name, is_index=i.is_index, asset_class=i.asset_class,
            lot_size=i.lot_size, strike_step=i.strike_step, exchange=i.exchange,
        )
        for i in ALL_INSTRUMENTS.values()
    ]


@router.get("/data-provider")
def data_provider():
    """Which market-data provider is currently active — useful for confirming
    live (Kite) vs. simulated data before trusting anything the UI shows.
    """
    return {"provider": get_active_provider()}


def _get_chain(symbol: str, expiry: date | None = None):
    try:
        return generate_option_chain(symbol.upper(), expiry=expiry)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KiteAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except KiteFeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/expiries/{symbol}", response_model=ExpiriesResponse)
def expiries(symbol: str):
    """Expiry dates the frontend's expiry selector offers for this symbol —
    real listed dates when MARKET_DATA_PROVIDER=kite, an illustrative
    cadence-based list otherwise.
    """
    symbol = symbol.upper()
    try:
        dates = available_expiries(symbol)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KiteAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except KiteFeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ExpiriesResponse(symbol=symbol, expiries=dates)


@router.get("/option-chain/{symbol}", response_model=OptionChainOut)
def option_chain(symbol: str, expiry: date | None = None, num_strikes: int = 21):
    chain = _get_chain(symbol, expiry)
    return OptionChainOut(
        symbol=chain.symbol, spot=chain.spot, expiry=chain.expiry, timestamp=chain.timestamp,
        time_to_expiry_years=chain.time_to_expiry_years, risk_free_rate=chain.risk_free_rate,
        prev_close=chain.prev_close,
        rows=[_chain_row_out(r) for r in chain.rows],
    )


@router.get("/analytics/intraday/{symbol}", response_model=IntradayResponse)
def intraday(symbol: str):
    """Today's minute-by-minute underlying price — the same series the
    backtest replay engine runs against. Replaces a third-party charting
    widget with data this platform actually owns and can guarantee renders.
    """
    symbol = symbol.upper()
    try:
        series = generate_minute_series(symbol)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KiteAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except KiteFeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    vwaps = vwap_mod.vwap_series([(p, v) for _, p, v in series])
    return IntradayResponse(
        symbol=symbol,
        points=[
            IntradayPointOut(timestamp=t, spot=p, volume=v, vwap=round(vw, 2))
            for (t, p, v), vw in zip(series, vwaps)
        ],
    )


@router.get("/analytics/max-pain/{symbol}")
def max_pain(symbol: str, expiry: date | None = None):
    chain = _get_chain(symbol, expiry)
    result = max_pain_mod.compute_max_pain(chain)
    return {"symbol": chain.symbol, "spot": chain.spot, "max_pain_strike": result.max_pain_strike,
            "payout_by_strike": result.payout_by_strike}


@router.get("/analytics/pcr/{symbol}")
def pcr(symbol: str, expiry: date | None = None):
    chain = _get_chain(symbol, expiry)
    result = pcr_mod.compute_pcr(chain)
    return {"symbol": chain.symbol, **result.__dict__}


@router.get("/analytics/oi/{symbol}")
def open_interest(symbol: str, expiry: date | None = None, spot_change_pct: float = 0.0):
    chain = _get_chain(symbol, expiry)
    strikes = oi_mod.multistrike_oi(chain, spot_change_pct)
    smart = oi_mod.smart_oi_score(chain)
    gex = oi_mod.gamma_exposure(chain)
    return {
        "symbol": chain.symbol,
        "by_strike": [s.__dict__ for s in strikes],
        "smart_oi": smart,
        # kite_feed's oi_change is always 0 (Kite's quote API has no
        # previous-session diff — see kite_feed.py's module docstring), so
        # smart_oi_score is structurally always {"score": 0.0, "bias":
        # "neutral"} under live data. This flag lets the frontend say so
        # instead of presenting that as a real reading.
        "oi_change_available": get_active_provider() != "kite",
        "gamma_exposure": gex,
    }


@router.get("/analytics/volatility/{symbol}")
def volatility(symbol: str, expiry: date | None = None):
    chain = _get_chain(symbol, expiry)
    grid = volatility_mod.iv_grid(chain)
    return {
        "symbol": chain.symbol,
        "atm_iv": volatility_mod.atm_iv(chain),
        "skew": volatility_mod.volatility_skew(chain),
        "grid": [g.__dict__ for g in grid],
    }


@router.get("/analytics/straddle/{symbol}")
def straddle(symbol: str, expiry: date | None = None):
    chain = _get_chain(symbol, expiry)
    return {
        "symbol": chain.symbol,
        "atm_straddle": straddle_mod.atm_straddle(chain).__dict__,
        "multistrike": [p.__dict__ for p in straddle_mod.multistrike_straddle(chain)],
        "decay_curve": straddle_mod.premium_decay_curve(chain),
    }


@router.post("/strategy/discover", response_model=DiscoverResponse)
def discover(req: DiscoverRequest):
    chain = _get_chain(req.symbol, req.expiry)
    instrument = get_instrument(req.symbol)
    c = req.constraints
    constraints = StrategyConstraints(
        min_pop=c.min_probability_of_profit, min_yield_pct=c.min_yield_pct,
        max_profit_cap=c.max_profit_cap, max_loss_cap=c.max_loss_cap, margin_cap=c.margin_cap,
    )
    results = discover_strategies(chain, instrument, constraints)
    return DiscoverResponse(
        symbol=chain.symbol, spot=chain.spot, expiry=chain.expiry,
        results=[
            StrategyResultOut(
                strategy_type=r.strategy_type,
                legs=[leg_to_out(leg) for leg in r.legs],
                margin=MarginOut(**r.margin.__dict__),
                payoff=PayoffOut(**r.payoff.__dict__),
                probability_of_profit=r.probability_of_profit,
                expected_value=r.expected_value,
                sharpe=r.sharpe,
                yield_pct=r.yield_pct,
            )
            for r in results
        ],
    )


@router.post("/backtest/run", response_model=BacktestResponse)
def backtest(req: BacktestRequest):
    chain = _get_chain(req.symbol)
    instrument = get_instrument(req.symbol)
    legs = [leg_in_to_domain(leg_in, chain) for leg_in in req.legs]
    expiry_dt = datetime.combine(req.expiry, instrument.session_end)
    rules = RiskRules(**req.risk_rules.model_dump()) if req.risk_rules else None

    result = replay_strategy(legs, instrument, expiry_dt, session_date=req.session_date, risk_rules=rules)
    return BacktestResponse(
        snapshots=[MinuteSnapshotOut(**s.__dict__) for s in result.snapshots],
        max_drawdown=result.max_drawdown, final_pnl=result.final_pnl, peak_pnl=result.peak_pnl,
        triggered_action=RiskActionOut(**result.triggered_action.__dict__) if result.triggered_action else None,
        triggered_at=result.triggered_at,
    )


@router.post("/execution/place-basket", response_model=BasketOrderResultOut)
def place_basket(req: ExecutionRequest):
    chain = _get_chain(req.symbol)
    legs = [leg_in_to_domain(leg_in, chain) for leg_in in req.legs]
    result = _broker.place_basket_order(legs, req.symbol.upper())
    return BasketOrderResultOut(
        basket_id=result.basket_id,
        fills=[OrderFillOut(leg=leg_to_out(f.leg), fill_price=f.fill_price, status=f.status.value,
                             broker_order_id=f.broker_order_id) for f in result.fills],
        all_filled=result.all_filled,
    )


@router.post("/execution/square-off", response_model=BasketOrderResultOut)
def square_off(req: ExecutionRequest):
    chain = _get_chain(req.symbol)
    legs = [leg_in_to_domain(leg_in, chain) for leg_in in req.legs]
    result = _broker.square_off(legs, req.symbol.upper())
    return BasketOrderResultOut(
        basket_id=result.basket_id,
        fills=[OrderFillOut(leg=leg_to_out(f.leg), fill_price=f.fill_price, status=f.status.value,
                             broker_order_id=f.broker_order_id) for f in result.fills],
        all_filled=result.all_filled,
    )


@router.get("/execution/positions")
def positions():
    return [leg_to_out(leg).model_dump() for leg in _broker.get_positions()]


@router.get("/execution/margin")
def margin_summary():
    return {"available_margin": _broker.get_available_margin()}


@router.post("/margin/live", response_model=LiveMarginResponse)
def live_margin(req: LiveMarginRequest):
    """Real broker margin for a leg set, via Kite's basket_order_margins()
    (read-only — no order is placed). Only works when MARKET_DATA_PROVIDER=kite
    and every leg carries a Kite tradingsymbol (i.e. the chain it was priced
    off of came from the live feed, not the mock one).
    """
    chain = _get_chain(req.symbol)
    instrument = get_instrument(req.symbol)
    legs = [leg_in_to_domain(leg_in, chain) for leg_in in req.legs]
    try:
        result = fetch_basket_margin(legs, instrument, consider_positions=req.consider_positions)
    except KiteMarginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KiteAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return LiveMarginResponse(
        total_margin=result.total_margin,
        span_margin=result.span_margin,
        exposure_margin=result.exposure_margin,
        option_premium=result.option_premium,
    )
