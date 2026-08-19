import type { CommentaryResponse } from "@/lib/types";

function fmt(n: number, digits = 2) {
  return n.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function fmtInt(n: number) {
  return n.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export default function CommentaryBox({ data }: { data: CommentaryResponse }) {
  const { symbol, spot, prev_close, vwap, atm_iv, time_to_expiry_years, max_pain_strike, pcr_oi } = data;
  const sr = data.support_resistance;
  const band = data.expiry_band;

  const changePct = prev_close !== 0 ? ((spot - prev_close) / prev_close) * 100 : 0;
  const pcrLeansBullish = pcr_oi > 1;
  const vwapDiff = vwap != null ? spot - vwap : null;
  const vwapAt = vwapDiff != null && Math.abs(vwapDiff) < 0.01;
  const daysToExpiry = time_to_expiry_years * 365;
  const bandPctLabel = fmt(band.band_pct * 100, 1);

  return (
    <div className="card flex flex-col gap-3 text-sm leading-relaxed text-slate-300">
      <h3 className="text-sm font-medium text-muted">Expiry Outlook</h3>

      <p>
        <span className="mono font-medium text-slate-100">{symbol}</span> is trading at{" "}
        <span className="mono">{fmt(spot)}</span>,{" "}
        <span className={changePct >= 0 ? "text-accent" : "text-danger"}>
          {changePct >= 0 ? "up" : "down"} {fmt(Math.abs(changePct))}%
        </span>{" "}
        from the previous close of <span className="mono">{fmt(prev_close)}</span>. OI-based support sits at{" "}
        <span className="mono text-accent">{fmtInt(sr.support_strike)}</span> (put OI{" "}
        {fmtInt(sr.support_put_oi)}), with resistance at{" "}
        <span className="mono text-danger">{fmtInt(sr.resistance_strike)}</span> (call OI{" "}
        {fmtInt(sr.resistance_call_oi)}).
      </p>

      <p>
        {data.oi_change_available ? (
          <>
            Smart OI flow reads <span className="font-medium">{data.smart_oi.bias}</span> (score{" "}
            {fmt(data.smart_oi.score, 2)}), and the OI-weighted PCR of <span className="mono">{fmt(pcr_oi)}</span>{" "}
            leans {pcrLeansBullish ? "bullish (more put OI than call OI)" : "bearish (more call OI than put OI)"}.
          </>
        ) : (
          <>
            OI-change-based signals (Smart OI, buildup) aren&apos;t available on the live feed — PCR (OI) alone
            reads <span className="mono">{fmt(pcr_oi)}</span>, leaning{" "}
            {pcrLeansBullish ? "bullish" : "bearish"}.
          </>
        )}
      </p>

      <p>
        {vwapDiff == null ? (
          "VWAP isn't available yet for today's session."
        ) : vwapAt ? (
          <>
            Spot is trading right at the session VWAP of <span className="mono">{fmt(vwap as number)}</span>.
          </>
        ) : (
          <>
            Spot is trading {vwapDiff > 0 ? "above" : "below"} the session VWAP of{" "}
            <span className="mono">{fmt(vwap as number)}</span> by{" "}
            <span className="mono">{fmt(Math.abs(vwapDiff))}</span> points, a mildly{" "}
            {vwapDiff > 0 ? "bullish" : "bearish"} intraday signal.
          </>
        )}
      </p>

      <p>
        Max Pain stands at <span className="mono">{fmtInt(max_pain_strike)}</span> — option writers are
        collectively positioned for the index to settle near this level into expiry.
      </p>

      <p className="text-muted">
        Based on an ATM IV of <span className="mono">{fmt(atm_iv * 100, 1)}%</span> and{" "}
        <span className="mono">{fmt(daysToExpiry, 1)}</span> days to expiry, the model-estimated probability of{" "}
        {symbol} settling within +{bandPctLabel}%/-{bandPctLabel}% of spot ({fmt(band.lower)} – {fmt(band.upper)})
        at expiry is{" "}
        {band.probability == null ? (
          "not defined (no time value remaining)"
        ) : (
          <span className="mono font-medium text-slate-100">{fmt(band.probability * 100, 1)}%</span>
        )}
        .
      </p>
    </div>
  );
}
