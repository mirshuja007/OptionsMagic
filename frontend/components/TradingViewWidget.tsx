"use client";

import { useEffect, useRef } from "react";

// Uses TradingView's free embeddable "Advanced Chart" widget (no API key
// required) for underlying technical analysis, per the platform's UI/UX
// spec. The simpler "Symbol Overview" widget was tried first but returned
// "This symbol is only available on TradingView" for NSE:NIFTY — that
// widget product appears to carry a narrower real-time-data license than
// TradingView's main site or this Advanced Chart embed.
//
// `exchange` should match the instrument's real exchange (NSE/BSE/MCX) so
// the widget resolves the right symbol. Note MCX commodities in particular
// may need TradingView's continuous-contract ticker convention (e.g.
// "MCX:CRUDEOIL1!" rather than "MCX:CRUDEOIL") — verify in-browser and
// adjust if the chart shows "symbol not found". Even the Advanced Chart
// widget may not carry every NSE/MCX real-time symbol (Indian exchange
// data redistribution is tightly licensed) — if a given symbol still
// doesn't render, that's a TradingView data-availability limit, not a bug
// in this app; the option-chain analytics below are unaffected either way.
export default function TradingViewWidget({ symbol, exchange = "NSE" }: { symbol: string; exchange?: string }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;
    container.current.innerHTML = "";

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: `${exchange}:${symbol}`,
      interval: "D",
      timezone: "Asia/Kolkata",
      theme: "dark",
      style: "1",
      locale: "in",
      allow_symbol_change: false,
      support_host: "https://www.tradingview.com",
    });
    container.current.appendChild(script);
  }, [symbol, exchange]);

  return (
    <div className="flex flex-col gap-1">
      <div className="card h-[420px] overflow-hidden">
        <div ref={container} className="tradingview-widget-container h-full w-full" />
      </div>
      <p className="text-center text-[11px] text-muted">
        Chart via TradingView — may not render for every symbol depending on their data licensing.
      </p>
    </div>
  );
}
