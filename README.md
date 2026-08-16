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
backend/   FastAPI + Python — analytics, constraint solver, margin, backtest, execution
frontend/  Next.js (App Router) + TypeScript + Tailwind — Research & Strategy Command UI
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

- `/research` — Research Mode: TradingView chart, spot/Max Pain/PCR/ATM IV tiles,
  Smart OI & GEX, multi-strike OI chart, IV skew chart, straddle decay chart,
  full option chain table with Greeks.
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
- **Expiry cadence**: NSE index options are weekly; MCX commodity options
  run monthly cycles that vary per commodity and shift for holidays.
  `kite_feed.py` doesn't hardcode either — it always picks from whatever
  expiries Kite's instrument dump actually lists. Only the *simulated*
  mock feed needs an assumed default (Thursdays for index, +20 days for
  commodities) since it has no real listing to read from.
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
pytest -q   # 91 tests
```

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api npm run dev
```

Open `http://localhost:3000` (redirects to `/research`).

## Key API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/instruments` | Supported indices, commodities (MCX), & F&O stocks |
| GET | `/api/data-provider` | Which feed is active — `mock` or `kite` |
| GET | `/api/option-chain/{symbol}` | Option chain (mock or live Kite, per `MARKET_DATA_PROVIDER`) |
| GET | `/api/analytics/max-pain/{symbol}` | Max Pain strike & payout curve |
| GET | `/api/analytics/pcr/{symbol}` | Put-Call Ratio (OI & volume) |
| GET | `/api/analytics/oi/{symbol}` | Per-strike OI, buildup, Smart OI, GEX |
| GET | `/api/analytics/volatility/{symbol}` | IV grid, ATM IV, 25-delta skew |
| GET | `/api/analytics/straddle/{symbol}` | ATM/multi-strike straddle, decay curve |
| POST | `/api/strategy/discover` | Run the constraint solver, get top 3 strategies |
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
