"""General-purpose condition toolkit for building custom algo strategies:
composable AND/OR/NOT predicates evaluated against one ``MarketSnapshot``,
usable for *either* entry or exit logic — a condition doesn't know or care
which role it's playing, so the same ``PremiumChangeCondition`` that
screens the whole chain for a strike that already moved +100% today can
equally be the exit rule on a position you're already holding; only the
reference point you choose (entry price vs. today's open vs. yesterday's
close) differs between the two uses.

This is deliberately a toolkit, not a strategy: it has no opinion on what
symbol, what instrument, or what to actually trade when a condition set
evaluates true — that's for whatever assembles these into a concrete
strategy (see ``app.backtest`` for strategy-specific engines) to decide.

Every leaf condition raises ``ValueError`` if the ``MarketSnapshot`` field
it needs is ``None``, rather than silently evaluating to ``False`` — a
condition set with a wiring mistake (e.g. forgetting to populate
``option_premium`` before evaluating a ``PremiumChangeCondition``) should
fail loudly, not quietly screen out every single day and look like "no
signal" when it's actually "not computed."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

GapDirection = Literal["up", "down", "flat"]
Operator = Literal[">", ">=", "<", "<=", "==", "!="]
Greek = Literal["delta", "gamma", "theta", "vega"]
PremiumReference = Literal["entry", "day_open", "prev_close"]
IvRegime = Literal["rich", "cheap", "neutral"]


def _compare(value: float, operator: Operator, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator == "==":
        return value == threshold
    if operator == "!=":
        return value != threshold
    raise ValueError(f"Unknown operator {operator!r}")


@dataclass(frozen=True)
class MarketSnapshot:
    """Everything a condition might read, at one point in time (an entry
    moment, or any later minute being checked for an exit). Every field
    but ``spot``/``prev_close`` is optional-by-absence — a condition that
    doesn't need a field never reads it, but one that does raises a clear
    error rather than silently treating a missing value as "doesn't match."
    """
    spot: float
    prev_close: float
    vix: float | None = None
    atm_call_premium: float | None = None
    atm_put_premium: float | None = None
    option_premium: float | None = None  # the specific leg this snapshot is "about", if any
    option_entry_premium: float | None = None
    option_day_open_premium: float | None = None
    option_prev_close_premium: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None
    realized_vol: float | None = None


@runtime_checkable
class Condition(Protocol):
    def evaluate(self, snap: MarketSnapshot) -> bool: ...


def _require(value: float | None, field_name: str, condition_name: str) -> float:
    if value is None:
        raise ValueError(f"{condition_name} needs MarketSnapshot.{field_name}, but it's None")
    return value


@dataclass(frozen=True)
class And:
    conditions: tuple[Condition, ...]

    def evaluate(self, snap: MarketSnapshot) -> bool:
        return all(c.evaluate(snap) for c in self.conditions)


@dataclass(frozen=True)
class Or:
    conditions: tuple[Condition, ...]

    def evaluate(self, snap: MarketSnapshot) -> bool:
        return any(c.evaluate(snap) for c in self.conditions)


@dataclass(frozen=True)
class Not:
    condition: Condition

    def evaluate(self, snap: MarketSnapshot) -> bool:
        return not self.condition.evaluate(snap)


@dataclass(frozen=True)
class AlwaysTrue:
    """Trivial condition, useful as a default entry screener (trade every
    day, no entry filter at all) or as a placeholder while a UI is still
    being configured.
    """
    def evaluate(self, snap: MarketSnapshot) -> bool:
        return True


@dataclass(frozen=True)
class AlwaysFalse:
    """Trivial condition, useful as a default exit: never fires, so a
    position rides to whatever end-of-day/flat-by cutoff the engine
    enforces instead of ever closing on a condition.
    """
    def evaluate(self, snap: MarketSnapshot) -> bool:
        return False


@dataclass(frozen=True)
class GapCondition:
    """The underlying's opening move vs. the previous close. "flat" uses
    only ``max_pct`` as a symmetric band around zero; "up"/"down" require
    the magnitude to fall strictly between ``min_pct`` and ``max_pct``
    (exclusive/inclusive, matching every other gap-band condition already
    confirmed in this app) so a dead-flat 0.0% open never double-counts
    as both "flat" and "up".
    """
    direction: GapDirection
    min_pct: float = 0.0
    max_pct: float = float("inf")

    def evaluate(self, snap: MarketSnapshot) -> bool:
        spot = _require(snap.spot, "spot", "GapCondition")
        prev_close = _require(snap.prev_close, "prev_close", "GapCondition")
        change_pct = (spot - prev_close) / prev_close * 100.0
        magnitude = abs(change_pct)
        if self.direction == "flat":
            return magnitude <= self.max_pct
        if self.direction == "up":
            return change_pct > 0 and self.min_pct < magnitude <= self.max_pct
        return change_pct < 0 and self.min_pct < magnitude <= self.max_pct


@dataclass(frozen=True)
class VixCondition:
    operator: Operator
    threshold: float

    def evaluate(self, snap: MarketSnapshot) -> bool:
        vix = _require(snap.vix, "vix", "VixCondition")
        return _compare(vix, self.operator, self.threshold)


@dataclass(frozen=True)
class StraddlePctCondition:
    """ATM combined (call + put) straddle premium as a percent of spot —
    a cheap, zero-history "how much move is priced in" read.
    """
    operator: Operator
    threshold_pct: float

    def evaluate(self, snap: MarketSnapshot) -> bool:
        call = _require(snap.atm_call_premium, "atm_call_premium", "StraddlePctCondition")
        put = _require(snap.atm_put_premium, "atm_put_premium", "StraddlePctCondition")
        spot = _require(snap.spot, "spot", "StraddlePctCondition")
        straddle_pct = (call + put) / spot * 100.0
        return _compare(straddle_pct, self.operator, self.threshold_pct)


@dataclass(frozen=True)
class PremiumChangeCondition:
    """% change in one option's premium vs. a chosen reference point.
    ``reference="entry"`` makes this an exit rule on a position you
    already hold (percent move from your own fill); ``"day_open"`` or
    ``"prev_close"`` makes it an entry screener across the live chain
    (percent move since this morning's open, or since yesterday's close)
    — the same condition type serves both roles, just pointed at a
    different reference.
    """
    reference: PremiumReference
    operator: Operator
    pct: float  # e.g. 100 for "+100%", -50 for "-50%"

    def evaluate(self, snap: MarketSnapshot) -> bool:
        current = _require(snap.option_premium, "option_premium", "PremiumChangeCondition")
        field_name = f"option_{self.reference}_premium"
        reference_value = getattr(snap, field_name)
        if reference_value is None:
            raise ValueError(f"PremiumChangeCondition needs MarketSnapshot.{field_name}, but it's None")
        if reference_value <= 0:
            return False  # a zero/negative reference makes "% change" undefined, not a false positive
        change_pct = (current - reference_value) / reference_value * 100.0
        return _compare(change_pct, self.operator, self.pct)


@dataclass(frozen=True)
class GreekCondition:
    greek: Greek
    operator: Operator
    threshold: float

    def evaluate(self, snap: MarketSnapshot) -> bool:
        value = _require(getattr(snap, self.greek), self.greek, "GreekCondition")
        return _compare(value, self.operator, self.threshold)


@dataclass(frozen=True)
class IvRegimeCondition:
    """Reuses ``app.analytics.volatility.classify_iv_regime`` so this and
    Research Mode's IV-vs-HV read are always computed the same way.
    """
    regime: IvRegime
    neutral_band: float = 0.02

    def evaluate(self, snap: MarketSnapshot) -> bool:
        from app.analytics.volatility import classify_iv_regime

        iv = _require(snap.iv, "iv", "IvRegimeCondition")
        realized_vol = _require(snap.realized_vol, "realized_vol", "IvRegimeCondition")
        return classify_iv_regime(iv, realized_vol, self.neutral_band) == self.regime
