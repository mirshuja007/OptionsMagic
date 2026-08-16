"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { StraddleResponse } from "@/lib/types";

export default function StraddleDecayChart({ data }: { data: StraddleResponse }) {
  const chartData = [...data.decay_curve].reverse().map((p, idx) => ({
    step: idx,
    "Combined Premium": p.combined_premium,
  }));

  return (
    <div className="card h-80">
      <h3 className="mb-2 text-sm font-medium text-muted">
        ATM Straddle Premium Decay ({data.atm_straddle.strike})
      </h3>
      <ResponsiveContainer width="100%" height="90%">
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="step" stroke="#8b98a9" fontSize={11} label={{ value: "Time -> Expiry", position: "insideBottom", offset: -2, fill: "#8b98a9", fontSize: 11 }} />
          <YAxis stroke="#8b98a9" fontSize={11} />
          <Tooltip contentStyle={{ background: "#121821", border: "1px solid #1f2937" }} />
          <Line type="monotone" dataKey="Combined Premium" stroke="#f59e0b" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
