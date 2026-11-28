"""Single-leg custom strategy actions: pick one option (strike by ATM,
OTM-by-points, or OTM-by-SD; side; option type; lots) to trade once an
entry condition from ``app.analytics.conditions`` fires. The building
block ``app.backtest.custom_strategy_backtest`` runs against a condition
tree.

Deliberately single-leg for now — multi-leg custom combos are a real
extension (see that module's docstring), not built yet; proving this on
one leg first before generalizing further, same approach as every other
strategy in this app.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.black_scholes import OptionType
from app.strategy.legs import Side

StrikeMethod = Literal["atm", "otm_points", "otm_sd"]


@dataclass(frozen=True)
class StrikeSelection:
    method: StrikeMethod
    value: float = 0.0  # points for "otm_points"; SD multiple for "otm_sd"; unused for "atm"


@dataclass(frozen=True)
class LegAction:
    option_type: OptionType
    side: Side
    strike_selection: StrikeSelection
    lots: int = 1


def select_strike(
    spot: float,
    option_type: OptionType,
    selection: StrikeSelection,
    strike_step: float,
    expected_move_pct: float | None = None,
) -> float:
    """Resolve a ``StrikeSelection`` to an actual listed strike, rounded
    to ``strike_step``. OTM direction is automatic from ``option_type`` —
    a call's OTM strikes sit above spot, a put's sit below — so
    ``StrikeSelection("otm_sd", 1.5)`` means "1.5 SD out of the money"
    regardless of which side is being traded, without the caller having
    to know or encode the sign themselves.
    """
    if selection.method == "atm":
        raw = spot
    elif selection.method == "otm_points":
        sign = 1 if option_type == OptionType.CALL else -1
        raw = spot + sign * selection.value
    elif selection.method == "otm_sd":
        if expected_move_pct is None:
            raise ValueError("otm_sd strike selection needs expected_move_pct, but it's None")
        sign = 1 if option_type == OptionType.CALL else -1
        raw = spot * (1 + sign * selection.value * expected_move_pct)
    else:
        raise ValueError(f"Unknown strike selection method {selection.method!r}")
    return round(raw / strike_step) * strike_step
