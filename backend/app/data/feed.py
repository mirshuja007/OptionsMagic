"""Market-data provider facade.

Every caller in the platform (API routes, the paper broker's margin lookup,
the backtest replay engine) imports ``generate_option_chain`` /
``generate_minute_series`` from *this* module, not from ``mock_feed`` or
``kite_feed`` directly. Which provider actually runs is chosen by the
``MARKET_DATA_PROVIDER`` env var:

  MARKET_DATA_PROVIDER=mock   (default) simulated feed, no credentials needed
  MARKET_DATA_PROVIDER=kite   live Zerodha Kite Connect (see README/.env.example)

The provider is resolved per-call rather than cached at import time, so
tests (and ops, via a live env var change + reload) can switch it without
import-order gymnastics.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from types import ModuleType

VALID_PROVIDERS = ("mock", "kite")


def get_active_provider() -> str:
    provider = os.environ.get("MARKET_DATA_PROVIDER", "mock").strip().lower()
    if provider not in VALID_PROVIDERS:
        raise RuntimeError(f"Unknown MARKET_DATA_PROVIDER={provider!r}; expected one of {VALID_PROVIDERS}")
    return provider


def _module() -> ModuleType:
    if get_active_provider() == "kite":
        from app.data import kite_feed

        return kite_feed
    from app.data import mock_feed

    return mock_feed


def get_risk_free_rate() -> float:
    return _module().RISK_FREE_RATE


def generate_option_chain(
    symbol: str,
    expiry: date | None = None,
    num_strikes: int | None = None,
    as_of: datetime | None = None,
):
    return _module().generate_option_chain(symbol, expiry=expiry, num_strikes=num_strikes, as_of=as_of)


def generate_minute_series(
    symbol: str,
    session_date: date | None = None,
    minutes: int = 375,
):
    return _module().generate_minute_series(symbol, session_date=session_date, minutes=minutes)


def available_expiries(symbol: str) -> list[date]:
    """Expiry dates the UI's expiry selector can offer. Mock: a plausible
    illustrative cadence (see mock_feed.available_expiries). Kite: the real
    dates currently listed on the exchange.
    """
    return _module().available_expiries(symbol)
