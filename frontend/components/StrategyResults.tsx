"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { LiveMarginResponse, StrategyResult } from "@/lib/types";

function fmtCurrency(n: number) {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function strategyLabel(type: string) {
  return type
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export default function StrategyResults({ symbol, results }: { symbol: string; results: StrategyResult[] }) {
  if (results.length === 0) {
    return (
      <div className="card text-sm text-muted">
        No strategies matched your constraints. Try relaxing the min probability of profit, max loss cap
        (often the binding one — a spread narrower than one strike step already risks more than a small cap
        allows), target yield, or margin cap.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {results.map((r, idx) => (
        <StrategyCard key={idx} rank={idx + 1} symbol={symbol} result={r} />
      ))}
    </div>
  );
}

function StrategyCard({ rank, symbol, result: r }: { rank: number; symbol: string; result: StrategyResult }) {
  const [liveMargin, setLiveMargin] = useState<LiveMarginResponse | null>(null);
  const [liveMarginError, setLiveMarginError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function verifyRealMargin() {
    setLoading(true);
    setLiveMarginError(null);
    api
      .liveMargin(symbol, r.legs)
      .then(setLiveMargin)
      .catch((e) => setLiveMarginError(String(e)))
      .finally(() => setLoading(false));
  }

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="rounded bg-accent/20 px-2 py-1 text-xs font-semibold text-accent">#{rank}</span>
          <span className="text-base font-semibold">{strategyLabel(r.strategy_type)}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="mono text-sm text-muted">EV {fmtCurrency(r.expected_value)}</span>
          <span
            className="mono rounded bg-white/5 px-2 py-0.5 text-xs text-slate-300"
            title="Ranking key: the yield/PoP/Sharpe blend (per the selected ranking mode) plus any Research-signal alignment nudge"
          >
            Score {r.composite_score.toFixed(3)}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <Metric label="PoP" value={`${(r.probability_of_profit * 100).toFixed(1)}%`} />
        <Metric label="Yield on Margin" value={`${(r.yield_pct * 100).toFixed(2)}%`} />
        <Metric label="Max Profit" value={fmtCurrency(r.payoff.max_profit)} tone="positive" />
        <Metric label="Max Loss" value={fmtCurrency(r.payoff.max_loss)} tone="negative" />
        <Metric label="Margin Blocked (est.)" value={fmtCurrency(r.margin.total_margin)} />
        <Metric label="Net Entry Credit" value={fmtCurrency(r.margin.net_entry_credit)} />
        <Metric label="Sharpe" value={r.sharpe.toFixed(3)} />
        <Metric
          label="Signal Alignment"
          value={`${(r.technical_alignment * 100).toFixed(0)}%`}
          tone={r.technical_alignment >= 0.5 ? "positive" : undefined}
        />
        {liveMargin && <Metric label="Margin Blocked (real, Kite)" value={fmtCurrency(liveMargin.total_margin)} tone="positive" />}
      </div>

      <table className="mono w-full text-xs">
        <thead className="text-muted">
          <tr>
            <th className="px-2 py-1 text-left">Side</th>
            <th className="px-2 py-1 text-left">Type</th>
            <th className="px-2 py-1 text-left">Strike</th>
            <th className="px-2 py-1 text-left">Qty (lots)</th>
            <th className="px-2 py-1 text-left">Entry</th>
            <th className="px-2 py-1 text-left">IV</th>
          </tr>
        </thead>
        <tbody>
          {r.legs.map((leg, i) => (
            <tr key={i} className="border-t border-border/50">
              <td className={`px-2 py-1 ${leg.side === "short" ? "text-danger" : "text-accent"}`}>{leg.side}</td>
              <td className="px-2 py-1">{leg.option_type}</td>
              <td className="px-2 py-1">{leg.strike}</td>
              <td className="px-2 py-1">{leg.quantity_lots}</td>
              <td className="px-2 py-1">{leg.entry_price.toFixed(2)}</td>
              <td className="px-2 py-1">{(leg.iv * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex items-center gap-3 border-t border-border/50 pt-3">
        <button
          onClick={verifyRealMargin}
          disabled={loading}
          className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-white/5 disabled:opacity-50"
        >
          {loading ? "Checking…" : "Verify Real Margin (Kite)"}
        </button>
        <span className="text-xs text-muted">
          Read-only broker lookup — requires MARKET_DATA_PROVIDER=kite and no order is placed.
        </span>
      </div>
      {liveMarginError && (
        <div className="text-xs text-danger">
          Couldn&apos;t fetch real margin: {liveMarginError}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "positive" | "negative" }) {
  const toneClass = tone === "positive" ? "text-accent" : tone === "negative" ? "text-danger" : "text-slate-100";
  return (
    <div className="flex flex-col">
      <span className="text-muted">{label}</span>
      <span className={`mono font-medium ${toneClass}`}>{value}</span>
    </div>
  );
}
