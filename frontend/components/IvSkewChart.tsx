"use client";

import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { VolatilityResponse } from "@/lib/types";

export default function IvSkewChart({ data }: { data: VolatilityResponse }) {
  const chartData = data.grid.map((g) => ({
    strike: g.strike,
    "Call IV": +(g.call_iv * 100).toFixed(2),
    "Put IV": +(g.put_iv * 100).toFixed(2),
  }));

  return (
    <div className="card h-80">
      <h3 className="mb-2 text-sm font-medium text-muted">Implied Volatility Skew</h3>
      <ResponsiveContainer width="100%" height="90%">
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="strike" stroke="#8b98a9" fontSize={11} />
          <YAxis stroke="#8b98a9" fontSize={11} unit="%" />
          <Tooltip contentStyle={{ background: "#121821", border: "1px solid #1f2937" }} />
          <Legend />
          <Line type="monotone" dataKey="Call IV" stroke="#22c55e" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="Put IV" stroke="#ef4444" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
