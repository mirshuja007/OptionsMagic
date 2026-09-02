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
    def strike_range_pct(self) -> float:
        """Default option-chain coverage each side of spot, as a fraction of
        spot, when the caller doesn't request a specific strike count.
        Commodities (esp. crude) swing materially more than equity indices
        intraday, so they get a wider default band.
        """
        return 0.10 if self.is_commodity else 0.05

    @property
    def session_start(self) -> time:
        """Approximate; MCX's evening session in particular varies by
        commodity and by US daylight-saving time — treat as illustrative.
        """
        return time(9, 0) if self.is_commodity else time(9, 15)

    @property
    def session_end(self) -> time:
        return time(23, 30) if self.is_commodity else time(15, 30)


# base_spot below is a static illustrative anchor for the mock feed's random
# walk (see mock_feed.generate_option_chain) — it is NOT live and never
# updates itself; it just sits at whatever value was last set here. NIFTY/
# BANKNIFTY/FINNIFTY/MIDCPNIFTY refreshed from nseindia.com's own live
# index quotes on 2026-08-17 15:30 IST close (user-supplied screenshot):
# NIFTY 50 24,287.65 · NIFTY BANK 57,497.80 · NIFTY FIN SERVICE 26,217.15 ·
# NIFTY MIDCAP SELECT ~14,948. It will drift stale again as the real market
# moves — there is no substitute here for actually switching
# MARKET_DATA_PROVIDER=kite if you need numbers that track reality.
INDICES: dict[str, Instrument] = {
    "NIFTY": Instrument(
        "NIFTY", "Nifty 50", True, 75, 50, 24287.65, 0.12, "NSE",
        kite_underlying_name="NIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY 50",
        expiry_cadence="weekly", expiry_weekday=1,  # Tuesday — the only NSE index that still gets weekly expiry
    ),
    "BANKNIFTY": Instrument(
        "BANKNIFTY", "Bank Nifty", True, 35, 100, 57497.80, 0.14, "NSE",
        kite_underlying_name="BANKNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY BANK",
        expiry_cadence="monthly", expiry_weekday=1,  # weekly discontinued Nov 2024; last-Tuesday monthly since
    ),
    "FINNIFTY": Instrument(
        "FINNIFTY", "Fin Nifty", True, 65, 50, 26217.15, 0.13, "NSE",
        kite_underlying_name="FINNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY FIN SERVICE",
        expiry_cadence="monthly", expiry_weekday=1,
    ),
    "MIDCPNIFTY": Instrument(
        "MIDCPNIFTY", "Midcap Nifty", True, 140, 25, 14948.0, 0.16, "NSE",
        kite_underlying_name="MIDCPNIFTY", kite_options_exchange="NFO",
        kite_spot_exchange="NSE", kite_spot_tradingsymbol="NIFTY MID SELECT",
        expiry_cadence="monthly", expiry_weekday=1,
    ),
    "SENSEX": Instrument(
        "SENSEX", "Sensex", True, 20, 100, 78100.0, 0.13, "BSE",
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
    # NIFTY 50's next heaviest-weight constituents (by index weightage, Aug
    # 2026) — added deliberately over trying to cover the full ~200-stock
    # F&O/CAS universe, since these are the names that actually move the
    # index and are what CAS Monitor's stock list is meant to prioritize.
    # lot_size/base_spot below are best-effort snapshots (Aug 2026); like
    # every other entry here, verify against a live Kite pull before
    # trusting a margin/yield number this produces.
    "BHARTIARTL": Instrument("BHARTIARTL", "Bharti Airtel", False, 1851, 20, 1935.70, 0.22, "NSE",
                              kite_underlying_name="BHARTIARTL", kite_spot_tradingsymbol="BHARTIARTL",
                              expiry_cadence="monthly", expiry_weekday=1),
    "BAJFINANCE": Instrument("BAJFINANCE", "Bajaj Finance", False, 250, 20, 1087.40, 0.26, "NSE",
                              kite_underlying_name="BAJFINANCE", kite_spot_tradingsymbol="BAJFINANCE",
                              expiry_cadence="monthly", expiry_weekday=1),
    "LT": Instrument("LT", "Larsen & Toubro", False, 175, 50, 4087.0, 0.24, "NSE",
                      kite_underlying_name="LT", kite_spot_tradingsymbol="LT",
                      expiry_cadence="monthly", expiry_weekday=1),
    "HINDUNILVR": Instrument("HINDUNILVR", "Hindustan Unilever", False, 300, 20, 2023.30, 0.18, "NSE",
                              kite_underlying_name="HINDUNILVR", kite_spot_tradingsymbol="HINDUNILVR",
                              expiry_cadence="monthly", expiry_weekday=1),
    "SUNPHARMA": Instrument("SUNPHARMA", "Sun Pharmaceutical Industries", False, 350, 20, 1917.10, 0.23, "NSE",
                             kite_underlying_name="SUNPHARMA", kite_spot_tradingsymbol="SUNPHARMA",
                             expiry_cadence="monthly", expiry_weekday=1),
    "TITAN": Instrument("TITAN", "Titan Company", False, 175, 50, 5099.30, 0.25, "NSE",
                         kite_underlying_name="TITAN", kite_spot_tradingsymbol="TITAN",
                         expiry_cadence="monthly", expiry_weekday=1),
    "MARUTI": Instrument("MARUTI", "Maruti Suzuki India", False, 50, 100, 13639.0, 0.23, "NSE",
                          kite_underlying_name="MARUTI", kite_spot_tradingsymbol="MARUTI",
                          expiry_cadence="monthly", expiry_weekday=1),
    # Rounds out coverage to (approximately) the top ~20 heaviest-weight
    # constituents of NIFTY 50 *and* SENSEX 30 combined, deduplicated,
    # restricted to names that actually have listed F&O contracts — still
    # nowhere near the full ~200-stock F&O/CAS universe, but covers the
    # names that move both major indices. KOTAKBANK/AXISBANK/HCLTECH/ITC/
    # NTPC/M&M round out NIFTY 50's next tier by weight; INDIGO/TRENT/TECHM
    # are SENSEX 30 constituents heavy enough to matter there even where
    # their NIFTY 50 weight alone wouldn't have made this cut. Lot sizes
    # for the last few below are principled estimates (NSE roughly targets
    # a ₹5-10L contract value per lot) rather than a confirmed source, since
    # not every one of these turned up an exact current figure — same
    # "verify against a live Kite pull" caveat as everything else here.
    "KOTAKBANK": Instrument("KOTAKBANK", "Kotak Mahindra Bank", False, 1600, 5, 424.20, 0.22, "NSE",
                             kite_underlying_name="KOTAKBANK", kite_spot_tradingsymbol="KOTAKBANK",
                             expiry_cadence="monthly", expiry_weekday=1),
    "AXISBANK": Instrument("AXISBANK", "Axis Bank", False, 875, 10, 1246.0, 0.24, "NSE",
                            kite_underlying_name="AXISBANK", kite_spot_tradingsymbol="AXISBANK",
                            expiry_cadence="monthly", expiry_weekday=1),
    "HCLTECH": Instrument("HCLTECH", "HCL Technologies", False, 350, 20, 1301.20, 0.22, "NSE",
                           kite_underlying_name="HCLTECH", kite_spot_tradingsymbol="HCLTECH",
                           expiry_cadence="monthly", expiry_weekday=1),
    "ITC": Instrument("ITC", "ITC", False, 1575, 5, 269.0, 0.19, "NSE",
                       kite_underlying_name="ITC", kite_spot_tradingsymbol="ITC",
                       expiry_cadence="monthly", expiry_weekday=1),
    "NTPC": Instrument("NTPC", "NTPC", False, 1800, 5, 337.10, 0.20, "NSE",
                        kite_underlying_name="NTPC", kite_spot_tradingsymbol="NTPC",
                        expiry_cadence="monthly", expiry_weekday=1),
    "M&M": Instrument("M&M", "Mahindra & Mahindra", False, 175, 50, 3443.0, 0.26, "NSE",
                       kite_underlying_name="M&M", kite_spot_tradingsymbol="M&M",
                       expiry_cadence="monthly", expiry_weekday=1),
    "INDIGO": Instrument("INDIGO", "InterGlobe Aviation", False, 100, 50, 5275.0, 0.32, "NSE",
                          kite_underlying_name="INDIGO", kite_spot_tradingsymbol="INDIGO",
                          expiry_cadence="monthly", expiry_weekday=1),
    "TRENT": Instrument("TRENT", "Trent", False, 200, 50, 2915.0, 0.30, "NSE",
                         kite_underlying_name="TRENT", kite_spot_tradingsymbol="TRENT",
                         expiry_cadence="monthly", expiry_weekday=1),
    "TECHM": Instrument("TECHM", "Tech Mahindra", False, 600, 20, 1598.0, 0.25, "NSE",
                         kite_underlying_name="TECHM", kite_spot_tradingsymbol="TECHM",
                         expiry_cadence="monthly", expiry_weekday=1),
    # Full deduplicated NIFTY 50 + SENSEX 30 membership (49 distinct names —
    # every SENSEX 30 constituent turned out to already be a NIFTY 50 one
    # too, so the union is just NIFTY 50's own 49; see
    # app.data.index_weights.NIFTY50_WEIGHTS_PCT / SENSEX30_WEIGHTS_PCT,
    # both real user-supplied snapshots). Everything above this comment
    # was the earlier ~top-20-by-weight curation; these 26 round that out
    # to full membership per explicit request, so CAS Monitor and the
    # constituent bias signal cover every index name, not just the
    # heaviest ones. base_spot below is each stock's real CAS reference
    # price from the 2026-09-01 NSE Closing Auction Session CSV the user
    # supplied that day — an actual traded snapshot, not invented — but
    # lot_size/strike_step are principled estimates (NSE roughly targets a
    # ₹5-10L contract value per lot; strike step scaled to price the same
    # way the rest of this file's estimated entries are) with no live
    # source to confirm them against in this sandbox. Same "verify against
    # a live Kite pull before trusting a margin/yield number" caveat as
    # everywhere else here — this expands coverage, it doesn't upgrade
    # confidence in the guessed contract specs.
    "ADANIENT": Instrument("ADANIENT", "Adani Enterprises", False, 275, 100, 2852.60, 0.30, "NSE",
                            kite_underlying_name="ADANIENT", kite_spot_tradingsymbol="ADANIENT",
                            expiry_cadence="monthly", expiry_weekday=1),
    "ADANIPORTS": Instrument("ADANIPORTS", "Adani Ports and Special Economic Zone", False, 450, 50, 1642.70, 0.26,
                              "NSE", kite_underlying_name="ADANIPORTS", kite_spot_tradingsymbol="ADANIPORTS",
                              expiry_cadence="monthly", expiry_weekday=1),
    "APOLLOHOSP": Instrument("APOLLOHOSP", "Apollo Hospitals Enterprise", False, 85, 100, 8722.0, 0.24, "NSE",
                              kite_underlying_name="APOLLOHOSP", kite_spot_tradingsymbol="APOLLOHOSP",
                              expiry_cadence="monthly", expiry_weekday=1),
    "ASIANPAINT": Instrument("ASIANPAINT", "Asian Paints", False, 300, 100, 2563.90, 0.20, "NSE",
                              kite_underlying_name="ASIANPAINT", kite_spot_tradingsymbol="ASIANPAINT",
                              expiry_cadence="monthly", expiry_weekday=1),
    "BAJAJ-AUTO": Instrument("BAJAJ-AUTO", "Bajaj Auto", False, 60, 200, 12310.0, 0.24, "NSE",
                              kite_underlying_name="BAJAJ-AUTO", kite_spot_tradingsymbol="BAJAJ-AUTO",
                              expiry_cadence="monthly", expiry_weekday=1),
    "BAJAJFINSV": Instrument("BAJAJFINSV", "Bajaj Finserv", False, 375, 50, 1966.10, 0.25, "NSE",
                              kite_underlying_name="BAJAJFINSV", kite_spot_tradingsymbol="BAJAJFINSV",
                              expiry_cadence="monthly", expiry_weekday=1),
    "BEL": Instrument("BEL", "Bharat Electronics", False, 1800, 10, 410.25, 0.28, "NSE",
                       kite_underlying_name="BEL", kite_spot_tradingsymbol="BEL",
                       expiry_cadence="monthly", expiry_weekday=1),
    "CIPLA": Instrument("CIPLA", "Cipla", False, 550, 50, 1414.90, 0.22, "NSE",
                         kite_underlying_name="CIPLA", kite_spot_tradingsymbol="CIPLA",
                         expiry_cadence="monthly", expiry_weekday=1),
    "COALINDIA": Instrument("COALINDIA", "Coal India", False, 1900, 10, 401.65, 0.25, "NSE",
                             kite_underlying_name="COALINDIA", kite_spot_tradingsymbol="COALINDIA",
                             expiry_cadence="monthly", expiry_weekday=1),
    "DRREDDY": Instrument("DRREDDY", "Dr. Reddy's Laboratories", False, 650, 50, 1171.30, 0.23, "NSE",
                           kite_underlying_name="DRREDDY", kite_spot_tradingsymbol="DRREDDY",
                           expiry_cadence="monthly", expiry_weekday=1),
    "EICHERMOT": Instrument("EICHERMOT", "Eicher Motors", False, 95, 100, 7926.0, 0.24, "NSE",
                             kite_underlying_name="EICHERMOT", kite_spot_tradingsymbol="EICHERMOT",
                             expiry_cadence="monthly", expiry_weekday=1),
    "ETERNAL": Instrument("ETERNAL", "Eternal", False, 2300, 10, 327.25, 0.35, "NSE",
                           kite_underlying_name="ETERNAL", kite_spot_tradingsymbol="ETERNAL",
                           expiry_cadence="monthly", expiry_weekday=1),
    "GRASIM": Instrument("GRASIM", "Grasim Industries", False, 225, 100, 3297.70, 0.22, "NSE",
                          kite_underlying_name="GRASIM", kite_spot_tradingsymbol="GRASIM",
                          expiry_cadence="monthly", expiry_weekday=1),
    "HDFCLIFE": Instrument("HDFCLIFE", "HDFC Life Insurance", False, 1400, 20, 539.80, 0.20, "NSE",
                            kite_underlying_name="HDFCLIFE", kite_spot_tradingsymbol="HDFCLIFE",
                            expiry_cadence="monthly", expiry_weekday=1),
    "HINDALCO": Instrument("HINDALCO", "Hindalco Industries", False, 750, 50, 1014.20, 0.28, "NSE",
                            kite_underlying_name="HINDALCO", kite_spot_tradingsymbol="HINDALCO",
                            expiry_cadence="monthly", expiry_weekday=1),
    "JIOFIN": Instrument("JIOFIN", "Jio Financial Services", False, 3200, 5, 235.75, 0.30, "NSE",
                          kite_underlying_name="JIOFIN", kite_spot_tradingsymbol="JIOFIN",
                          expiry_cadence="monthly", expiry_weekday=1),
    "JSWSTEEL": Instrument("JSWSTEEL", "JSW Steel", False, 550, 50, 1309.10, 0.27, "NSE",
                            kite_underlying_name="JSWSTEEL", kite_spot_tradingsymbol="JSWSTEEL",
                            expiry_cadence="monthly", expiry_weekday=1),
    "MAXHEALTH": Instrument("MAXHEALTH", "Max Healthcare Institute", False, 750, 50, 1006.50, 0.24, "NSE",
                             kite_underlying_name="MAXHEALTH", kite_spot_tradingsymbol="MAXHEALTH",
                             expiry_cadence="monthly", expiry_weekday=1),
    "ONGC": Instrument("ONGC", "Oil & Natural Gas Corporation", False, 3200, 5, 236.14, 0.24, "NSE",
                        kite_underlying_name="ONGC", kite_spot_tradingsymbol="ONGC",
                        expiry_cadence="monthly", expiry_weekday=1),
    "POWERGRID": Instrument("POWERGRID", "Power Grid Corporation of India", False, 2900, 10, 263.05, 0.18, "NSE",
                             kite_underlying_name="POWERGRID", kite_spot_tradingsymbol="POWERGRID",
                             expiry_cadence="monthly", expiry_weekday=1),
    "SBILIFE": Instrument("SBILIFE", "SBI Life Insurance", False, 425, 50, 1741.40, 0.20, "NSE",
                           kite_underlying_name="SBILIFE", kite_spot_tradingsymbol="SBILIFE",
                           expiry_cadence="monthly", expiry_weekday=1),
    "SHRIRAMFIN": Instrument("SHRIRAMFIN", "Shriram Finance", False, 700, 50, 1054.10, 0.26, "NSE",
                              kite_underlying_name="SHRIRAMFIN", kite_spot_tradingsymbol="SHRIRAMFIN",
                              expiry_cadence="monthly", expiry_weekday=1),
    "TATACONSUM": Instrument("TATACONSUM", "Tata Consumer Products", False, 750, 50, 1027.10, 0.20, "NSE",
                              kite_underlying_name="TATACONSUM", kite_spot_tradingsymbol="TATACONSUM",
                              expiry_cadence="monthly", expiry_weekday=1),
    "TMPV": Instrument("TMPV", "Tata Motors Passenger Vehicles", False, 2400, 10, 309.80, 0.28, "NSE",
                        kite_underlying_name="TMPV", kite_spot_tradingsymbol="TMPV",
                        expiry_cadence="monthly", expiry_weekday=1),
    "ULTRACEMCO": Instrument("ULTRACEMCO", "UltraTech Cement", False, 65, 200, 11411.0, 0.21, "NSE",
                              kite_underlying_name="ULTRACEMCO", kite_spot_tradingsymbol="ULTRACEMCO",
                              expiry_cadence="monthly", expiry_weekday=1),
    "WIPRO": Instrument("WIPRO", "Wipro", False, 4200, 5, 180.90, 0.22, "NSE",
                         kite_underlying_name="WIPRO", kite_spot_tradingsymbol="WIPRO",
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
        "CRUDEOIL", "Crude Oil", False, 100, 50, 7800.0, 0.35, "MCX",
        is_commodity=True, expiry_cadence="monthly",  # expiry_weekday unused: MCX's calendar isn't a fixed weekday
        kite_underlying_name="CRUDEOIL", kite_options_exchange="MCX",
        kite_spot_exchange="MCX", kite_spot_tradingsymbol="",
    ),
    "GOLD": Instrument(
        "GOLD", "Gold", False, 100, 100, 153000.0, 0.14, "MCX",
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
