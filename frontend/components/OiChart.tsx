"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { StrikeOi } from "@/lib/types";

export default function OiChart({ data }: { data: StrikeOi[] }) {
  const chartData = data.map((d) => ({
    strike: d.strike,
    "Call OI": d.call_oi,
    "Put OI": d.put_oi,
  }));

  return (
    <div className="card h-80">
      <h3 className="mb-2 text-sm font-medium text-muted">MultiStrike Open Interest</h3>
      <ResponsiveContainer width="100%" height="90%">
        <BarChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="strike" stroke="#8b98a9" fontSize={11} />
          <YAxis stroke="#8b98a9" fontSize={11} />
          <Tooltip contentStyle={{ background: "#121821", border: "1px solid #1f2937" }} />
          <Legend />
          <Bar dataKey="Call OI" fill="#22c55e" />
          <Bar dataKey="Put OI" fill="#ef4444" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
