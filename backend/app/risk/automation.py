"""Automated risk-management trigger evaluation: take-profit, stop-loss, and
delta-hedging rules. Pure decision logic — wiring an ``Action`` to an actual
broker call (square-off / hedge order) happens in the caller via a
``BrokerAdapter``, keeping this module broker-agnostic and easy to unit test.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.data.instruments import Instrument
from app.strategy.legs import Leg, PayoffExtrema, mark_to_market, portfolio_greeks


class ActionType(str, Enum):
    NONE = "none"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    DELTA_HEDGE = "delta_hedge"


@dataclass(frozen=True)
class RiskRules:
    take_profit_pct_of_max: float = 0.8
    stop_loss_amount: float = 3000.0  # positive rupee cap on loss
    delta_hedge_threshold: float = 15.0  # net portfolio delta, in underlying-share units


@dataclass(frozen=True)
class RiskAction:
    action_type: ActionType
    reason: str
    current_pnl: float
    net_delta: float
    hedge_quantity_shares: float = 0.0  # underlying shares to trade to flatten delta, signed


def evaluate(
    legs: list[Leg],
    extrema: PayoffExtrema,
    instrument: Instrument,
    spot: float,
    t: float,
    r: float,
    rules: RiskRules,
) -> RiskAction:
    current_pnl = mark_to_market(legs, spot, t, r, instrument.lot_size)
    net_delta = portfolio_greeks(legs, spot, t, r, instrument.lot_size).delta

    if current_pnl <= -rules.stop_loss_amount:
        return RiskAction(
            ActionType.STOP_LOSS,
            f"Loss {current_pnl:.2f} breached stop-loss cap of {rules.stop_loss_amount:.2f}",
            current_pnl,
            net_delta,
        )

    take_profit_level = rules.take_profit_pct_of_max * extrema.max_profit
    if extrema.max_profit > 0 and current_pnl >= take_profit_level:
        return RiskAction(
            ActionType.TAKE_PROFIT,
            f"P&L {current_pnl:.2f} reached {rules.take_profit_pct_of_max:.0%} of max profit "
            f"({take_profit_level:.2f})",
            current_pnl,
            net_delta,
        )

    if abs(net_delta) > rules.delta_hedge_threshold:
        # Flatten delta by trading the opposite sign in the underlying.
        hedge_qty = -net_delta
        return RiskAction(
            ActionType.DELTA_HEDGE,
            f"Net delta {net_delta:.2f} exceeds threshold {rules.delta_hedge_threshold:.2f}",
            current_pnl,
            net_delta,
            hedge_quantity_shares=round(hedge_qty, 2),
        )

    return RiskAction(ActionType.NONE, "Within all risk thresholds", current_pnl, net_delta)
