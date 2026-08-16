"""Abstract broker interface.

Every execution surface in the platform (automated adjustment triggers, the
strategy command panel's "Execute" button) talks to this interface, not to a
specific broker SDK. To go live, implement this ABC against Kite Connect
(Zerodha), SmartAPI (AngelOne), or the Fyers API — real order placement
needs that broker's API key/secret and a signed session token, none of which
belong in this codebase. ``PaperBroker`` (paper.py) is the default
implementation so the rest of the system is fully runnable without them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from app.strategy.legs import Leg


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class OrderFill:
    leg: Leg
    fill_price: float
    status: OrderStatus
    broker_order_id: str


@dataclass(frozen=True)
class BasketOrderResult:
    basket_id: str
    fills: list[OrderFill]
    all_filled: bool


class BrokerAdapter(ABC):
    """Adapters must fill every leg of a basket as close to atomically as the
    broker's API allows, to avoid legging risk on multi-leg strategies.
    """

    @abstractmethod
    def get_available_margin(self) -> float:
        ...

    @abstractmethod
    def estimate_order_margin(self, legs: list[Leg], symbol: str) -> float:
        ...

    @abstractmethod
    def place_basket_order(self, legs: list[Leg], symbol: str) -> BasketOrderResult:
        ...

    @abstractmethod
    def get_positions(self) -> list[Leg]:
        ...

    @abstractmethod
    def square_off(self, legs: list[Leg], symbol: str) -> BasketOrderResult:
        ...
