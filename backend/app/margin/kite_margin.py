"""Real margin lookup via Kite Connect's basket margin API.

Unlike ``app/margin/span.py``'s SPAN-methodology approximation, this asks
the broker directly for the actual number — via ``basket_order_margins()``,
which (unlike the plain ``order_margins()``) accounts for margin benefit
across hedged multi-leg combinations (credit spreads, iron condors) the same
way Kite computes it for a real order. It is read-only: passing orders to
this endpoint only prices them, it never places anything.

Deliberately NOT wired into the solver's bulk sweep — that screens a few
hundred candidates per request, and Kite's margin API has real rate limits.
Use this for a targeted "verify this one result" lookup instead (see
``POST /api/margin/live``).

Caveat: the exact response shape below (``final``/``initial`` sections with
a ``total`` field) is Kite Connect's documented basket-margin response
structure at the time this was written, but it has not been exercised
against a live account from this environment — if Kite changes the shape,
this raises ``KiteMarginError`` with the raw response rather than silently
returning a wrong number. Sanity-check the first few real calls.
"""
from __future__ import annotations

from dataclasses import dataclass

from kiteconnect.exceptions import KiteException

from app.data.instruments import Instrument
from app.data.kite_client import get_kite_client
from app.strategy.legs import Leg, Side


class KiteMarginError(RuntimeError):
    """Raised when real margin can't be fetched: auth, network, an
    unexpected response shape, or a leg missing its Kite tradingsymbol.
    """


@dataclass(frozen=True)
class KiteMarginResult:
    total_margin: float
    span_margin: float | None
    exposure_margin: float | None
    option_premium: float | None
    raw: dict


def _order_param(leg: Leg, instrument: Instrument) -> dict:
    if not leg.tradingsymbol:
        raise KiteMarginError(
            "Leg has no Kite tradingsymbol — real margin lookup only works for legs sourced "
            "from the live feed (MARKET_DATA_PROVIDER=kite), not the simulated mock feed."
        )
    return {
        "exchange": instrument.kite_options_exchange,
        "tradingsymbol": leg.tradingsymbol,
        "transaction_type": "BUY" if leg.side == Side.LONG else "SELL",
        "variety": "regular",
        "product": "NRML",
        "order_type": "MARKET",
        "quantity": leg.quantity_lots * instrument.lot_size,
    }


def fetch_basket_margin(legs: list[Leg], instrument: Instrument, consider_positions: bool = False) -> KiteMarginResult:
    """Real, hedge-benefit-aware margin for a standalone combo (no order
    placed). ``consider_positions=False`` prices the basket on its own,
    matching what ``app/margin/span.py`` represents, rather than the
    incremental margin against whatever else is already open in the account.
    """
    if not legs:
        return KiteMarginResult(0.0, 0.0, 0.0, 0.0, {})

    kite = get_kite_client()
    orders = [_order_param(leg, instrument) for leg in legs]

    try:
        response = kite.basket_order_margins(orders, consider_positions=consider_positions)
    except KiteException as exc:
        raise KiteMarginError(f"basket_order_margins failed for {instrument.symbol}: {exc}") from exc

    section = response.get("final") or response.get("initial") if isinstance(response, dict) else None
    if not isinstance(section, dict):
        raise KiteMarginError(f"Unexpected basket_order_margins response shape (no final/initial section): {response}")

    total = section.get("total")
    if total is None:
        orders_section = section.get("orders")
        if isinstance(orders_section, list) and orders_section:
            total = sum(float(o.get("total", 0.0)) for o in orders_section)
    if total is None:
        raise KiteMarginError(f"Unexpected basket_order_margins response shape (no total): {response}")

    span = section.get("span")
    exposure = section.get("exposure")
    premium = section.get("option_premium")

    return KiteMarginResult(
        total_margin=float(total),
        span_margin=float(span) if span is not None else None,
        exposure_margin=float(exposure) if exposure is not None else None,
        option_premium=float(premium) if premium is not None else None,
        raw=response,
    )
