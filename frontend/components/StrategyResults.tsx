import type { StrategyResult } from "@/lib/types";

function fmtCurrency(n: number) {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function strategyLabel(type: string) {
  return type
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export default function StrategyResults({ results }: { results: StrategyResult[] }) {
  if (results.length === 0) {
    return (
      <div className="card text-sm text-muted">
        No strategies matched your constraints. Try relaxing the PoP, yield, or margin cap.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {results.map((r, idx) => (
        <div key={idx} className="card flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="rounded bg-accent/20 px-2 py-1 text-xs font-semibold text-accent">#{idx + 1}</span>
              <span className="text-base font-semibold">{strategyLabel(r.strategy_type)}</span>
            </div>
            <span className="mono text-sm text-muted">EV {fmtCurrency(r.expected_value)}</span>
          </div>

          <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <Metric label="PoP" value={`${(r.probability_of_profit * 100).toFixed(1)}%`} />
            <Metric label="Yield on Margin" value={`${(r.yield_pct * 100).toFixed(2)}%`} />
            <Metric label="Max Profit" value={fmtCurrency(r.payoff.max_profit)} tone="positive" />
            <Metric label="Max Loss" value={fmtCurrency(r.payoff.max_loss)} tone="negative" />
            <Metric label="Margin Blocked" value={fmtCurrency(r.margin.total_margin)} />
            <Metric label="Net Entry Credit" value={fmtCurrency(r.margin.net_entry_credit)} />
            <Metric label="Sharpe" value={r.sharpe.toFixed(3)} />
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
        </div>
      ))}
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
