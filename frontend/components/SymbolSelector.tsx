"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Instrument } from "@/lib/types";

export default function SymbolSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (symbol: string) => void;
}) {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .instruments()
      .then(setInstruments)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return <div className="text-sm text-danger">Failed to load instruments: {error}</div>;
  }

  const indices = instruments.filter((i) => i.asset_class === "index");
  const stocks = instruments.filter((i) => i.asset_class === "equity");
  const commodities = instruments.filter((i) => i.asset_class === "commodity");

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-border bg-panel px-3 py-2 text-sm"
    >
      <optgroup label="Indices">
        {indices.map((i) => (
          <option key={i.symbol} value={i.symbol}>
            {i.display_name}
          </option>
        ))}
      </optgroup>
      <optgroup label="Commodities (MCX)">
        {commodities.map((i) => (
          <option key={i.symbol} value={i.symbol}>
            {i.display_name}
          </option>
        ))}
      </optgroup>
      <optgroup label="F&O Stocks">
        {stocks.map((i) => (
          <option key={i.symbol} value={i.symbol}>
            {i.display_name}
          </option>
        ))}
      </optgroup>
    </select>
  );
}
