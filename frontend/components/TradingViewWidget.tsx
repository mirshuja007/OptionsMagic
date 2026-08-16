"use client";

import { useEffect, useRef } from "react";

// Uses TradingView's free embeddable widget (no API key required) for
// underlying technical analysis, per the platform's UI/UX spec.
//
// `exchange` should match the instrument's real exchange (NSE/BSE/MCX) so
// the widget resolves the right symbol. Note MCX commodities in particular
// may need TradingView's continuous-contract ticker convention (e.g.
// "MCX:CRUDEOIL1!" rather than "MCX:CRUDEOIL") — verify in-browser and
// adjust if the widget shows "symbol not found".
export default function TradingViewWidget({ symbol, exchange = "NSE" }: { symbol: string; exchange?: string }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;
    container.current.innerHTML = "";

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-symbol-overview.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      symbols: [[`${exchange}:${symbol}`]],
      chartOnly: false,
      width: "100%",
      height: 400,
      locale: "in",
      colorTheme: "dark",
      autosize: true,
      showVolume: true,
    });
    container.current.appendChild(script);
  }, [symbol, exchange]);

  return (
    <div className="card h-[420px] overflow-hidden">
      <div ref={container} className="tradingview-widget-container h-full w-full" />
    </div>
  );
}
