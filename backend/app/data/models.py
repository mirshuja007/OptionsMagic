"""Feed-agnostic data shapes.

Every market-data provider (the simulated feed in ``mock_feed.py``, the real
one in ``kite_feed.py``, and any future adapter) returns these same
dataclasses. Nothing downstream — analytics, the strategy generator, the
solver, margin, backtest — imports a provider module directly for its types;
they all import from here (usually via ``app.data.mock_feed``'s re-export,
kept for backward compatibility) so swapping the active provider in
``app/data/feed.py`` changes zero call sites.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.core.black_scholes import Greeks, OptionType


@dataclass(frozen=True)
class LegQuote:
    option_type: OptionType
    ltp: float
    bid: float
    ask: float
    oi: int
    oi_change: int
    volume: int
    iv: float
    greeks: Greeks
    tradingsymbol: str = ""  # only populated by kite_feed.py; empty for the simulated feed


@dataclass(frozen=True)
class ChainRow:
    strike: float
    call: LegQuote
    put: LegQuote


@dataclass(frozen=True)
class OptionChain:
    symbol: str
    spot: float
    expiry: date
    timestamp: datetime
    time_to_expiry_years: float
    risk_free_rate: float
    prev_close: float
    rows: list[ChainRow] = field(default_factory=list)
