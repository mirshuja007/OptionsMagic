"""Minute-by-minute historical replay of an open strategy.

Replays a fixed set of legs (struck and IV-priced at entry) across a
simulated session's minute-by-minute underlying path, tracking combined
premium, portfolio Greeks, and drawdown — and, if risk rules are supplied,
the first minute an automated trigger (take-profit/stop-loss/delta-hedge)
would have fired.

Limitation: intraday IV is held fixed at its entry value for each leg. A
production build would replay a historical *IV surface* (from a tick/vendor
feed) rather than assuming static vol; that's independent of which
underlying-price feed (simulated or live Kite history) is active.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.data.feed import generate_minute_series, get_risk_free_rate
from app.data.instruments import Instrument
from app.risk.automation import ActionType, RiskAction, RiskRules, evaluate
from app.strategy.legs import Leg, PayoffExtrema, mark_to_market, payoff_extrema, portfolio_greeks


@dataclass(frozen=True)
class MinuteSnapshot:
    timestamp: datetime
    spot: float
    pnl: float
    delta: float
    theta: float
    vega: float
    gamma: float


@dataclass(frozen=True)
class ReplayResult:
    snapshots: list[MinuteSnapshot]
    max_drawdown: float
    final_pnl: float
    peak_pnl: float
    triggered_action: RiskAction | None
    triggered_at: datetime | None


def replay_strategy(
    legs: list[Leg],
    instrument: Instrument,
    expiry_dt: datetime,
    session_date: date | None = None,
    risk_rules: RiskRules | None = None,
) -> ReplayResult:
    minute_series = generate_minute_series(instrument.symbol, session_date=session_date)
    extrema: PayoffExtrema = payoff_extrema(legs, instrument.lot_size)
    risk_free_rate = get_risk_free_rate()

    snapshots: list[MinuteSnapshot] = []
    peak_pnl = float("-inf")
    max_drawdown = 0.0
    triggered_action: RiskAction | None = None
    triggered_at: datetime | None = None

    for timestamp, spot in minute_series:
        t = max((expiry_dt - timestamp).total_seconds() / (365.0 * 24 * 3600), 1e-6)
        pnl = mark_to_market(legs, spot, t, risk_free_rate, instrument.lot_size)
        g = portfolio_greeks(legs, spot, t, risk_free_rate, instrument.lot_size)

        peak_pnl = max(peak_pnl, pnl)
        max_drawdown = min(max_drawdown, pnl - peak_pnl)

        snapshots.append(
            MinuteSnapshot(timestamp=timestamp, spot=spot, pnl=round(pnl, 2), delta=g.delta, theta=g.theta, vega=g.vega, gamma=g.gamma)
        )

        if risk_rules is not None and triggered_action is None:
            action = evaluate(legs, extrema, instrument, spot, t, risk_free_rate, risk_rules)
            if action.action_type != ActionType.NONE:
                triggered_action = action
                triggered_at = timestamp

    final_pnl = snapshots[-1].pnl if snapshots else 0.0
    return ReplayResult(
        snapshots=snapshots,
        max_drawdown=round(max_drawdown, 2),
        final_pnl=final_pnl,
        peak_pnl=round(peak_pnl, 2) if snapshots else 0.0,
        triggered_action=triggered_action,
        triggered_at=triggered_at,
    )
