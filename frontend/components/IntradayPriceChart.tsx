"use client";

import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { IntradayResponse } from "@/lib/types";

function fmtTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function IntradayPriceChart({ data }: { data: IntradayResponse }) {
  const chartData = data.points.map((p) => ({
    time: fmtTime(p.timestamp),
    Spot: Math.round(p.spot * 100) / 100,
    VWAP: Math.round(p.vwap * 100) / 100,
  }));

  return (
    <div className="card h-[420px]">
      <h3 className="mb-2 text-sm font-medium text-muted">{data.symbol} — Intraday Spot Price vs. VWAP</h3>
      <ResponsiveContainer width="100%" height="90%">
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="time" stroke="#8b98a9" fontSize={11} interval="preserveStartEnd" minTickGap={40} />
          <YAxis stroke="#8b98a9" fontSize={11} domain={["auto", "auto"]} />
          <Tooltip contentStyle={{ background: "#121821", border: "1px solid #1f2937" }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="Spot" stroke="#22c55e" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="VWAP" stroke="#f59e0b" dot={false} strokeWidth={1.5} strokeDasharray="4 3" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
