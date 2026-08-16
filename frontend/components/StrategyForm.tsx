"use client";

import { useState } from "react";
import SymbolSelector from "@/components/SymbolSelector";
import type { StrategyConstraintsIn } from "@/lib/types";

export interface StrategyFormValues {
  symbol: string;
  constraints: StrategyConstraintsIn;
}

export default function StrategyForm({
  onSubmit,
  loading,
}: {
  onSubmit: (values: StrategyFormValues) => void;
  loading: boolean;
}) {
  const [symbol, setSymbol] = useState("NIFTY");
  const [minPop, setMinPop] = useState(80);
  const [minYield, setMinYield] = useState(1.0);
  const [maxProfit, setMaxProfit] = useState(5000);
  const [maxLoss, setMaxLoss] = useState(3000);
  const [marginCap, setMarginCap] = useState(500000);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit({
      symbol,
      constraints: {
        min_probability_of_profit: minPop / 100,
        min_yield_pct: minYield / 100,
        max_profit_cap: maxProfit,
        max_loss_cap: maxLoss,
        margin_cap: marginCap,
      },
    });
  }

  return (
    <form onSubmit={submit} className="card flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted">Constraint Inputs</h2>
        <SymbolSelector value={symbol} onChange={setSymbol} />
      </div>

      <Field label="Minimum Probability of Profit (%)" value={minPop} onChange={setMinPop} min={1} max={99} />
      <Field label="Target Yield on Margin (%)" value={minYield} onChange={setMinYield} min={0} step={0.1} />
      <Field label="Max Profit Ceiling (₹)" value={maxProfit} onChange={setMaxProfit} min={0} step={100} />
      <Field label="Max Loss Cap (₹)" value={maxLoss} onChange={setMaxLoss} min={0} step={100} />
      <Field label="Margin Blocked Cap (₹)" value={marginCap} onChange={setMarginCap} min={0} step={1000} />

      <button
        type="submit"
        disabled={loading}
        className="mt-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-black transition hover:opacity-90 disabled:opacity-50"
      >
        {loading ? "Solving…" : "Discover Strategies"}
      </button>
    </form>
  );
}

function Field({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded-md border border-border bg-panel px-3 py-2 mono"
      />
    </label>
  );
}
