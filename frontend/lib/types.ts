export interface Greeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
}

export interface LegQuote {
  ltp: number;
  bid: number;
  ask: number;
  oi: number;
  oi_change: number;
  volume: number;
  iv: number;
  greeks: Greeks;
}

export interface ChainRow {
  strike: number;
  call: LegQuote;
  put: LegQuote;
}

export interface OptionChain {
  symbol: string;
  spot: number;
  expiry: string;
  timestamp: string;
  time_to_expiry_years: number;
  risk_free_rate: number;
  rows: ChainRow[];
}

export interface Instrument {
  symbol: string;
  display_name: string;
  is_index: boolean;
  lot_size: number;
  strike_step: number;
  exchange: string;
}

export interface MaxPainResponse {
  symbol: string;
  spot: number;
  max_pain_strike: number;
  payout_by_strike: Record<string, number>;
}

export interface PcrResponse {
  symbol: string;
  pcr_oi: number;
  pcr_volume: number;
  total_call_oi: number;
  total_put_oi: number;
  total_call_volume: number;
  total_put_volume: number;
}

export interface StrikeOi {
  strike: number;
  call_oi: number;
  put_oi: number;
  call_oi_change: number;
  put_oi_change: number;
  call_buildup: string;
  put_buildup: string;
}

export interface OiResponse {
  symbol: string;
  by_strike: StrikeOi[];
  smart_oi: { score: number; bias: string };
  gamma_exposure: {
    net_gex: number;
    call_gex: number;
    put_gex: number;
    regime: string;
    by_strike: Record<string, number>;
  };
}

export interface VolatilityResponse {
  symbol: string;
  atm_iv: number;
  skew: {
    skew: number;
    put_strike?: number;
    put_iv?: number;
    call_strike?: number;
    call_iv?: number;
  };
  grid: { strike: number; call_iv: number; put_iv: number }[];
}

export interface StraddlePoint {
  strike: number;
  call_premium: number;
  put_premium: number;
  combined_premium: number;
}

export interface StraddleResponse {
  symbol: string;
  atm_straddle: StraddlePoint;
  multistrike: StraddlePoint[];
  decay_curve: { time_to_expiry_years: number; combined_premium: number }[];
}

export interface LegOut {
  option_type: "CE" | "PE";
  strike: number;
  side: "long" | "short";
  quantity_lots: number;
  entry_price: number;
  iv: number;
}

export interface StrategyConstraintsIn {
  min_probability_of_profit: number;
  min_yield_pct: number;
  max_profit_cap: number;
  max_loss_cap: number;
  margin_cap: number;
}

export interface StrategyResult {
  strategy_type: string;
  legs: LegOut[];
  margin: {
    span_margin: number;
    exposure_margin: number;
    total_margin: number;
    net_entry_credit: number;
  };
  payoff: {
    max_profit: number;
    max_loss: number;
    unlimited_upside_risk: boolean;
    unlimited_downside_risk: boolean;
  };
  probability_of_profit: number;
  expected_value: number;
  sharpe: number;
  yield_pct: number;
}

export interface DiscoverResponse {
  symbol: string;
  spot: number;
  expiry: string;
  results: StrategyResult[];
}
