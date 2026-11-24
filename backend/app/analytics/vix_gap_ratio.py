"""VIX + Gap-triggered put/call ratio strategy — a user-designed system
(not from a webinar), confirmed interactively rather than assumed. NIFTY
weekly options only, same-day (no overnight carry).

Confirmed rules:
  * Entry gate: India VIX > 15 AND the opening-candle gap (vs previous
    close) is between 0% and 0.5% in EITHER direction. Outside that band
    (VIX <= 15, or a gap of 0% or past 0.5%) — no trade. What happens on
    those other days is explicitly still undecided; not invented here.
  * "1 SD" is NIFTY's own realized (not implied) volatility: the standard
    deviation of daily log-returns over the last 30 trading closes,
    scaled by sqrt(trading days to expiry) to get the expected move for a
    position held to that week's expiry — the same sqrt-time scaling any
    volatility figure uses to stretch across a different horizon, just
    starting from realized vol instead of IV.
  * Gap DOWN -> sell 3 lots OTM PE ~2 SD below spot, buy 1 lot OTM PE ~1
    SD below spot (the hedge), sell 1 lot OTM CE ~1.5 SD above spot. Gap
    UP is the exact mirror image (puts <-> calls).
  * Per-leg stop-loss, read literally as "the premium doubles" (a price
    comparison, not a P&L percentage): the naked short — the lone short
    on the side opposite the gap — exits when its own premium reaches 2x
    its entry premium. The covered group — the 3-short/1-long side on the
    gap's own side — exits as a *whole* when the net cost to close it
    (buy back the 3 shorts, sell the 1 long) reaches 2x the net credit
    collected at entry; the hedge's value nets out before this check
    runs, confirmed explicitly rather than assumed.
  * Whole-position profit target: if total intraday profit across every
    leg exceeds 30% of the total premium collected at entry, close
    everything early.
  * Same-day only: whatever's left when neither a stop nor the profit
    target has fired gets flattened at end of day — confirmed as an
    explicit simplification of an earlier multi-day-carry version still
    under discussion, not the final word on this strategy's shape.
  * Entry is evaluated once per day, right after the 9:15-9:20 opening
    candle closes — the same window Opening Volume Breakout uses.
  * Always the nearest weekly NIFTY expiry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.core.black_scholes import OptionType

GapDirection = Literal["up", "down"]

VIX_THRESHOLD = 15.0
GAP_MIN_PCT = 0.0
GAP_MAX_PCT = 0.5
SD_LOOKBACK_DAYS = 30

NAKED_SD = 1.5
COVERED_SHORT_SD = 2.0
COVERED_LONG_SD = 1.0
COVERED_SHORT_LOTS = 3
COVERED_LONG_LOTS = 1
NAKED_LOTS = 1

NAKED_STOP_PRICE_MULTIPLE = 2.0
COVERED_STOP_COST_MULTIPLE = 2.0
PROFIT_TARGET_PCT = 0.30


def vix_filter_passes(vix: float) -> bool:
    return vix > VIX_THRESHOLD


def gap_pct(opening_candle_close: float, prev_close: float) -> float:
    return (opening_candle_close - prev_close) / prev_close * 100.0


def gap_direction(change_pct: float) -> GapDirection | None:
    """``None`` covers both a dead-flat open (~0%) and a gap bigger than
    0.5% — both outside the confirmed trigger band, genuinely "no signal"
    rather than a bug.
    """
    magnitude = abs(change_pct)
    if GAP_MIN_PCT < magnitude <= GAP_MAX_PCT:
        return "down" if change_pct < 0 else "up"
    return None


def entry_direction(vix: float, change_pct: float) -> GapDirection | None:
    if not vix_filter_passes(vix):
        return None
    return gap_direction(change_pct)


def historical_expected_move_pct(
    daily_closes: list[float], trading_days_to_expiry: int, lookback: int = SD_LOOKBACK_DAYS
) -> float:
    """1-SD expected move as a fraction of spot (e.g. 0.035 = 3.5%) for a
    position held ``trading_days_to_expiry`` trading days, from NIFTY's
    own realized volatility over its last ``lookback`` daily closes.
    ``daily_closes`` must already be in ascending date order; only the
    most recent ``lookback`` + 1 closes are used (need one extra point to
    get ``lookback`` daily returns from ``lookback`` + 1 prices).
    """
    closes = daily_closes[-(lookback + 1):]
    if len(closes) < 3:
        return 0.0
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    daily_sd = math.sqrt(variance)
    return daily_sd * math.sqrt(max(trading_days_to_expiry, 0))


@dataclass(frozen=True)
class StrikeLevels:
    naked_strike: float  # the lone short, on the side OPPOSITE the gap
    covered_short_strike: float  # the 3-lot short, on the SAME side as the gap
    covered_long_strike: float  # the 1-lot hedge, same side as the gap, closer to spot


def strike_levels(spot: float, expected_move_pct: float, strike_step: float, direction: GapDirection) -> StrikeLevels:
    """Round every SD-based target level to the nearest listed strike. On
    a gap DOWN the covered put group sits below spot (the gap's own
    direction) and the naked short is a call above spot (the opposite
    side); a gap UP mirrors this exactly.
    """

    def level(sd_multiple: float, sign: int) -> float:
        raw = spot * (1 + sign * sd_multiple * expected_move_pct)
        return round(raw / strike_step) * strike_step

    if direction == "down":
        return StrikeLevels(
            naked_strike=level(NAKED_SD, +1),
            covered_short_strike=level(COVERED_SHORT_SD, -1),
            covered_long_strike=level(COVERED_LONG_SD, -1),
        )
    return StrikeLevels(
        naked_strike=level(NAKED_SD, -1),
        covered_short_strike=level(COVERED_SHORT_SD, +1),
        covered_long_strike=level(COVERED_LONG_SD, +1),
    )


def naked_option_type(direction: GapDirection) -> OptionType:
    """The naked leg sits opposite the gap: a gap down sells the naked CE, a gap up sells the naked PE."""
    return OptionType.CALL if direction == "down" else OptionType.PUT


def covered_option_type(direction: GapDirection) -> OptionType:
    """The covered group sits on the gap's own side: PUTs on a gap down, CALLs on a gap up."""
    return OptionType.PUT if direction == "down" else OptionType.CALL


def naked_leg_stopped(entry_premium: float, current_premium: float) -> bool:
    """Literal "premium doubles" reading: a price comparison, not a loss
    percentage (doubling from P to 2P is a 100%-of-credit loss, not 200%
    — the distinction matters, so this stays in price space to avoid any
    ambiguity about which convention is meant).
    """
    return current_premium >= NAKED_STOP_PRICE_MULTIPLE * entry_premium


def covered_group_entry_net_credit(short_entry_premium: float, long_entry_premium: float) -> float:
    return COVERED_SHORT_LOTS * short_entry_premium - COVERED_LONG_LOTS * long_entry_premium


def covered_group_current_net_cost(short_current_premium: float, long_current_premium: float) -> float:
    """Cost to close the group right now: buy back the 3 shorts, sell the 1 long."""
    return COVERED_SHORT_LOTS * short_current_premium - COVERED_LONG_LOTS * long_current_premium


def covered_group_stopped(entry_net_credit: float, short_current_premium: float, long_current_premium: float) -> bool:
    """The hedge's current value nets out of this check (confirmed
    explicitly) — a bigger long-put value when the group is deep ITM
    offsets the short puts' own loss before comparing to the stop.
    """
    if entry_net_credit <= 0:
        return False  # no credit was ever received; "doubling" a non-positive credit is undefined
    current_net_cost = covered_group_current_net_cost(short_current_premium, long_current_premium)
    return current_net_cost >= COVERED_STOP_COST_MULTIPLE * entry_net_credit


def profit_target_hit(total_entry_credit: float, total_current_cost: float) -> bool:
    """Profit on a net-credit combo = entry credit minus current cost to
    close (value decaying toward zero is the win). Hits the 30% target
    when that profit reaches 30% of the entry credit.
    """
    if total_entry_credit <= 0:
        return False
    profit = total_entry_credit - total_current_cost
    return profit >= PROFIT_TARGET_PCT * total_entry_credit
