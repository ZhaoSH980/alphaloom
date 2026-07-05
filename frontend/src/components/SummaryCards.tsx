// frontend/src/components/SummaryCards.tsx
const FIELDS: [string, string][] = [
  ["net_pnl", "Net PnL"], ["return_pct", "Return %"], ["max_drawdown", "Max DD"],
  ["num_trades", "Trades"], ["win_rate", "Win rate"], ["profit_factor", "Profit factor"],
];
export default function SummaryCards({ summary }: { summary: Record<string, unknown> }) {
  return (
    <div className="grid grid-cols-3 lg:grid-cols-6 gap-2">
      {FIELDS.map(([k, label]) => {
        const v = summary[k];
        const num = typeof v === "number" ? v : null;
        const accent = k === "net_pnl" && num != null
          ? num >= 0 ? "text-loom-green" : "text-loom-red" : "text-slate-200";
        return (
          <div key={k} className="panel p-2">
            <div className="hud-label">{label}</div>
            <div className={`font-mono text-sm ${accent}`}>
              {v == null ? "—" : typeof v === "number" ? +v.toFixed(4) : String(v)}
            </div>
          </div>);
      })}
    </div>
  );
}
