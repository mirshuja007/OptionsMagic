# Custom-Mojo — Intelligent Option Strategy Synthesis & Execution Suite

An Indian options analytics and constraint-solving strategy discovery platform for
Nifty 50, BankNifty, FinNifty, MidcpNifty, Sensex, and liquid F&O stocks. Two
modes: **Research Mode** (option chain, Max Pain, PCR, OI/Smart OI, IV skew,
Greeks, straddle decay) and **Strategy Command Mode** (the constraint-solving
multi-leg strategy engine, backtesting, and paper execution).

## Architecture

```
backend/   FastAPI + Python — analytics, constraint solver, margin, backtest, execution
frontend/  Next.js (App Router) + TypeScript + Tailwind — Research & Strategy Command UI
```

### Backend (`backend/app`)

| Module | Responsibility |
|---|---|
| `core/black_scholes.py` | Pricing, Greeks (Δ Γ Θ V ρ), implied-vol solver (Newton-Raphson + bisection) |
| `core/pop.py` | Probability of Profit — delta-approximation heuristic and GBM Monte Carlo |
| `data/mock_feed.py` | **Simulated** live option-chain feed (see "What's simulated" below) |
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
  legs, margin breakdown, payoff, PoP, EV, and Sharpe.

## What's simulated vs. real

This is a fully runnable, self-contained build with **no external
credentials required**. To keep it that way, one layer is a documented
simulation standing in for a paid vendor/broker feed:

- **`app/data/mock_feed.py`** generates realistic option chains (Black-Scholes
  priced, with a volatility skew/smirk, OI/volume distributed around the
  ATM strike) and minute-by-minute underlying paths. Swap this module for a
  real feed adapter (Kite Connect, SmartAPI, Fyers, NSE India) — the rest of
  the platform (analytics, solver, margin, backtest, execution) consumes it
  through the same `OptionChain` shape and needs no other changes.
- **`app/broker/paper.py`** fills orders at the mid price with no slippage.
  Implement `BrokerAdapter` (`app/broker/base.py`) against a real broker SDK
  to go live — that requires that broker's API key/secret, which does not
  belong in this codebase.
- **`app/margin/span.py`** approximates NSE's SPAN methodology (price/vol
  scanning + exposure margin) rather than calling the exchange's proprietary
  SPAN engine, which requires its daily risk-parameter files.
- **Smart OI** (`app/analytics/oi.py`) is a documented heuristic (OI-change
  weighted by proximity to ATM) standing in for NSE's separate FII/DII
  participant-wise open-interest bulletin.
- Backtest replay holds each leg's IV fixed at entry (no historical IV
  surface) — see the docstring in `app/backtest/replay.py`.

Everything else — Black-Scholes pricing, Greeks, PoP (delta-approx and Monte
Carlo), the combinatorial strategy generator, the constraint solver, payoff
math, and risk-automation logic — is real, tested computation, not
placeholders.

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
pytest -q   # 42 tests
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
| GET | `/api/instruments` | Supported indices & F&O stocks |
| GET | `/api/option-chain/{symbol}` | Live (simulated) option chain |
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
