"""Paper-trading broker: fills every basket order at the chain's current mid
price with no slippage, and tracks positions/available margin in memory.
Used as the default adapter so strategies can be "executed" end-to-end
without a live broker connection.
"""
from __future__ import annotations

import uuid

from app.broker.base import BasketOrderResult, BrokerAdapter, OrderFill, OrderStatus
from app.data.instruments import get_instrument
from app.data.mock_feed import generate_option_chain
from app.margin.span import estimate_margin
from app.strategy.legs import Leg


class PaperBroker(BrokerAdapter):
    def __init__(self, starting_capital: float = 5_000_000.0):
        self._capital = starting_capital
        self._blocked_margin = 0.0
        self._positions: list[Leg] = []

    def get_available_margin(self) -> float:
        return self._capital - self._blocked_margin

    def estimate_order_margin(self, legs: list[Leg], symbol: str) -> float:
        instrument = get_instrument(symbol)
        chain = generate_option_chain(symbol)
        return estimate_margin(legs, instrument, chain.spot, chain.time_to_expiry_years, chain.risk_free_rate).total_margin

    def place_basket_order(self, legs: list[Leg], symbol: str) -> BasketOrderResult:
        fills = [
            OrderFill(
                leg=leg,
                fill_price=leg.entry_price,
                status=OrderStatus.FILLED,
                broker_order_id=str(uuid.uuid4()),
            )
            for leg in legs
        ]
        self._positions.extend(legs)
        self._blocked_margin += self.estimate_order_margin(legs, symbol)
        return BasketOrderResult(basket_id=str(uuid.uuid4()), fills=fills, all_filled=True)

    def get_positions(self) -> list[Leg]:
        return list(self._positions)

    def square_off(self, legs: list[Leg], symbol: str) -> BasketOrderResult:
        remaining = list(self._positions)
        for leg in legs:
            if leg in remaining:
                remaining.remove(leg)
        self._positions = remaining
        self._blocked_margin = max(self._blocked_margin - self.estimate_order_margin(legs, symbol), 0.0)
        fills = [
            OrderFill(leg=leg, fill_price=leg.entry_price, status=OrderStatus.FILLED, broker_order_id=str(uuid.uuid4()))
            for leg in legs
        ]
        return BasketOrderResult(basket_id=str(uuid.uuid4()), fills=fills, all_filled=True)
