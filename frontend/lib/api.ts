import type {
  DiscoverResponse,
  Instrument,
  MaxPainResponse,
  OiResponse,
  OptionChain,
  PcrResponse,
  StraddleResponse,
  StrategyConstraintsIn,
  VolatilityResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Request failed: ${path} (${res.status})`);
  }
  return res.json();
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Request failed: ${path} (${res.status}) ${detail}`);
  }
  return res.json();
}

export const api = {
  instruments: () => getJson<Instrument[]>("/instruments"),
  optionChain: (symbol: string) => getJson<OptionChain>(`/option-chain/${symbol}`),
  maxPain: (symbol: string) => getJson<MaxPainResponse>(`/analytics/max-pain/${symbol}`),
  pcr: (symbol: string) => getJson<PcrResponse>(`/analytics/pcr/${symbol}`),
  oi: (symbol: string) => getJson<OiResponse>(`/analytics/oi/${symbol}`),
  volatility: (symbol: string) => getJson<VolatilityResponse>(`/analytics/volatility/${symbol}`),
  straddle: (symbol: string) => getJson<StraddleResponse>(`/analytics/straddle/${symbol}`),
  discoverStrategies: (symbol: string, constraints: StrategyConstraintsIn) =>
    postJson<DiscoverResponse>("/strategy/discover", { symbol, constraints }),
};
