"""Static instrument metadata for supported NSE/BSE/MCX underlyings.

Real deployments would source this (and live spot/chain data) from a broker
or vendor feed (Zerodha Kite, AngelOne SmartAPI, Fyers, NSE/MCX India, etc).
``app.data.mock_feed`` stands in as a self-contained simulated feed so the
rest of the platform is fully runnable and testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class Instrument:
    symbol: str
    display_name: str
    is_index: bool
    lot_size: int
    strike_step: float
    base_spot: float
    base_iv: float  # annualized, decimal
    exchange: str

    # Commodity options (MCX Crude Oil, Gold, ...) are options *on futures
    # contracts*, not on a spot index — this drives both margin sizing and
    # pricing convention below. Kept as its own bool (mirroring is_index)
    # rather than a free-form "asset_class" string so there's no way for an
    # entry below to forget to set it and silently misclassify.
    is_commodity: bool = False

    # --- Expiry cadence (mock feed only — see note below) ---
    # "weekly" | "monthly". NSE/BSE have changed these rules twice in the
    # last two years (SEBI-driven consolidation to one weekly-expiry index
    # per exchange, then a Thursday->Tuesday/Thursday day shift). As of the
    # last time this was checked (rules effective Aug/Sep 2025):
    #   - NSE: only Nifty 50 has weekly options, expiring Tuesday. BankNifty,
    #     FinNifty, MidcpNifty, and single-stock F&O are monthly-only,
    #     expiring the last Tuesday of the month.
    #   - BSE: Sensex keeps weekly expiry, on Thursday.
    # These fields exist so a future rule change is a one-line edit per
    # instrument instead of a hunt through pricing code. Re-verify against
    # NSE/BSE circulars (or just trust kite_feed.py, which reads real listed
    # expiries and never hardcodes any of this) before relying on this for
    # anything beyond the mock feed's simulated default.
    expiry_cadence: str = "weekly"
    expiry_weekday: int = 1  # Monday=0 ... Sunday=6 (Python date.weekday()); 1 = Tuesday

    # --- Kite Connect symbol mapping ---
    # ``kite_underlying_name`` filters the "name" column of Kite's
    # NFO/BFO/MCX instrument dump (``kite.instruments(exchange)``) down to
    # this underlying's option contracts. ``kite_spot_*`` locates the
    # index/stock spot quote — for commodities (is_commodity=True) it's
    # unused; the underlying's price instead comes from the matching futures
    # contract, resolved dynamically per options expiry (see kite_feed.py).
    # Index tradingsymbols in particular are NOT guaranteed stable — verify
    # these against a live ``kite.instruments()`` pull before going live; a
    # silent mismatch here means an empty chain, not a crash.
    kite_underlying_name: str = ""
    kite_options_exchange: str = "NFO"  # "NFO" NSE F&O, "BFO" BSE F&O, "MCX" commodities
    kite_spot_exchange: str = "NSE"
    kite_spot_tradingsymbol: str = ""

    @property
    def asset_class(self) -> str:
        """"index" | "commodity" | "equity", derived from is_index/is_commodity."""
        if self.is_commodity:
            return "commodity"
        return "index" if self.is_index else "equity"

    @property
    def pricing_carry_rate_equals_risk_free(self) -> bool:
        """Commodity options are options-on-futures: correct Black-Scholes
        pricing sets the carry/dividend yield q equal to r, which reduces
        the standard formula to Black-76 (the market-standard model for
        options on futures). Equity/index options here use q=0.
        """
        return self.is_commodity

    @property
    def margin_price_scan_pct(self) -> float:
        """Approximate SPAN price scan range as a fraction of spot."""
        if self.is_commodity:
            return 0.09  # commodities (esp. crude) run materially more volatile than equity indices
        return 0.035 if self.is_index else 0.06

    @property
    def margin_exposure_pct(self) -> float:
        """Approximate exchange exposure-margin add-on as a fraction of notional."""
        if self.is_commodity:
            return 0.06
        return 0.03 if self.is_index else 0.05

    @property
    def session_start(self) -> time:
        """Approximate; MCX's evening session in particular varies by
        commodity and by US daylight-saving time — treat as illustrative.
        """
        return time(9, 0) if self.is_commodity else time(9, 15)

    @property
    def session_end(self) -> time:
        return time(23, 30) if self.is_commodity else time(15, 30)


INDICES: dict[str, Instrument] = {
    "NIFTY": Instrument(
        "NIFTY", "Nifty 50", True, 75, 50, 24800.0, 0.12, "NSE",
        kite_underlying_name="NIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY 50",
        expiry_cadence="weekly", expiry_weekday=1,  # Tuesday — the only NSE index that still gets weekly expiry
    ),
    "BANKNIFTY": Instrument(
        "BANKNIFTY", "Bank Nifty", True, 35, 100, 51500.0, 0.14, "NSE",
        kite_underlying_name="BANKNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY BANK",
        expiry_cadence="monthly", expiry_weekday=1,  # weekly discontinued Nov 2024; last-Tuesday monthly since
    ),
    "FINNIFTY": Instrument(
        "FINNIFTY", "Fin Nifty", True, 65, 50, 23400.0, 0.13, "NSE",
        kite_underlying_name="FINNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY FIN SERVICE",
        expiry_cadence="monthly", expiry_weekday=1,
    ),
    "MIDCPNIFTY": Instrument(
        "MIDCPNIFTY", "Midcap Nifty", True, 140, 25, 12600.0, 0.16, "NSE",
        kite_underlying_name="MIDCPNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY MID SELECT",
        expiry_cadence="monthly", expiry_weekday=1,
    ),
    "SENSEX": Instrument(
        "SENSEX", "Sensex", True, 20, 100, 81200.0, 0.13, "BSE",
        kite_underlying_name="SENSEX", kite_options_exchange="BFO",
        kite_spot_exchange="BSE", kite_spot_tradingsymbol="SENSEX",
        expiry_cadence="weekly", expiry_weekday=3,  # Thursday — BSE's one weekly-expiry index
    ),
}

# A representative slice of liquid F&O stocks (the full StockMojo baseline
# covers 200+; this set is enough to exercise every code path realistically).
# For stocks, Kite's NFO "name" column and the NSE equity tradingsymbol both
# match the plain trading symbol, so no override is needed beyond the flags.
# Single-stock F&O is monthly-only, last Tuesday of the month, same as the
# non-Nifty indices above.
STOCKS: dict[str, Instrument] = {
    "RELIANCE": Instrument("RELIANCE", "Reliance Industries", False, 500, 20, 2950.0, 0.22, "NSE",
                            kite_underlying_name="RELIANCE", kite_spot_tradingsymbol="RELIANCE",
                            expiry_cadence="monthly", expiry_weekday=1),
    "HDFCBANK": Instrument("HDFCBANK", "HDFC Bank", False, 550, 10, 1680.0, 0.20, "NSE",
                            kite_underlying_name="HDFCBANK", kite_spot_tradingsymbol="HDFCBANK",
                            expiry_cadence="monthly", expiry_weekday=1),
    "INFY": Instrument("INFY", "Infosys", False, 400, 20, 1850.0, 0.24, "NSE",
                        kite_underlying_name="INFY", kite_spot_tradingsymbol="INFY",
                        expiry_cadence="monthly", expiry_weekday=1),
    "TCS": Instrument("TCS", "Tata Consultancy Services", False, 175, 20, 4150.0, 0.19, "NSE",
                       kite_underlying_name="TCS", kite_spot_tradingsymbol="TCS",
                       expiry_cadence="monthly", expiry_weekday=1),
    "ICICIBANK": Instrument("ICICIBANK", "ICICI Bank", False, 700, 10, 1240.0, 0.21, "NSE",
                             kite_underlying_name="ICICIBANK", kite_spot_tradingsymbol="ICICIBANK",
                             expiry_cadence="monthly", expiry_weekday=1),
    "TATASTEEL": Instrument("TATASTEEL", "Tata Steel", False, 5500, 2, 165.0, 0.28, "NSE",
                             kite_underlying_name="TATASTEEL", kite_spot_tradingsymbol="TATASTEEL",
                             expiry_cadence="monthly", expiry_weekday=1),
    "SBIN": Instrument("SBIN", "State Bank of India", False, 750, 5, 815.0, 0.25, "NSE",
                        kite_underlying_name="SBIN", kite_spot_tradingsymbol="SBIN",
                        expiry_cadence="monthly", expiry_weekday=1),
}

# MCX commodity options — options on futures contracts, settled per MCX's
# monthly (not weekly) expiry cycles. lot_size/strike_step/base_spot/base_iv
# below are ILLUSTRATIVE PLACEHOLDERS: MCX periodically revises contract
# specs (lot size directly scales P&L and margin), and commodity prices move
# enough that a hardcoded "base_spot" is only good for rough shape-testing.
# Verify against a live `kite.instruments("MCX")` pull before trusting any
# margin/yield number this produces. kite_spot_tradingsymbol is intentionally
# blank — the underlying price is resolved dynamically to the matching
# futures contract per options expiry (see kite_feed.py's futures lookup).
COMMODITIES: dict[str, Instrument] = {
    "CRUDEOIL": Instrument(
        "CRUDEOIL", "Crude Oil", False, 100, 50, 6200.0, 0.35, "MCX",
        is_commodity=True, expiry_cadence="monthly",  # expiry_weekday unused: MCX's calendar isn't a fixed weekday
        kite_underlying_name="CRUDEOIL", kite_options_exchange="MCX",
        kite_spot_exchange="MCX", kite_spot_tradingsymbol="",
    ),
    "GOLD": Instrument(
        "GOLD", "Gold", False, 100, 100, 72000.0, 0.14, "MCX",
        is_commodity=True, expiry_cadence="monthly",
        kite_underlying_name="GOLD", kite_options_exchange="MCX",
        kite_spot_exchange="MCX", kite_spot_tradingsymbol="",
    ),
}

ALL_INSTRUMENTS: dict[str, Instrument] = {**INDICES, **STOCKS, **COMMODITIES}


def get_instrument(symbol: str) -> Instrument:
    symbol = symbol.upper()
    if symbol not in ALL_INSTRUMENTS:
        raise KeyError(f"Unknown instrument '{symbol}'")
    return ALL_INSTRUMENTS[symbol]
