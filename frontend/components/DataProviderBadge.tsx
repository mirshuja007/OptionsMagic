"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function DataProviderBadge() {
  const [provider, setProvider] = useState<"mock" | "kite" | "unreachable" | null>(null);

  useEffect(() => {
    api
      .dataProvider()
      .then((r) => setProvider(r.provider))
      .catch(() => setProvider("unreachable"));
  }, []);

  if (provider === null) return null;

  if (provider === "unreachable") {
    return (
      <span className="rounded bg-danger/20 px-2 py-0.5 text-xs font-medium text-danger" title="Could not reach the backend">
        Backend unreachable
      </span>
    );
  }

  if (provider === "kite") {
    return (
      <span
        className="rounded bg-accent/20 px-2 py-0.5 text-xs font-medium text-accent"
        title="Live data via Zerodha Kite Connect"
      >
        Live (Kite)
      </span>
    );
  }

  return (
    <span
      className="rounded bg-warn/20 px-2 py-0.5 text-xs font-medium text-warn"
      title="Simulated option chains and prices — not real market data. Set MARKET_DATA_PROVIDER=kite for live data."
    >
      Simulated Data
    </span>
  );
}
