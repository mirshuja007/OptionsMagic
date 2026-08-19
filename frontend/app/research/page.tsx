"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IntradayResponse, MaxPainResponse, OiResponse, OptionChain, PcrResponse, StraddleResponse, VolatilityResponse } from "@/lib/types";
import SymbolSelector from "@/components/SymbolSelector";
import ExpirySelector from "@/components/ExpirySelector";
import StatTile from "@/components/StatTile";
import OptionChainTable from "@/components/OptionChainTable";
import OiChart from "@/components/OiChart";
import IvSkewChart from "@/components/IvSkewChart";
import StraddleDecayChart from "@/components/StraddleDecayChart";
import IntradayPriceChart from "@/components/IntradayPriceChart";

export default function ResearchPage() {
  const [symbol, setSymbol] = useState("NIFTY");
  const [expiry, setExpiry] = useState("");
  const [chain, setChain] = useState<OptionChain | null>(null);
  const [intraday, setIntraday] = useState<IntradayResponse | null>(null);
  const [maxPain, setMaxPain] = useState<MaxPainResponse | null>(null);
  const [pcr, setPcr] = useState<PcrResponse | null>(null);
  const [oi, setOi] = useState<OiResponse | null>(null);
  const [vol, setVol] = useState<VolatilityResponse | null>(null);
  const [straddle, setStraddle] = useState<StraddleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function handleSymbolChange(next: string) {
    setSymbol(next);
    setExpiry(""); // ExpirySelector will populate this with the new symbol's nearest expiry
  }

  useEffect(() => {
    if (!expiry) return; // wait for ExpirySelector to resolve a default for this symbol
    setError(null);
    Promise.allSettled([
      api.optionChain(symbol, expiry),
      api.intraday(symbol),
      api.maxPain(symbol, expiry),
      api.pcr(symbol, expiry),
      api.oi(symbol, expiry),
      api.volatility(symbol, expiry),
      api.straddle(symbol, expiry),
    ]).then(([c, i, mp, p, o, v, s]) => {
      setChain(c.status === "fulfilled" ? c.value : null);
      setIntraday(i.status === "fulfilled" ? i.value : null);
      setMaxPain(mp.status === "fulfilled" ? mp.value : null);
      setPcr(p.status === "fulfilled" ? p.value : null);
      setOi(o.status === "fulfilled" ? o.value : null);
      setVol(v.status === "fulfilled" ? v.value : null);
      setStraddle(s.status === "fulfilled" ? s.value : null);
      const errors = [c, i, mp, p, o, v, s]
        .filter((r): r is PromiseRejectedResult => r.status === "rejected")
        .map((r) => String(r.reason));
      setError(errors.length > 0 ? errors.join("; ") : null);
    });
  }, [symbol, expiry]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Research Mode</h1>
        <div className="flex items-center gap-3">
          <ExpirySelector symbol={symbol} value={expiry} onChange={setExpiry} />
          <SymbolSelector value={symbol} onChange={handleSymbolChange} />
        </div>
      </div>

      {error && (
        <div className="card border-danger/40 text-sm text-danger">
          Some data couldn&apos;t be loaded from NEXT_PUBLIC_API_BASE_URL: {error}
        </div>
      )}

      {intraday && <IntradayPriceChart data={intraday} />}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <StatTile label="Spot" value={chain ? chain.spot.toFixed(2) : "—"} />
        <StatTile
          label="Expiry"
          value={
            chain
              ? new Date(chain.expiry).toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short" })
              : "—"
          }
          sub={chain ? chain.expiry : undefined}
        />
        <StatTile label="Max Pain" value={maxPain ? maxPain.max_pain_strike.toFixed(0) : "—"} />
        <StatTile
          label="PCR (OI)"
          value={pcr ? pcr.pcr_oi.toFixed(2) : "—"}
          tone={pcr ? (pcr.pcr_oi > 1 ? "positive" : "negative") : "neutral"}
        />
        <StatTile label="ATM IV" value={vol ? `${(vol.atm_iv * 100).toFixed(1)}%` : "—"} />
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatTile
          label="Smart OI Bias"
          value={oi ? (oi.oi_change_available ? oi.smart_oi.bias : "n/a") : "—"}
          tone={
            oi && oi.oi_change_available
              ? oi.smart_oi.bias === "bullish"
                ? "positive"
                : oi.smart_oi.bias === "bearish"
                  ? "negative"
                  : "neutral"
              : "neutral"
          }
          sub={
            oi
              ? oi.oi_change_available
                ? `score ${oi.smart_oi.score.toFixed(2)}`
                : "OI-change unavailable on live feed"
              : undefined
          }
        />
        <StatTile
          label="Gamma Exposure (GEX)"
          value={oi ? oi.gamma_exposure.regime.replace("_", " ") : "—"}
          sub={oi ? `net ${(oi.gamma_exposure.net_gex / 1e7).toFixed(2)} Cr` : undefined}
        />
        <StatTile label="Volatility Skew (25d)" value={vol ? vol.skew.skew.toFixed(4) : "—"} />
        {(() => {
          const last = intraday && intraday.points.length > 0 ? intraday.points[intraday.points.length - 1] : null;
          const diff = last ? last.spot - last.vwap : 0;
          const atVwap = last ? Math.abs(diff) < 0.01 : false;
          return (
            <StatTile
              label="VWAP"
              value={last ? last.vwap.toFixed(2) : "—"}
              tone={last ? (atVwap ? "neutral" : diff > 0 ? "positive" : "negative") : "neutral"}
              sub={last ? (atVwap ? "spot at VWAP" : `spot ${diff > 0 ? "above" : "below"}`) : undefined}
            />
          );
        })()}
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
