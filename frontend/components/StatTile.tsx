export default function StatTile({
  label,
  value,
  tone = "neutral",
  sub,
}: {
  label: string;
  value: string;
  tone?: "neutral" | "positive" | "negative";
  sub?: string;
}) {
  const toneClass =
    tone === "positive" ? "text-accent" : tone === "negative" ? "text-danger" : "text-slate-100";

  return (
    <div className="card flex flex-col gap-1">
      <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
      <span className={`mono text-2xl font-semibold ${toneClass}`}>{value}</span>
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </div>
  );
}
