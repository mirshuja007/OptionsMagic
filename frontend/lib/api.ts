import type {
  CommentaryResponse,
  DiscoverResponse,
  ExpiriesResponse,
  Instrument,
  IntradayResponse,
  LegOut,
  LiveMarginResponse,
  MaxPainResponse,
  OiResponse,
  OptionChain,
  PcrResponse,
  StraddleResponse,
  StrategyConstraintsIn,
  VolatilityResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

function withExpiry(path: string, expiry?: string): string {
  return expiry ? `${path}?expiry=${expiry}` : path;
}

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
  expiries: (symbol: string) => getJson<ExpiriesResponse>(`/expiries/${symbol}`),
  optionChain: (symbol: string, expiry?: string) =>
    getJson<OptionChain>(withExpiry(`/option-chain/${symbol}`, expiry)),
  intraday: (symbol: string) => getJson<IntradayResponse>(`/analytics/intraday/${symbol}`),
  maxPain: (symbol: string, expiry?: string) =>
    getJson<MaxPainResponse>(withExpiry(`/analytics/max-pain/${symbol}`, expiry)),
  pcr: (symbol: string, expiry?: string) => getJson<PcrResponse>(withExpiry(`/analytics/pcr/${symbol}`, expiry)),
  oi: (symbol: string, expiry?: string) => getJson<OiResponse>(withExpiry(`/analytics/oi/${symbol}`, expiry)),
  volatility: (symbol: string, expiry?: string) =>
    getJson<VolatilityResponse>(withExpiry(`/analytics/volatility/${symbol}`, expiry)),
  straddle: (symbol: string, expiry?: string) =>
    getJson<StraddleResponse>(withExpiry(`/analytics/straddle/${symbol}`, expiry)),
  commentary: (symbol: string, expiry?: string) =>
    getJson<CommentaryResponse>(withExpiry(`/analytics/commentary/${symbol}`, expiry)),
  discoverStrategies: (symbol: string, constraints: StrategyConstraintsIn, expiry?: string) =>
    postJson<DiscoverResponse>("/strategy/discover", { symbol, constraints, expiry: expiry || undefined }),
  liveMargin: (symbol: string, legs: LegOut[]) =>
    postJson<LiveMarginResponse>("/margin/live", {
      symbol,
      legs: legs.map((l) => ({
        option_type: l.option_type,
        strike: l.strike,
        side: l.side,
        quantity_lots: l.quantity_lots,
        entry_price: l.entry_price,
        iv: l.iv,
      })),
    }),
  dataProvider: () => getJson<{ provider: "mock" | "kite" }>("/data-provider"),
};
