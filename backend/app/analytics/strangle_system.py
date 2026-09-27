"""Time-window short-strangle system for NIFTY/SENSEX weekly options — the
"2 Hour Trading Strategy" from a rule-based-trading webinar (Darshan
Rathod / Multyfi), transcribed and confirmed rather than assumed on every
ambiguous point.

Four independent short strangles, three fixed clock windows, every leg
managed independently:

  NIFTY D1111   09:30-10:30   OTM2 strikes   SL 60%/leg   DTE 0,1,2
  NIFTY D5HJ    13:30-14:30   ATM strikes    SL 60%/leg   no DTE gate (*)
  SENSEX A5X    13:30-14:30   ATM strikes    SL 35%/leg   no DTE gate (*)
  SENSEX A6X    14:30-15:28   ATM strikes    SL 35%/leg   DTE 0,1

Confirmed interpretations (not verbatim in the source slides — each was
asked about explicitly rather than assumed):
  * "SL X% per leg" = that leg's own premium rising to entry x (1+X%).
  * "1x re-entry at cost" = a fresh sell of the SAME strike, but only at
    the ORIGINAL entry premium (a limit re-sell, not "sell now at
    whatever the market is").
  * "DTE 0,1,2" for D1111 and "DTE 0,1" for A6X gate whether those two
    strategies trade at all on a given day — unchanged from the source
    slides.

(*) D5HJ and A5X's DTE gate was deliberately removed — not a transcription
of the source slides. The slides state DTE 0,1,2 (NIFTY) / 0,1 (SENSEX)
for every strategy AND separately describe a routine 1:30-2:30 overlap
between D5HJ and A5X every week. Checked against the actual current NSE/
BSE weekly expiry calendar (NIFTY Tuesday, SENSEX Thursday, unchanged
since Sep 2025 — verified live, not assumed): under strict DTE 0-2/0-1
gating, NIFTY's eligible days (Fri/Mon/Tue) and SENSEX's (Wed/Thu) never
share a calendar day, so that overlap could never actually happen — a
genuine inconsistency in the source material, not a bug in this
calculation. Told about this, the call was to keep the stated overlap
real rather than the stated DTE numbers: D5HJ and A5X now run every
trading day regardless of DTE, so their shared 1:30-2:30 window overlaps
daily as the source material describes. D1111 and A6X don't participate
in that overlap and keep their original, unmodified DTE gates.

What this module deliberately is NOT: a backtester (that needs historical
per-strike option premiums, a separate, heavier data build not done here),
or an order-placing system (no Kite order calls anywhere in this module)
— it's the rules plus a live status computation for a paper-tracking
dashboard where a human places every order and confirms every fill.

Known simplification: DTE is counted in trading days skipping weekends
only — there's no NSE holiday calendar wired into this platform yet, so a
DTE reading is off by one in a week containing a market holiday. Surfaced
in the UI wherever DTE is shown, not hidden.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Literal

StrikeRule = Literal["ATM", "OTM2"]
WindowState = Literal["before", "live", "after"]


@dataclass(frozen=True)
class StrategyDef:
    key: str
    label: str
    symbol: str
    window_start: time
    window_end: time
    strike_rule: StrikeRule
    sl_pct: float  # e.g. 0.60 for a 60% stop
    dte_allowed: frozenset[int] | None  # None = no DTE gate, eligible every trading day


STRATEGIES: list[StrategyDef] = [
    StrategyDef("nifty_d1111", "NIFTY D1111", "NIFTY", time(9, 30), time(10, 30), "OTM2", 0.60, frozenset({0, 1, 2})),
    StrategyDef("nifty_d5hj", "NIFTY D5HJ", "NIFTY", time(13, 30), time(14, 30), "ATM", 0.60, None),
    StrategyDef("sensex_a5x", "SENSEX A5X", "SENSEX", time(13, 30), time(14, 30), "ATM", 0.35, None),
    StrategyDef("sensex_a6x", "SENSEX A6X", "SENSEX", time(14, 30), time(15, 28), "ATM", 0.35, frozenset({0, 1})),
]


def is_dte_eligible(strategy: StrategyDef, dte: int) -> bool:
    """Whether ``strategy`` trades at all on a day with this DTE. ``None``
    (D5HJ, A5X — see module docstring) means no DTE gate at all.
    """
    return strategy.dte_allowed is None or dte in strategy.dte_allowed


def trading_days_between(start: date, end: date) -> int:
    """Trading days from ``start`` to ``end`` (exclusive of ``start``,
    inclusive of ``end``), skipping Saturday/Sunday only — see the module
    docstring's "known simplification" for the holiday-calendar gap.
    Zero when ``end == start``; negative when ``end`` precedes ``start``.
    """
    if end == start:
        return 0
    step = 1 if end > start else -1
    count = 0
    cursor = start
    while cursor != end:
        cursor += timedelta(days=step)
        if cursor.weekday() < 5:  # Mon-Fri
            count += step
    return count


def dte_for(today: date, expiry: date) -> int:
    """Days-to-expiry in trading days — 0 on expiry day itself."""
    return trading_days_between(today, expiry)


def window_status(now_t: time, window_start: time, window_end: time) -> WindowState:
    if now_t < window_start:
        return "before"
    if now_t <= window_end:
        return "live"
    return "after"


def resolve_leg_strikes(spot: float, strike_step: float, strike_rule: StrikeRule) -> tuple[float, float]:
    """(call_strike, put_strike) for this strategy's leg pair.

    ATM: both legs at the same nearest-to-spot strike — the source
    material's literal wording ("ATM Call" + "ATM Put" at the same
    level); strictly that makes those legs a straddle rather than a
    strangle, a naming looseness in the source webinar, not something
    this module resolves differently from what was actually specified.

    OTM2: two strike-steps out on each side (call strike above spot, put
    strike below), the standard "OTMn = n strike-steps away" convention.
    """
    atm = round(spot / strike_step) * strike_step
    if strike_rule == "ATM":
        return atm, atm
    return atm + 2 * strike_step, atm - 2 * strike_step


@dataclass(frozen=True)
class LegStatus:
    entry_premium: float
    current_premium: float
    sl_threshold: float
    sl_hit: bool
    pct_move: float


def leg_status(entry_premium: float, current_premium: float, sl_pct: float) -> LegStatus:
    """SL basis: a rise in this leg's OWN premium to entry x (1 + sl_pct)
    — e.g. sold at 10 with a 60% SL triggers if the premium reaches 16.
    """
    threshold = entry_premium * (1 + sl_pct)
    pct_move = (current_premium - entry_premium) / entry_premium if entry_premium else 0.0
    return LegStatus(entry_premium, current_premium, threshold, current_premium >= threshold, pct_move)


def re_entry_ready(entry_premium: float, current_premium: float) -> bool:
    """The one allowed re-entry is a limit re-sell at the ORIGINAL entry
    premium — actionable only once the premium has decayed back down to
    (or below) that level, not an immediate re-sell at the current price.
    """
    return current_premium <= entry_premium
