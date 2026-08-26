"""Combinatorial generation of multi-leg strategy candidates from a live
option chain: credit spreads, iron condors, iron flies, and ratio spreads.
Each candidate is a plain list of ``Leg`` that downstream margin/PoP/solver
code can treat identically regardless of strategy shape.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.black_scholes import OptionType
from app.data.instruments import get_instrument
from app.data.mock_feed import ChainRow, OptionChain
from app.strategy.legs import Leg, Side


@dataclass(frozen=True)
class Candidate:
    strategy_type: str
    legs: list[Leg]


def row_by_strike(chain: OptionChain) -> dict[float, ChainRow]:
    return {row.strike: row for row in chain.rows}


def _mid(bid: float, ask: float) -> float:
    return round((bid + ask) / 2.0, 2)


def carry_rate(chain: OptionChain) -> float:
    """0 for equity/index underlyings, r for futures-underlying (commodity)
    ones — see Instrument.pricing_carry_rate_equals_risk_free.
    """
    instrument = get_instrument(chain.symbol)
    return chain.risk_free_rate if instrument.pricing_carry_rate_equals_risk_free else 0.0


def build_leg(row: ChainRow, option_type: OptionType, side: Side, qty: int, q: float) -> Leg:
    quote = row.call if option_type == OptionType.CALL else row.put
    return Leg(
        option_type=option_type,
        strike=row.strike,
        side=side,
        quantity_lots=qty,
        entry_price=_mid(quote.bid, quote.ask),
        iv=quote.iv,
        q=q,
        tradingsymbol=quote.tradingsymbol,
    )


def bull_put_spreads(chain: OptionChain, widths: tuple[int, ...] = (1, 2, 3, 4)) -> list[Candidate]:
    by_strike = row_by_strike(chain)
    step = strike_step(chain)
    q = carry_rate(chain)
    out: list[Candidate] = []
    for row in chain.rows:
        if row.strike >= chain.spot:
            continue  # short leg must be OTM put
        for width in widths:
            long_strike = row.strike - width * step
            if long_strike not in by_strike:
                continue
            legs = [
                build_leg(row, OptionType.PUT, Side.SHORT, 1, q),
                build_leg(by_strike[long_strike], OptionType.PUT, Side.LONG, 1, q),
            ]
            out.append(Candidate("bull_put_spread", legs))
    return out


def bear_call_spreads(chain: OptionChain, widths: tuple[int, ...] = (1, 2, 3, 4)) -> list[Candidate]:
    by_strike = row_by_strike(chain)
    step = strike_step(chain)
    q = carry_rate(chain)
    out: list[Candidate] = []
    for row in chain.rows:
        if row.strike <= chain.spot:
            continue  # short leg must be OTM call
        for width in widths:
            long_strike = row.strike + width * step
            if long_strike not in by_strike:
                continue
            legs = [
                build_leg(row, OptionType.CALL, Side.SHORT, 1, q),
                build_leg(by_strike[long_strike], OptionType.CALL, Side.LONG, 1, q),
            ]
            out.append(Candidate("bear_call_spread", legs))
    return out


def _iron_condors_from(puts: list[Candidate], calls: list[Candidate], max_combos: int = 300) -> list[Candidate]:
    out: list[Candidate] = []
    for put_spread in puts:
        for call_spread in calls:
            legs = [*put_spread.legs, *call_spread.legs]
            out.append(Candidate("iron_condor", legs))
            if len(out) >= max_combos:
                return out
    return out


def iron_condors(
    chain: OptionChain,
    widths: tuple[int, ...] = (1, 2, 3, 4),
    max_combos: int = 300,
) -> list[Candidate]:
    return _iron_condors_from(bull_put_spreads(chain, widths), bear_call_spreads(chain, widths), max_combos)


def iron_flies(chain: OptionChain, wing_widths: tuple[int, ...] = (2, 3, 4, 5, 6)) -> list[Candidate]:
    by_strike = row_by_strike(chain)
    step = strike_step(chain)
    q = carry_rate(chain)
    atm_row = min(chain.rows, key=lambda r: abs(r.strike - chain.spot))
    out: list[Candidate] = []
    for width in wing_widths:
        call_wing_strike = atm_row.strike + width * step
        put_wing_strike = atm_row.strike - width * step
        if call_wing_strike not in by_strike or put_wing_strike not in by_strike:
            continue
        legs = [
            build_leg(atm_row, OptionType.CALL, Side.SHORT, 1, q),
            build_leg(atm_row, OptionType.PUT, Side.SHORT, 1, q),
            build_leg(by_strike[call_wing_strike], OptionType.CALL, Side.LONG, 1, q),
            build_leg(by_strike[put_wing_strike], OptionType.PUT, Side.LONG, 1, q),
        ]
        out.append(Candidate("iron_fly", legs))
    return out


def ratio_spreads(
    chain: OptionChain,
    sell_ratio: int = 2,
    otm_offsets: tuple[int, ...] = (2, 3, 4, 5),
) -> list[Candidate]:
    """1x-N ratio spreads: buy 1 near-the-money leg, sell N further-OTM legs
    on the same side. Undefined risk on the excess short legs — the solver's
    max-loss/margin filters naturally weed out unsuitable ones.
    """
    by_strike = row_by_strike(chain)
    step = strike_step(chain)
    q = carry_rate(chain)
    atm_row = min(chain.rows, key=lambda r: abs(r.strike - chain.spot))
    out: list[Candidate] = []

    for option_type in (OptionType.CALL, OptionType.PUT):
        long_strike = atm_row.strike
        long_row = by_strike[long_strike]
        for offset in otm_offsets:
            short_strike = long_strike + offset * step if option_type == OptionType.CALL else long_strike - offset * step
            if short_strike not in by_strike:
                continue
            legs = [
                build_leg(long_row, option_type, Side.LONG, 1, q),
                build_leg(by_strike[short_strike], option_type, Side.SHORT, sell_ratio, q),
            ]
            label = "call" if option_type == OptionType.CALL else "put"
            out.append(Candidate(f"ratio_spread_{label}", legs))
    return out


def strike_step(chain: OptionChain) -> float:
    strikes = sorted({row.strike for row in chain.rows})
    return strikes[1] - strikes[0] if len(strikes) > 1 else 1.0


#: Every strategy_type string a Candidate can carry, in generation order —
#: the vocabulary strategy_types filters (solver.py, the API) validate
#: against.
ALL_STRATEGY_TYPES = (
    "bull_put_spread",
    "bear_call_spread",
    "iron_condor",
    "iron_fly",
    "ratio_spread_call",
    "ratio_spread_put",
)


def generate_all_candidates(
    chain: OptionChain,
    max_iron_condor_combos: int = 300,
    strategy_types: set[str] | None = None,
) -> list[Candidate]:
    """All candidates, optionally restricted to a subset of
    ``ALL_STRATEGY_TYPES`` (``strategy_types=None`` means every type).
    Iron condors are built from bull put + bear call spreads, so both must
    be included whenever ``iron_condor`` is requested.
    """
    want = strategy_types if strategy_types is not None else set(ALL_STRATEGY_TYPES)
    need_puts = "bull_put_spread" in want or "iron_condor" in want
    need_calls = "bear_call_spread" in want or "iron_condor" in want

    puts = bull_put_spreads(chain) if need_puts else []
    calls = bear_call_spreads(chain) if need_calls else []

    out: list[Candidate] = []
    if "bull_put_spread" in want:
        out += puts
    if "bear_call_spread" in want:
        out += calls
    if "iron_condor" in want:
        out += _iron_condors_from(puts, calls, max_combos=max_iron_condor_combos)
    if "iron_fly" in want:
        out += iron_flies(chain)
    if "ratio_spread_call" in want or "ratio_spread_put" in want:
        out += [c for c in ratio_spreads(chain) if c.strategy_type in want]
    return out
