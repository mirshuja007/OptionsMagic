"""Static instrument metadata for supported NSE/BSE F&O underlyings.

Real deployments would source this (and live spot/chain data) from a broker
or vendor feed (Zerodha Kite, AngelOne SmartAPI, Fyers, NSE India, etc).
``app.data.mock_feed`` stands in as a self-contained simulated feed so the
rest of the platform is fully runnable and testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass


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

    # --- Kite Connect symbol mapping ---
    # ``kite_underlying_name`` filters the "name" column of Kite's NFO/BFO
    # instrument dump (``kite.instruments(exchange)``) down to this
    # underlying's option contracts. ``kite_spot_*`` locates the index/stock
    # spot quote. Index tradingsymbols in particular are NOT guaranteed
    # stable — verify these against a live ``kite.instruments()`` pull before
    # going live; a silent mismatch here means an empty chain, not a crash.
    kite_underlying_name: str = ""
    kite_options_exchange: str = "NFO"  # "NFO" for NSE F&O, "BFO" for BSE F&O (Sensex/Bankex)
    kite_spot_exchange: str = "NSE"
    kite_spot_tradingsymbol: str = ""

    @property
    def margin_price_scan_pct(self) -> float:
        """Approximate SPAN price scan range as a fraction of spot."""
        return 0.035 if self.is_index else 0.06

    @property
    def margin_exposure_pct(self) -> float:
        """Approximate SEBI exposure-margin add-on as a fraction of notional."""
        return 0.03 if self.is_index else 0.05


INDICES: dict[str, Instrument] = {
    "NIFTY": Instrument(
        "NIFTY", "Nifty 50", True, 75, 50, 24800.0, 0.12, "NSE",
        kite_underlying_name="NIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY 50",
    ),
    "BANKNIFTY": Instrument(
        "BANKNIFTY", "Bank Nifty", True, 35, 100, 51500.0, 0.14, "NSE",
        kite_underlying_name="BANKNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY BANK",
    ),
    "FINNIFTY": Instrument(
        "FINNIFTY", "Fin Nifty", True, 65, 50, 23400.0, 0.13, "NSE",
        kite_underlying_name="FINNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY FIN SERVICE",
    ),
    "MIDCPNIFTY": Instrument(
        "MIDCPNIFTY", "Midcap Nifty", True, 140, 25, 12600.0, 0.16, "NSE",
        kite_underlying_name="MIDCPNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY MID SELECT",
    ),
    "SENSEX": Instrument(
        "SENSEX", "Sensex", True, 20, 100, 81200.0, 0.13, "BSE",
        kite_underlying_name="SENSEX", kite_options_exchange="BFO",
        kite_spot_exchange="BSE", kite_spot_tradingsymbol="SENSEX",
    ),
}

# A representative slice of liquid F&O stocks (the full StockMojo baseline
# covers 200+; this set is enough to exercise every code path realistically).
# For stocks, Kite's NFO "name" column and the NSE equity tradingsymbol both
# match the plain trading symbol, so no override is needed beyond the flags.
STOCKS: dict[str, Instrument] = {
    "RELIANCE": Instrument("RELIANCE", "Reliance Industries", False, 500, 20, 2950.0, 0.22, "NSE",
                            kite_underlying_name="RELIANCE", kite_spot_tradingsymbol="RELIANCE"),
    "HDFCBANK": Instrument("HDFCBANK", "HDFC Bank", False, 550, 10, 1680.0, 0.20, "NSE",
                            kite_underlying_name="HDFCBANK", kite_spot_tradingsymbol="HDFCBANK"),
    "INFY": Instrument("INFY", "Infosys", False, 400, 20, 1850.0, 0.24, "NSE",
                        kite_underlying_name="INFY", kite_spot_tradingsymbol="INFY"),
    "TCS": Instrument("TCS", "Tata Consultancy Services", False, 175, 20, 4150.0, 0.19, "NSE",
                       kite_underlying_name="TCS", kite_spot_tradingsymbol="TCS"),
    "ICICIBANK": Instrument("ICICIBANK", "ICICI Bank", False, 700, 10, 1240.0, 0.21, "NSE",
                             kite_underlying_name="ICICIBANK", kite_spot_tradingsymbol="ICICIBANK"),
    "TATASTEEL": Instrument("TATASTEEL", "Tata Steel", False, 5500, 2, 165.0, 0.28, "NSE",
                             kite_underlying_name="TATASTEEL", kite_spot_tradingsymbol="TATASTEEL"),
    "SBIN": Instrument("SBIN", "State Bank of India", False, 750, 5, 815.0, 0.25, "NSE",
                        kite_underlying_name="SBIN", kite_spot_tradingsymbol="SBIN"),
}

ALL_INSTRUMENTS: dict[str, Instrument] = {**INDICES, **STOCKS}


def get_instrument(symbol: str) -> Instrument:
    symbol = symbol.upper()
    if symbol not in ALL_INSTRUMENTS:
        raise KeyError(f"Unknown instrument '{symbol}'")
    return ALL_INSTRUMENTS[symbol]
