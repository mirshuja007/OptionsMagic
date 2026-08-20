"use client";

import { useState } from "react";
import SymbolSelector from "@/components/SymbolSelector";
import ExpirySelector from "@/components/ExpirySelector";
import type { DirectionBias, RankingMode, StrategyConstraintsIn, StrategyType } from "@/lib/types";

export interface StrategyFormValues {
  symbol: string;
  expiry: string;
  constraints: StrategyConstraintsIn;
}

const STRATEGY_TYPE_OPTIONS: { value: StrategyType; label: string }[] = [
  { value: "bull_put_spread", label: "Bull Put Spread" },
  { value: "bear_call_spread", label: "Bear Call Spread" },
  { value: "iron_condor", label: "Iron Condor" },
  { value: "iron_fly", label: "Iron Fly" },
  { value: "ratio_spread_call", label: "Ratio Spread (Call)" },
  { value: "ratio_spread_put", label: "Ratio Spread (Put)" },
];

export default function StrategyForm({
  onSubmit,
  loading,
}: {
  onSubmit: (values: StrategyFormValues) => void;
  loading: boolean;
}) {
  const [symbol, setSymbol] = useState("NIFTY");
  const [expiry, setExpiry] = useState("");
  const [minPop, setMinPop] = useState(80);
  const [minYield, setMinYield] = useState(1.0);
  const [maxProfit, setMaxProfit] = useState(5000);
  const [maxProfitUnlimited, setMaxProfitUnlimited] = useState(false);
  const [maxLoss, setMaxLoss] = useState(3000);
  const [maxLossUnlimited, setMaxLossUnlimited] = useState(false);
  const [marginCap, setMarginCap] = useState(500000);

  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [rankingMode, setRankingMode] = useState<RankingMode>("balanced");
  const [useResearchSignals, setUseResearchSignals] = useState(true);
  const [directionBias, setDirectionBias] = useState<DirectionBias>("auto");
  const [enabledTypes, setEnabledTypes] = useState<Set<StrategyType>>(
    new Set(STRATEGY_TYPE_OPTIONS.map((o) => o.value))
  );

  function handleSymbolChange(next: string) {
    setSymbol(next);
    setExpiry(""); // ExpirySelector will populate this with the new symbol's nearest expiry
  }

  function toggleType(type: StrategyType) {
    setEnabledTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        if (next.size > 1) next.delete(type); // always leave at least one type selected
      } else {
        next.add(type);
      }
      return next;
    });
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const allSelected = enabledTypes.size === STRATEGY_TYPE_OPTIONS.length;
    onSubmit({
      symbol,
      expiry,
      constraints: {
        min_probability_of_profit: minPop / 100,
        min_yield_pct: minYield / 100,
        max_profit_cap: maxProfitUnlimited ? null : maxProfit,
        max_loss_cap: maxLossUnlimited ? null : maxLoss,
        margin_cap: marginCap,
        ranking_mode: rankingMode,
        strategy_types: allSelected ? null : Array.from(enabledTypes),
        use_research_signals: useResearchSignals,
        direction_bias: directionBias,
      },
    });
  }

  return (
    <form onSubmit={submit} className="card flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <h2 className="text-sm font-medium text-muted">Constraint Inputs</h2>
        <div className="flex items-center gap-2">
          <SymbolSelector value={symbol} onChange={handleSymbolChange} />
          <ExpirySelector symbol={symbol} value={expiry} onChange={setExpiry} />
        </div>
      </div>

      <Field label="Minimum Probability of Profit (%)" value={minPop} onChange={setMinPop} min={1} max={99} />
      <Field label="Target Yield on Margin (%)" value={minYield} onChange={setMinYield} min={0} step={0.1} />
      <CapField
        label="Max Profit Ceiling (₹)"
        value={maxProfit}
        onChange={setMaxProfit}
        unlimited={maxProfitUnlimited}
        onUnlimitedChange={setMaxProfitUnlimited}
        min={0}
        step={100}
      />
      <CapField
        label="Max Loss Cap (₹)"
        value={maxLoss}
        onChange={setMaxLoss}
        unlimited={maxLossUnlimited}
        onUnlimitedChange={setMaxLossUnlimited}
        min={0}
        step={100}
      />
      <Field label="Margin Blocked Cap (₹)" value={marginCap} onChange={setMarginCap} min={0} step={1000} />

      <div className="flex flex-col gap-3 border-t border-border/50 pt-3">
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          className="flex items-center justify-between text-left text-sm font-medium text-muted transition hover:text-slate-200"
        >
          <span>Advanced: Ranking &amp; Research Signals</span>
          <span className="mono text-xs">{advancedOpen ? "▾" : "▸"}</span>
        </button>

        {advancedOpen && (
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-muted">Ranking Mode</span>
              <select
                value={rankingMode}
                onChange={(e) => setRankingMode(e.target.value as RankingMode)}
                className="rounded-md border border-border bg-panel px-3 py-2 text-sm"
              >
                <option value="yield">Yield-first — chase raw return on margin</option>
                <option value="balanced">Balanced — yield, PoP, and Sharpe evenly</option>
                <option value="safety">Safety-first — favor PoP and risk-adjusted quality</option>
              </select>
            </label>

            <label className="flex items-center justify-between text-sm">
              <span className="text-muted">Use Research Mode signals</span>
              <input
                type="checkbox"
                checked={useResearchSignals}
                onChange={(e) => setUseResearchSignals(e.target.checked)}
                className="h-4 w-4 accent-accent"
              />
            </label>
            {useResearchSignals && (
              <p className="text-xs text-muted">
                Nudges ranking toward candidates whose short strikes sit beyond OI-based support/resistance (or,
                for iron condors/flies, centered on Max Pain) and whose directional lean matches Smart OI / VWAP.
                Capped at +/-15% of the base score — it never overrides the hard constraints above.
              </p>
            )}

            <label className="flex flex-col gap-1 text-sm">
              <span className="text-muted">Direction Bias</span>
              <select
                value={directionBias}
                onChange={(e) => setDirectionBias(e.target.value as DirectionBias)}
                disabled={!useResearchSignals}
                className="rounded-md border border-border bg-panel px-3 py-2 text-sm disabled:opacity-50"
              >
                <option value="auto">Auto — follow Smart OI / VWAP</option>
                <option value="bullish">Force bullish</option>
                <option value="bearish">Force bearish</option>
                <option value="neutral">Force neutral</option>
              </select>
            </label>

            <div className="flex flex-col gap-1.5 text-sm">
              <span className="text-muted">Strategy Types</span>
              <div className="grid grid-cols-2 gap-1.5">
                {STRATEGY_TYPE_OPTIONS.map((opt) => (
                  <label key={opt.value} className="flex items-center gap-1.5 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={enabledTypes.has(opt.value)}
                      onChange={() => toggleType(opt.value)}
                      className="h-3.5 w-3.5 accent-accent"
                    />
                    {opt.label}
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted">
                Ratio spreads carry undefined risk on the excess short leg, so a finite Max Loss Cap almost always
                excludes them by design — check &quot;Unlimited&quot; on Max Loss Cap above to see any.
              </p>
            </div>
          </div>
        )}
      </div>

      <button
        type="submit"
        disabled={loading || !expiry}
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

function CapField({
  label,
  value,
  onChange,
  unlimited,
  onUnlimitedChange,
  min,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  unlimited: boolean;
  onUnlimitedChange: (v: boolean) => void;
  min?: number;
  step?: number;
}) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <div className="flex items-center justify-between">
        <span className="text-muted">{label}</span>
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input
            type="checkbox"
            checked={unlimited}
            onChange={(e) => onUnlimitedChange(e.target.checked)}
            className="h-3.5 w-3.5 accent-accent"
          />
          Unlimited
        </label>
      </div>
      <input
        type="number"
        value={value}
        min={min}
        step={step}
        disabled={unlimited}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded-md border border-border bg-panel px-3 py-2 mono disabled:opacity-40"
      />
    </div>
  );
}
