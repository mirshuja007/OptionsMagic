"""Conversions between API pydantic schemas and internal domain dataclasses."""
from __future__ import annotations

from fastapi import HTTPException

from app.api.schemas import LegIn, LegOut
from app.core.black_scholes import OptionType
from app.data.instruments import get_instrument
from app.data.mock_feed import OptionChain
from app.strategy.legs import Leg, Side


def leg_in_to_domain(leg_in: LegIn, chain: OptionChain) -> Leg:
    option_type = OptionType(leg_in.option_type)
    side = Side(leg_in.side)

    entry_price = leg_in.entry_price
    iv = leg_in.iv
    tradingsymbol = ""

    row = next((r for r in chain.rows if r.strike == leg_in.strike), None)
    if entry_price is None or iv is None:
        if row is None:
            raise HTTPException(status_code=422, detail=f"Strike {leg_in.strike} not found in {chain.symbol} chain")
    if row is not None:
        quote = row.call if option_type == OptionType.CALL else row.put
        entry_price = entry_price if entry_price is not None else round((quote.bid + quote.ask) / 2.0, 2)
        iv = iv if iv is not None else quote.iv
        tradingsymbol = quote.tradingsymbol

    instrument = get_instrument(chain.symbol)
    q = chain.risk_free_rate if instrument.pricing_carry_rate_equals_risk_free else 0.0

    return Leg(
        option_type=option_type,
        strike=leg_in.strike,
        side=side,
        quantity_lots=leg_in.quantity_lots,
        entry_price=entry_price,
        iv=iv,
        q=q,
        tradingsymbol=tradingsymbol,
    )


def leg_to_out(leg: Leg) -> LegOut:
    return LegOut(
        option_type=leg.option_type.value,
        strike=leg.strike,
        side=leg.side.value,
        quantity_lots=leg.quantity_lots,
        entry_price=leg.entry_price,
        iv=leg.iv,
        tradingsymbol=leg.tradingsymbol,
    )
