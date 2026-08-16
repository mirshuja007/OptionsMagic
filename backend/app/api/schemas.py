from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.black_scholes import Greeks


class GreeksOut(BaseModel):
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    @classmethod
    def from_domain(cls, g: Greeks) -> "GreeksOut":
        return cls(delta=g.delta, gamma=g.gamma, theta=g.theta, vega=g.vega, rho=g.rho)


class LegQuoteOut(BaseModel):
    ltp: float
    bid: float
    ask: float
    oi: int
    oi_change: int
    volume: int
    iv: float
    greeks: GreeksOut


class ChainRowOut(BaseModel):
    strike: float
    call: LegQuoteOut
    put: LegQuoteOut


class OptionChainOut(BaseModel):
    symbol: str
    spot: float
    expiry: date
    timestamp: datetime
    time_to_expiry_years: float
    risk_free_rate: float
    rows: list[ChainRowOut]


class InstrumentOut(BaseModel):
    symbol: str
    display_name: str
    is_index: bool
    lot_size: int
    strike_step: float
    exchange: str


# --- Strategy legs (request side) ---


class LegIn(BaseModel):
    option_type: Literal["CE", "PE"]
    strike: float
    side: Literal["long", "short"]
    quantity_lots: int = Field(gt=0)
    entry_price: float | None = None
    iv: float | None = None


class LegOut(BaseModel):
    option_type: str
    strike: float
    side: str
    quantity_lots: int
    entry_price: float
    iv: float


# --- Strategy discovery ---


class StrategyConstraintsIn(BaseModel):
    min_probability_of_profit: float = Field(0.8, ge=0, le=1)
    min_yield_pct: float = Field(0.01, ge=0)
    max_profit_cap: float = Field(gt=0)
    max_loss_cap: float = Field(gt=0)
    margin_cap: float = Field(gt=0)


class DiscoverRequest(BaseModel):
    symbol: str
    expiry: date | None = None
    constraints: StrategyConstraintsIn


class MarginOut(BaseModel):
    span_margin: float
    exposure_margin: float
    total_margin: float
    net_entry_credit: float


class PayoffOut(BaseModel):
    max_profit: float
    max_loss: float
    unlimited_upside_risk: bool
    unlimited_downside_risk: bool


class StrategyResultOut(BaseModel):
    strategy_type: str
    legs: list[LegOut]
    margin: MarginOut
    payoff: PayoffOut
    probability_of_profit: float
    expected_value: float
    sharpe: float
    yield_pct: float


class DiscoverResponse(BaseModel):
    symbol: str
    spot: float
    expiry: date
    results: list[StrategyResultOut]


# --- Backtest ---


class RiskRulesIn(BaseModel):
    take_profit_pct_of_max: float = 0.8
    stop_loss_amount: float = 3000.0
    delta_hedge_threshold: float = 15.0


class BacktestRequest(BaseModel):
    symbol: str
    legs: list[LegIn]
    expiry: date
    session_date: date | None = None
    risk_rules: RiskRulesIn | None = None


class MinuteSnapshotOut(BaseModel):
    timestamp: datetime
    spot: float
    pnl: float
    delta: float
    theta: float
    vega: float
    gamma: float


class RiskActionOut(BaseModel):
    action_type: str
    reason: str
    current_pnl: float
    net_delta: float
    hedge_quantity_shares: float


class BacktestResponse(BaseModel):
    snapshots: list[MinuteSnapshotOut]
    max_drawdown: float
    final_pnl: float
    peak_pnl: float
    triggered_action: RiskActionOut | None
    triggered_at: datetime | None


# --- Execution ---


class ExecutionRequest(BaseModel):
    symbol: str
    legs: list[LegIn]


class OrderFillOut(BaseModel):
    leg: LegOut
    fill_price: float
    status: str
    broker_order_id: str


class BasketOrderResultOut(BaseModel):
    basket_id: str
    fills: list[OrderFillOut]
    all_filled: bool
