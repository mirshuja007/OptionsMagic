"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IntradayResponse, MaxPainResponse, OiResponse, OptionChain, PcrResponse, StraddleResponse, VolatilityResponse } from "@/lib/types";
import SymbolSelector from "@/components/SymbolSelector";
import StatTile from "@/components/StatTile";
import OptionChainTable from "@/components/OptionChainTable";
import OiChart from "@/components/OiChart";
import IvSkewChart from "@/components/IvSkewChart";
import StraddleDecayChart from "@/components/StraddleDecayChart";
import IntradayPriceChart from "@/components/IntradayPriceChart";

export default function ResearchPage() {
  const [symbol, setSymbol] = useState("NIFTY");
  const [chain, setChain] = useState<OptionChain | null>(null);
  const [intraday, setIntraday] = useState<IntradayResponse | null>(null);
  const [maxPain, setMaxPain] = useState<MaxPainResponse | null>(null);
  const [pcr, setPcr] = useState<PcrResponse | null>(null);
  const [oi, setOi] = useState<OiResponse | null>(null);
  const [vol, setVol] = useState<VolatilityResponse | null>(null);
  const [straddle, setStraddle] = useState<StraddleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    Promise.all([
      api.optionChain(symbol),
      api.intraday(symbol),
      api.maxPain(symbol),
      api.pcr(symbol),
      api.oi(symbol),
      api.volatility(symbol),
      api.straddle(symbol),
    ])
      .then(([c, i, mp, p, o, v, s]) => {
        setChain(c);
        setIntraday(i);
        setMaxPain(mp);
        setPcr(p);
        setOi(o);
        setVol(v);
        setStraddle(s);
      })
      .catch((e) => setError(String(e)));
  }, [symbol]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Research Mode</h1>
        <SymbolSelector value={symbol} onChange={setSymbol} />
      </div>

      {error && (
        <div className="card border-danger/40 text-sm text-danger">
          Couldn&apos;t reach the backend at NEXT_PUBLIC_API_BASE_URL: {error}
        </div>
      )}

      {intraday && <IntradayPriceChart data={intraday} />}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatTile label="Spot" value={chain ? chain.spot.toFixed(2) : "—"} />
        <StatTile label="Max Pain" value={maxPain ? maxPain.max_pain_strike.toFixed(0) : "—"} />
        <StatTile
          label="PCR (OI)"
          value={pcr ? pcr.pcr_oi.toFixed(2) : "—"}
          tone={pcr ? (pcr.pcr_oi > 1 ? "positive" : "negative") : "neutral"}
        />
        <StatTile label="ATM IV" value={vol ? `${(vol.atm_iv * 100).toFixed(1)}%` : "—"} />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <StatTile
          label="Smart OI Bias"
          value={oi ? oi.smart_oi.bias : "—"}
          tone={oi ? (oi.smart_oi.bias === "bullish" ? "positive" : oi.smart_oi.bias === "bearish" ? "negative" : "neutral") : "neutral"}
          sub={oi ? `score ${oi.smart_oi.score.toFixed(2)}` : undefined}
        />
        <StatTile
          label="Gamma Exposure (GEX)"
          value={oi ? oi.gamma_exposure.regime.replace("_", " ") : "—"}
          sub={oi ? `net ${(oi.gamma_exposure.net_gex / 1e7).toFixed(2)} Cr` : undefined}
        />
        <StatTile label="Volatility Skew (25d)" value={vol ? vol.skew.skew.toFixed(4) : "—"} />
      </div>

      {oi && <OiChart data={oi.by_strike} />}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {vol && <IvSkewChart data={vol} />}
        {straddle && <StraddleDecayChart data={straddle} />}
      </div>

      {chain && <OptionChainTable chain={chain} />}
    </div>
  );
}
