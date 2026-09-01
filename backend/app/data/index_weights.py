"""Real NIFTY 50 constituent weights, as a dated snapshot the user pasted
in from smart-investing.in's live weightage page ("NIFTY 50 Index Weightage
& List of Stocks -Sep 01,2026"), captured 2026-09-01 IST.

This is a snapshot, not a live figure. NSE rebalances index constituents/
weights semi-annually (end-March, end-September) and free-float weights
drift daily with price — treat these numbers as approximately September
2026, not perpetually current. There is no automated refresh path: this
sandbox's network egress proxy rejects every finance-data site tried
(niftyindices.com, nseindia.com/archives, bseindia.com, tickertape.in,
smart-investing.in, dhan.co, even en.wikipedia.org — all "organization
policy" CONNECT denials, confirmed with a raw curl, not just this tool's
own domain allowlist). Refreshing this data means pasting a new snapshot,
the same way this one arrived.

The source table listed 49 rows (NIFTY 50 nominally has 50 constituents);
they sum to ~100.01%, so whatever's missing carries negligible weight —
not treated as a data gap worth flagging further.

Every symbol below was cross-checked against the live NSE/BSE Closing
Auction Session participant CSVs the user also supplied on 2026-09-01, so
these are confirmed-correct real trading symbols (e.g. the ``BAJAJ-AUTO``
hyphen, ``TMPV`` for Tata Motors Passenger Vehicles), not guesses.

No SENSEX 30 weight table has been supplied yet — see ``app.data.cas``'s
``compute_bias_signal`` docstring for why the constituent bias signal
stays equal-weighted until both index weight tables are in hand.
"""
from __future__ import annotations

# symbol -> NIFTY 50 weight, percent, as of the 2026-09-01 snapshot above.
NIFTY50_WEIGHTS_PCT: dict[str, float] = {
    "RELIANCE": 9.19,
    "BHARTIARTL": 6.07,
    "HDFCBANK": 5.70,
    "ICICIBANK": 5.36,
    "SBIN": 4.96,
    "TCS": 4.45,
    "BAJFINANCE": 3.41,
    "LT": 2.85,
    "HINDUNILVR": 2.44,
    "INFY": 2.43,
    "SUNPHARMA": 2.41,
    "TITAN": 2.32,
    "KOTAKBANK": 2.20,
    "MARUTI": 2.11,
    "M&M": 2.10,
    "AXISBANK": 2.04,
    "ADANIENT": 2.01,
    "ADANIPORTS": 1.97,
    "HCLTECH": 1.91,
    "BAJAJ-AUTO": 1.77,
    "ULTRACEMCO": 1.75,
    "ITC": 1.74,
    "JSWSTEEL": 1.66,
    "NTPC": 1.65,
    "ETERNAL": 1.64,
    "BAJAJFINSV": 1.64,
    "BEL": 1.56,
    "ONGC": 1.55,
    "SHRIRAMFIN": 1.30,
    "COALINDIA": 1.29,
    "ASIANPAINT": 1.28,
    "POWERGRID": 1.28,
    "TATASTEEL": 1.19,
    "HINDALCO": 1.18,
    "GRASIM": 1.17,
    "EICHERMOT": 1.14,
    "INDIGO": 1.02,
    "WIPRO": 0.93,
    "SBILIFE": 0.91,
    "TECHM": 0.83,
    "JIOFIN": 0.81,
    "TRENT": 0.79,
    "APOLLOHOSP": 0.65,
    "HDFCLIFE": 0.61,
    "CIPLA": 0.60,
    "TMPV": 0.59,
    "TATACONSUM": 0.53,
    "MAXHEALTH": 0.51,
    "DRREDDY": 0.51,
}
