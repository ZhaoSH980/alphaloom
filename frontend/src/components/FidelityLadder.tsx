// frontend/src/components/FidelityLadder.tsx —— 保真度阶梯 L0–L3 净利柱图 + 乐观差距高亮
// 后端 LadderReport.to_dict：{ levels:[{level,net_pnl,max_dd,num_trades,profit_factor}],
// optimism_gap: L0_pnl - L3_pnl }。诚实：负净利如实向下画，不美化。
import type { LadderReport } from "../lib/eval";
import { useLang } from "../lib/i18n";

const LEVEL_DESC: Record<string, string> = {
  L0: "naive close (most optimistic)",
  L1: "next-bar open (PaperBroker)",
  L2: "intrabar path proxy",
  L3: "fees + slippage (harshest)",
};

export default function FidelityLadder({ report }: { report: LadderReport }) {
  const { t } = useLang();
  const levels = report.levels ?? [];
  if (!levels.length) return <div className="text-xs text-slate-500">{t("noData")}</div>;

  const vals = levels.map((l) => l.net_pnl);
  const max = Math.max(0, ...vals);
  const min = Math.min(0, ...vals);
  const span = max - min || 1;
  // 柱区高度 120px，零线按 span 定位。
  const H = 120;
  const zeroY = (max / span) * H;
  const barH = (v: number) => Math.abs(v) / span * H;
  const barTop = (v: number) => (v >= 0 ? zeroY - barH(v) : zeroY);
  const fmt = (n: number) => (Number.isFinite(n) ? +n.toFixed(2) : "—");

  return (
    <div className="panel hud-frame p-3 space-y-2">
      <div className="flex items-baseline gap-3 flex-wrap">
        <div className="hud-label text-loom-blue">{t("fidelityTitle")}</div>
        <span className="text-[10px] text-slate-500">{t("fidelityHint")}</span>
      </div>

      {/* 乐观差距条：L0 − L3，越大回测越乐观 */}
      <div className="flex items-center gap-2">
        <span className="hud-label text-[9px]">{t("optimismGap")}</span>
        <span className="font-mono text-sm text-loom-amber">{fmt(report.optimism_gap)}</span>
        <span className="text-[10px] text-slate-600">{t("optimismGapHint")}</span>
      </div>

      {/* 柱图（纯 div，无依赖）——净利正绿负红，L0 高亮金框（最乐观的谎言起点） */}
      <div className="grid grid-cols-4 gap-3 pt-1">
        {levels.map((l) => {
          const pos = l.net_pnl >= 0;
          return (
            <div key={l.level} className="flex flex-col items-center gap-1">
              <div className="relative w-full" style={{ height: H }}>
                {/* 零线 */}
                <div className="absolute left-0 right-0 border-t border-edge"
                     style={{ top: zeroY }} />
                <div
                  className={`absolute left-1/4 right-1/4 rounded-sm ${l.level === "L0"
                    ? "ring-1 ring-loom-gold " : ""}${pos ? "bg-loom-green/70" : "bg-loom-red/70"}`}
                  style={{ top: barTop(l.net_pnl), height: Math.max(2, barH(l.net_pnl)) }}
                  title={`${l.level} net_pnl ${fmt(l.net_pnl)}`}
                />
              </div>
              <div className={`font-mono text-xs ${pos ? "text-loom-green" : "text-loom-red"}`}>
                {fmt(l.net_pnl)}
              </div>
              <div className="hud-label text-[10px] text-loom-blue">{l.level}</div>
              <div className="text-[9px] text-slate-600 text-center leading-tight">
                {LEVEL_DESC[l.level] ?? ""}
              </div>
              <div className="text-[9px] text-slate-500 font-mono">
                dd {(l.max_dd * 100).toFixed(1)}% · {l.num_trades}t
                {l.profit_factor != null && ` · pf ${l.profit_factor}`}
              </div>
            </div>);
        })}
      </div>
    </div>
  );
}
