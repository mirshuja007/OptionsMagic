"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

function fmtExpiry(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short" });
}

export default function ExpirySelector({
  symbol,
  value,
  onChange,
}: {
  symbol: string;
  value: string;
  onChange: (expiry: string) => void;
}) {
  const [expiries, setExpiries] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api
      .expiries(symbol)
      .then((res) => setExpiries(res.expiries))
      .catch((e) => setError(String(e)));
    // Re-fetch whenever the symbol changes; `value`/`onChange` intentionally
    // excluded so this doesn't re-run on every selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  useEffect(() => {
    if (expiries.length > 0 && !expiries.includes(value)) {
      onChange(expiries[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiries]);

  if (error) {
    return <div className="text-sm text-danger">Failed to load expiries: {error}</div>;
  }

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-border bg-panel px-3 py-2 text-sm"
    >
      {expiries.map((e) => (
        <option key={e} value={e}>
          {fmtExpiry(e)}
        </option>
      ))}
    </select>
  );
}
