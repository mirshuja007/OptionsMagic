"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { DiscoverResponse } from "@/lib/types";
import StrategyForm, { type StrategyFormValues } from "@/components/StrategyForm";
import StrategyResults from "@/components/StrategyResults";

export default function StrategyPage() {
  const [response, setResponse] = useState<DiscoverResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(values: StrategyFormValues) {
    setLoading(true);
    setError(null);
    api
      .discoverStrategies(values.symbol, values.constraints, values.expiry)
      .then(setResponse)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Strategy Command Mode</h1>
        <p className="text-sm text-muted">
          The Constraint-Solving Strategy Discovery Engine sweeps the live option chain for credit spreads,
          iron condors, iron flies, and ratio spreads that satisfy your risk/capital/probability boundaries,
          and ranks the survivors by expected value.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[360px_1fr]">
        <StrategyForm onSubmit={handleSubmit} loading={loading} />

        <div className="flex flex-col gap-4">
          {error && <div className="card border-danger/40 text-sm text-danger">{error}</div>}
          {response && (
            <div className="text-sm text-muted">
              {response.symbol} · Spot {response.spot.toFixed(2)} · Expiry {response.expiry} ·{" "}
              {response.results.length} strategies matched
            </div>
          )}
          {response ? (
            <StrategyResults symbol={response.symbol} results={response.results} />
          ) : (
            !loading && <div className="card text-sm text-muted">Set your constraints and run the solver.</div>
          )}
        </div>
      </div>
    </div>
  );
}
