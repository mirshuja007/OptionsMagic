# Custom-Mojo — Intelligent Option Strategy Synthesis & Execution Suite

An Indian options analytics and constraint-solving strategy discovery platform.
Primary focus: **Nifty, Sensex, MCX Crude Oil options, and MCX Gold options** —
plus BankNifty/FinNifty/MidcpNifty and a slice of liquid F&O stocks. Two
modes: **Research Mode** (option chain, Max Pain, PCR, OI/Smart OI, IV skew,
Greeks, straddle decay) and **Strategy Command Mode** (the constraint-solving
multi-leg strategy engine and backtesting).

Current scope is **strategy discovery and analytics, not automated order
execution** — the platform tells you what to trade and lets you paper-test it;
placing the actual order is a manual step you take in your broker. See
"Next steps not yet built" if that changes.

## Architecture

```
backend/          FastAPI + Python — analytics, constraint solver, margin, backtest, execution
frontend/          Next.js (App Router) + TypeScript + Tailwind — Research & Strategy Command UI
streamlit_app.py  Alternate frontend for Streamlit Community Cloud — see "Deploying to
streamlit_pages/  Streamlit Community Cloud" below. Same backend/app/* code, different UI.
```

### Backend (`backend/app`)

| Module | Responsibility |
|---|---|
| `core/black_scholes.py` | Pricing, Greeks (Δ Γ Θ V ρ), implied-vol solver (Newton-Raphson + bisection) — also doubles as Black-76 for options-on-futures when called with `q=r` (see "Index & equity options vs. commodity options" below) |
| `core/pop.py` | Probability of Profit — delta-approximation heuristic and GBM Monte Carlo |
| `data/instruments.py` | Per-underlying specs (lot size, strike step, Kite symbol mapping) and `is_index`/`is_commodity` flags driving margin sizing and pricing convention |
| `data/mock_feed.py` | **Simulated** option-chain feed, on by default (see "Market data providers" below) |
| `data/kite_feed.py` | **Live** option-chain feed via Zerodha Kite Connect |
| `data/feed.py` | Facade that picks mock vs. Kite via `MARKET_DATA_PROVIDER` — the only module the rest of the app imports from |
| `analytics/` | Max Pain, PCR, OI/Smart-OI/buildup classification, Gamma Exposure (GEX), IV grid/skew/IV-HV, straddle & premium decay |
| `strategy/legs.py` | Generic multi-leg payoff, mark-to-market, portfolio Greeks, defined/undefined-risk detection |
| `strategy/generator.py` | Combinatorial generation: credit spreads, iron condors, iron flies, ratio spreads |
| `strategy/solver.py` | **The Constraint-Solving Strategy Discovery Engine** — screens every candidate against PoP/yield/max-profit/max-loss/margin constraints and ranks survivors by expected value |
| `margin/span.py` | Simplified SPAN-style price/vol scanning + exposure margin estimator |
| `broker/` | Broker-agnostic execution interface (`base.py`) + in-memory `PaperBroker` |
| `risk/automation.py` | Take-profit / stop-loss / delta-hedge trigger evaluation |
| `backtest/replay.py` | Minute-by-minute replay of an open strategy over a simulated session |
| `api/` | FastAPI routers + Pydantic schemas exposing all of the above over HTTP |

### Frontend (`frontend/`)

- `/research` — Research Mode: intraday spot-price chart, spot/Max Pain/PCR/ATM
  IV tiles, Smart OI & GEX, multi-strike OI chart, IV skew chart, straddle
  decay chart, full option chain table with Greeks.
- `/strategy` — Strategy Command Mode: constraint input form (PoP, yield, max
  profit/loss, margin cap) → calls the solver → ranked strategy cards with
  legs, margin breakdown, payoff, PoP, EV, Sharpe, and a "Verify Real Margin
  (Kite)" button per card for a live, read-only broker margin check.

## Index & equity options vs. commodity options

Nifty/Sensex/stock options are options on a spot index or equity. MCX Crude
Oil and Gold options are **options on futures contracts** — a structurally
different instrument, priced with a different convention:

- **Underlying price**: for indices/stocks it's the live spot LTP. For
  commodities there's no single continuous spot price — `kite_feed.py`
  resolves the futures contract each options expiry actually references
  (the nearest one expiring on/after the options expiry) and uses *that*
  contract's LTP.
- **Pricing model**: options on futures are priced with Black-76, not plain
  Black-Scholes. Rather than a separate implementation, this reuses
  `core/black_scholes.py` with its carry/dividend-yield parameter `q` set
  equal to `r` — that's mathematically exactly Black-76 (see
  `tests/test_commodity_pricing.py` for the derivation check). Every
  `Leg` carries its own `q`, set from `Instrument.pricing_carry_rate_equals_risk_free`
  wherever a leg is constructed (`generator.py`, `converters.py`), so margin
  scanning, backtest replay, and risk automation all reprice commodity
  positions consistently.
- **Margin**: commodities get a wider SPAN price-scan range (9% vs. 3.5%
  for indices) — crude oil in particular moves far more than an equity
  index (it went briefly negative in April 2020).
- **Option chain strike coverage**: when no explicit `num_strikes` is
  requested, the chain covers a default band around spot — 5% either way
  for equity/index underlyings, 10% either way for commodities (same
  higher-volatility reasoning as the margin range above). See
  `Instrument.strike_range_pct`.
- **Expiry cadence**: NSE/BSE have changed weekly-expiry rules twice in the
  last two years (SEBI consolidated each exchange to one weekly-expiry
  index, then shifted the day). As of the last time this was verified
  (rules effective Aug/Sep 2025): only **Nifty 50** (NSE, Tuesday) and
  **Sensex** (BSE, Thursday) still get weekly options — BankNifty, FinNifty,
  MidcpNifty, and single-stock F&O are monthly-only, last Tuesday of the
  month. MCX commodity options run their own monthly cycles that vary per
  commodity and aren't tied to a fixed weekday at all.
  **`kite_feed.py` doesn't hardcode any of this** — it always picks from
  whatever expiries Kite's instrument dump actually lists, so it's correct
  regardless of the next rule change. Only the *simulated* mock feed needs
  an assumed default, via `Instrument.expiry_cadence`/`expiry_weekday`
  (`app/data/instruments.py`) — if NSE/BSE change the rules again, that's
  the one place to update; re-verify against a current NSE/BSE circular
  before trusting it, the same way `kite_feed.py`'s symbol mappings need
  periodic verification.
- **Contract specs**: `CRUDEOIL`/`GOLD` lot size, strike step, and base
  price in `app/data/instruments.py` are **illustrative placeholders** —
  MCX revises these periodically and lot size directly scales P&L/margin.
  Verify against a live `kite.instruments("MCX")` pull before trusting any
  number the solver produces for them.

## What's simulated vs. real

This is a fully runnable, self-contained build with **no external
credentials required** out of the box — `MARKET_DATA_PROVIDER=mock` (the
default) generates realistic option chains (Black-Scholes priced, with a
volatility skew/smirk, OI/volume distributed around the ATM strike) and
minute-by-minute underlying paths, no broker needed.

What's still a documented stand-in even with `MARKET_DATA_PROVIDER=kite`:

- **`app/broker/paper.py`** fills orders at the mid price with no slippage —
  and is the *only* execution path this platform currently has, by design
  (see "Current scope" above). Run it against the live Kite feed
  (`MARKET_DATA_PROVIDER=kite` + `PaperBroker`) to sanity-check a discovered
  strategy against real prices without placing anything.
- **`app/margin/span.py`** approximates NSE's SPAN methodology (price/vol
  scanning + exposure margin) rather than calling the exchange's proprietary
  SPAN engine — used for the solver's bulk sweep across hundreds of
  candidates, where calling Kite's margin API per-candidate would blow
  through its rate limits. **`app/margin/kite_margin.py`** gets the broker's
  real, hedge-benefit-aware number via `basket_order_margins()` (read-only,
  no order placed) for one candidate at a time — that's the "Verify Real
  Margin" button in Strategy Command Mode / `POST /api/margin/live`. Its
  response-shape parsing hasn't been exercised against a live account from
  this environment (see the module docstring); sanity-check your first few
  real calls.
- **Smart OI** (`app/analytics/oi.py`) is a documented heuristic (OI-change
  weighted by proximity to ATM) standing in for NSE's separate FII/DII
  participant-wise open-interest bulletin.
- **`kite_feed.py`'s `oi_change`** is always 0 — Kite's quote API returns
  point-in-time OI, not the change from the previous session; that needs a
  daily OI snapshot cache this doesn't have yet.
- Backtest replay holds each leg's IV fixed at entry (no historical IV
  surface), regardless of which price feed is active.
- **VWAP** (shown on the Research Mode intraday chart and stat tile) is
  computed the same way regardless of feed — cumulative(price × volume) /
  cumulative(volume) across the session (`app/analytics/vwap.py`) — but the
  volume feeding it differs: simulated (a U-shaped intraday profile) on
  `mock`, real per-minute traded volume from Kite's Historical Data API on
  `kite`.
- The expiry selector's date list (`GET /api/expiries/{symbol}`) is the real
  listed set on `kite`; on `mock` it's a plausible cadence-based projection
  (`mock_feed.available_expiries`), not verified listed dates.

Everything else — Black-Scholes pricing, Greeks, PoP (delta-approx and Monte
Carlo), the combinatorial strategy generator, the constraint solver, payoff
math, and risk-automation logic — is real, tested computation, not
placeholders.

## Market data providers

Set `MARKET_DATA_PROVIDER` (in `backend/.env`, copy from `.env.example`):

| Value | Behavior |
|---|---|
| `mock` (default) | Simulated feed, no credentials, safe for dev/tests |
| `kite` | Live Zerodha Kite Connect — requires setup below |

`GET /health` and `GET /api/data-provider` report which one is active — check
this before trusting anything Research Mode shows.

### Going live with Zerodha Kite Connect

1. Register an app at [developers.kite.trade](https://developers.kite.trade/apps)
   (₹2,000/month subscription; +₹2,000/month for the separate Historical
   Data API if you want real backtesting instead of simulated minute paths).
   Register an **Algo ID** too — SEBI's algo-trading framework requires one
   for any order placed via API, even for a personal account.
2. `cp backend/.env.example backend/.env` and set `KITE_API_KEY` and
   `MARKET_DATA_PROVIDER=kite`.
3. Every trading day, refresh the access token (it expires ~6 AM IST daily).
   Run this from **your own machine** — it's a live session against your
   real broker account either way, and shouldn't run from a shared/cloud
   environment:
   ```bash
   cd backend
   export KITE_API_KEY=...      # from step 1
   export KITE_API_SECRET=...   # never put this in .env or commit it
   python scripts/kite_login.py
   ```
   This walks you through the browser login and writes `KITE_ACCESS_TOKEN`
   into `backend/.env` for you. Restart the backend afterward. There's also
   an `--auto` mode that skips the browser using your user ID/password/TOTP
   secret directly — see the script's docstring for the tradeoffs before
   using it; it's opt-in for a reason.
4. **Verify the symbol mapping before trusting any output.** `Instrument`
   entries in `app/data/instruments.py` carry `kite_underlying_name`,
   `kite_spot_tradingsymbol`, etc. — index tradingsymbols and MCX commodity
   contract specs in particular aren't guaranteed stable. Confirm them
   against a live `kite.instruments("NFO")` / `kite.instruments("NSE")` /
   `kite.instruments("MCX")` pull; a mismatch raises a clear `KiteFeedError`
   rather than returning wrong data, but it's worth checking once up front.

### Next steps not yet built

Order execution is intentionally out of scope right now (this platform
discovers and analyzes strategies; you place the trade yourself). If that
changes later:

- A `KiteBroker` implementing `app/broker/base.py` for real order placement
  (no atomic basket order in Kite's API — legs go in sequentially, so this
  needs its own legging-risk handling), plus a registered Algo ID (see
  above) since SEBI requires one for any API-placed order.
- Persistence — positions/orders currently live in memory only
  (`PaperBroker`); nothing survives a process restart.
- An always-on worker for the risk-automation loop (`app/risk/automation.py`)
  — nothing currently calls `evaluate()` outside of backtests.
- A daily OI snapshot cache so `kite_feed.py` can report real OI *change*
  (currently always 0 — see "What's simulated vs. real" above).

## Running locally

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`. Run the test suite:

```bash
pytest -q   # 158 tests
```

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api npm run dev
```

Open `http://localhost:3000` (redirects to `/research`).

## Deploying to Streamlit Community Cloud

The FastAPI backend + Next.js frontend above is two processes, which
Streamlit Community Cloud can't host (it runs exactly one Python script).
`streamlit_app.py` (repo root) is a from-scratch **alternate frontend** for
exactly that situation: it re-implements Research Mode and Strategy Command
Mode as a Streamlit app that imports the backend's `app.*` modules directly
in-process — same analytics/solver/margin code as the Next.js app, calling
Python functions instead of HTTP endpoints, so there's no separate backend
service to stand up. It's a genuinely different UI (Streamlit's own widgets,
not the Tailwind design), not a proxy in front of the Next.js one.

Strategy Command Mode has two modes, toggled at the top of the page:
**Discover** (the constraint solver — same as the Next.js app's `/strategy`)
and **Manual Builder**, which lets you assemble a strategy leg by leg (pick
strike/type/side/lots, Add Position) and see payoff, Greeks, PoP, margin,
and breakevens update live via `app.strategy.solver.evaluate_strategy()`.
Its "Suggest Improvements" button (`optimize_legs()`) searches nearby
strikes for a variant of the same strategy shape with a higher max profit
at no worse max loss or margin, which you can apply with one click. This
mode isn't in the Next.js frontend yet.

A third page, **Futures Monitor**, shows live NIFTY and SENSEX index-futures
readings — current price, change vs. previous close, and the session's
high/low range (`app.data.kite_feed.futures_snapshot` / `mock_feed`'s
simulated equivalent, dispatched through the usual `app.data.feed` facade).

This page replaced an earlier "CAS Monitor" that tracked ~49 individual
stocks' prices relative to their own SEBI Closing Auction Session (live on
NSE/BSE from Aug 3, 2026) 3:00-3:15pm reference price. That measure had a
real blind spot, hit live on 2026-09-03: SENSEX fell as much as ~1600
points intraday before recovering to close down ~400, and because most
stocks' reference prices got set only after most of the drop had already
happened, the late recovery read as "upward" on the old signal even
though the day was still deeply negative overall — it was answering "how
did the last 20 minutes go relative to a baseline set near the bottom,"
not "how did today go." Index futures avoid that blind spot entirely — no
reference-window baseline to distort the read, and the day's high/low
range alone shows a swing like that at a glance. Worth being clear-eyed
about one thing: NIFTY/SENSEX futures don't go through CAS at all — CAS is
a per-stock mechanism for F&O-eligible stocks only — so this is a better
*index-direction* gauge, just not itself a CAS-specific reading. See
`streamlit_pages/cas.py`'s module docstring for the full account.

```
streamlit_app.py          entry point — page nav, secrets sync, provider badge
streamlit_pages/research.py   Research Mode (option chain, analytics, commentary)
streamlit_pages/strategy.py   Strategy Command Mode (constraint form + solver)
streamlit_pages/cas.py        Futures Monitor (live NIFTY/SENSEX futures — see module docstring for the name)
streamlit_pages/kite_login.py In-app "Login with Kite" panel (Research Mode) — see below
streamlit_pages/common.py     sys.path setup, Secrets->env sync, formatting helpers
requirements.txt (repo root)  what Streamlit Cloud installs — NOT backend/requirements.txt
.streamlit/config.toml        dark theme matching the Next.js palette
```

**To deploy:** on [share.streamlit.io](https://share.streamlit.io), point a
new app at this repo/branch with main file path `streamlit_app.py` (repo
root — not inside `backend/` or `streamlit_pages/`).

**Secrets** (Settings → Secrets, TOML format) — all optional, mirroring
`backend/.env.example`:

```toml
MARKET_DATA_PROVIDER = "mock"   # or "kite" for live data — see caveat below
KITE_API_KEY = ""
KITE_ACCESS_TOKEN = ""
KITE_API_SECRET = ""            # only needed to use the in-app "Login with Kite" panel below
KITE_TOTP_SECRET = ""           # optional — see "Login with Kite" below
```

Leaving these unset defaults to `mock`, same as running the backend locally
with no `.env`.

**Live refresh:** Research Mode has a "Live refresh (15s)" toggle (on by
default) that re-polls the chain and re-renders on a timer via
`st.fragment(run_every=...)` — the same 15-second polling cadence the
Next.js frontend uses, not a Kite Ticker websocket subscription (Streamlit
Cloud has no server-push mechanism for that). With `MARKET_DATA_PROVIDER=kite`
this means the numbers you see are genuinely current as of the last poll;
turn the toggle off to freeze on one snapshot (e.g. while reading the
commentary) without fighting a moving option chain.

**Kite Connect on a public app — read this before setting `MARKET_DATA_PROVIDER=kite`:**
Streamlit Community Cloud apps are public URLs by default (no built-in
per-user access control on the free tier). Two things follow from that:

1. **The daily token refresh doesn't happen on its own.** Kite access
   tokens expire every day (~6 AM IST). Research Mode has a "🔑 Login with
   Kite" panel (expander at the top of the page) that does this refresh
   in-app, without leaving the browser tab: click a Kite login link, log
   in yourself in your own browser, then paste back the resulting
   redirect URL/`request_token`. Only that short-lived, single-use code
   ever reaches the app — never your password. The token exchange itself
   calls Kite Connect's official, documented API endpoint.

   (An earlier version of this panel also offered an "Auto login" mode —
   User ID/password/TOTP submitted directly, no browser round trip. It
   was removed: that mode called Zerodha's *undocumented* login
   endpoints, which consistently 403'd when the request came from
   Streamlit Cloud's datacenter IP, so on this app's actual deployment it
   never worked — only added a dead-end option. The same automated-login
   approach still exists and still works for local, non-Cloud use via
   `backend/scripts/kite_login.py --auto`, run from your own machine.)

   A successful login updates *this running app process's* in-memory
   session and caches the access token to a small local file, dated to
   today (IST) — the next time the panel loads with no active session but
   that same-day cache still present (a script rerun, or a fresh browser
   tab reconnecting to the same running app), a "Use cached session"
   button appears so you don't have to repeat the login. Neither the
   in-memory session nor the cache can rewrite Streamlit Cloud's platform
   Secrets, and Streamlit Cloud doesn't promise the local filesystem
   survives a container restart (redeploys and idle sleep/wake cycles
   both reset it) — so a Cloud reboot or redeploy may still need a fresh
   login through the panel regardless, and a new trading day always does
   too, since Kite tokens expire daily no matter what.

   You can also skip the in-app panel entirely: run `kite_login.py`
   locally each trading morning and paste the fresh `KITE_ACCESS_TOKEN`
   into the app's Secrets panel. Whichever way, forgetting to refresh just
   means live mode goes stale (raises `KiteAuthError`, surfaced as a clean
   `st.error`, not a crash) until you do.
2. **Anyone with the app's URL can drive it against your live Kite quotes
   and read-only margin lookups** (option chain, Greeks, the "Verify Real
   Margin" button) using your API key/token — there's no order-placement
   path anywhere in this app (`PaperBroker` is simulated), so the actual
   exposure is API-quota consumption and letting strangers poke around
   your research tool, not account compromise. Still, default to `mock`
   unless you specifically want that and have thought it through.

## Key API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/instruments` | Supported indices, commodities (MCX), & F&O stocks |
| GET | `/api/data-provider` | Which feed is active — `mock` or `kite` |
| GET | `/api/expiries/{symbol}` | Expiry dates for the frontend's expiry selector (real listed dates on `kite`, illustrative cadence on `mock`) |
| GET | `/api/option-chain/{symbol}` | Option chain (mock or live Kite, per `MARKET_DATA_PROVIDER`); accepts `?expiry=YYYY-MM-DD` |
| GET | `/api/analytics/intraday/{symbol}` | Today's minute-by-minute spot price, volume, and running VWAP (powers the Research Mode chart) |
| GET | `/api/analytics/max-pain/{symbol}` | Max Pain strike & payout curve; accepts `?expiry=YYYY-MM-DD` |
| GET | `/api/analytics/pcr/{symbol}` | Put-Call Ratio (OI & volume); accepts `?expiry=YYYY-MM-DD` |
| GET | `/api/analytics/oi/{symbol}` | Per-strike OI, buildup, Smart OI, GEX; accepts `?expiry=YYYY-MM-DD` |
| GET | `/api/analytics/volatility/{symbol}` | IV grid, ATM IV, 25-delta skew; accepts `?expiry=YYYY-MM-DD` |
| GET | `/api/analytics/straddle/{symbol}` | ATM/multi-strike straddle, decay curve; accepts `?expiry=YYYY-MM-DD` |
| POST | `/api/strategy/discover` | Run the constraint solver, get top 3 strategies; accepts `"expiry"` in the request body |
| POST | `/api/backtest/run` | Minute-by-minute replay of a leg set |
| POST | `/api/execution/place-basket` | Paper-fill a multi-leg basket order |
| POST | `/api/execution/square-off` | Paper-close a basket |
| GET | `/api/execution/positions` / `/margin` | Paper broker state |
| POST | `/api/margin/live` | Real, read-only Kite margin for a leg set (no order placed) |

## Example: discovering strategies

```bash
curl -X POST http://localhost:8000/api/strategy/discover \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol": "NIFTY",
    "constraints": {
      "min_probability_of_profit": 0.8,
      "min_yield_pct": 0.01,
      "max_profit_cap": 5000,
      "max_loss_cap": 3000,
      "margin_cap": 500000
    }
  }'
```

This mirrors the spec's worked example: ≥80% PoP, ≥1% yield on margin
blocked, ≤₹5,000 max profit, ≤₹3,000 max loss, ≤₹5,00,000 margin — filtered
and ranked by expected value.
