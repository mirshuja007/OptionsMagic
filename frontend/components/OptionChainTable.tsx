import type { OptionChain } from "@/lib/types";

function fmt(n: number, digits = 2) {
  return n.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export default function OptionChainTable({ chain }: { chain: OptionChain }) {
  const atmStrike = chain.rows.reduce((closest, row) =>
    Math.abs(row.strike - chain.spot) < Math.abs(closest.strike - chain.spot) ? row : closest
  ).strike;

  return (
    <div className="card overflow-x-auto">
      <table className="w-full min-w-[900px] text-right text-sm">
        <thead className="text-xs uppercase text-muted">
          <tr>
            <th className="px-2 py-2 text-right">OI</th>
            <th className="px-2 py-2 text-right">Chg OI</th>
            <th className="px-2 py-2 text-right">Vol</th>
            <th className="px-2 py-2 text-right">IV</th>
            <th className="px-2 py-2 text-right">Delta</th>
            <th className="px-2 py-2 text-right">LTP</th>
            <th className="px-3 py-2 text-center font-semibold text-slate-200">Strike</th>
            <th className="px-2 py-2 text-left">LTP</th>
            <th className="px-2 py-2 text-left">Delta</th>
            <th className="px-2 py-2 text-left">IV</th>
            <th className="px-2 py-2 text-left">Vol</th>
            <th className="px-2 py-2 text-left">Chg OI</th>
            <th className="px-2 py-2 text-left">OI</th>
          </tr>
        </thead>
        <tbody className="mono">
          {chain.rows.map((row) => {
            const isAtm = row.strike === atmStrike;
            return (
              <tr key={row.strike} className={isAtm ? "bg-accent/10" : "hover:bg-white/5"}>
                <td className="px-2 py-1 text-right text-muted">{fmt(row.call.oi, 0)}</td>
                <td
                  className={`px-2 py-1 text-right ${
                    row.call.oi_change >= 0 ? "text-accent" : "text-danger"
                  }`}
                >
                  {fmt(row.call.oi_change, 0)}
                </td>
                <td className="px-2 py-1 text-right text-muted">{fmt(row.call.volume, 0)}</td>
                <td className="px-2 py-1 text-right">{fmt(row.call.iv * 100, 1)}%</td>
                <td className="px-2 py-1 text-right">{fmt(row.call.greeks.delta, 2)}</td>
                <td className="px-2 py-1 text-right font-medium">{fmt(row.call.ltp)}</td>
                <td className="px-3 py-1 text-center font-semibold text-slate-200">{fmt(row.strike, 0)}</td>
                <td className="px-2 py-1 text-left font-medium">{fmt(row.put.ltp)}</td>
                <td className="px-2 py-1 text-left">{fmt(row.put.greeks.delta, 2)}</td>
                <td className="px-2 py-1 text-left">{fmt(row.put.iv * 100, 1)}%</td>
                <td className="px-2 py-1 text-left text-muted">{fmt(row.put.volume, 0)}</td>
                <td
                  className={`px-2 py-1 text-left ${
                    row.put.oi_change >= 0 ? "text-accent" : "text-danger"
                  }`}
                >
                  {fmt(row.put.oi_change, 0)}
                </td>
                <td className="px-2 py-1 text-left text-muted">{fmt(row.put.oi, 0)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
