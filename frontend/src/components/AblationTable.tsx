// frontend/src/components/AblationTable.tsx —— 委员会消融三臂对比 + 护栏价值
// 后端 AblationReport.to_dict：{arms:[{arm,run_id,bars,summary{},num_vetoes,num_trades,
// verdict_counts{}}], guardrail_value: {net_pnl_delta, guardrail_helped, ...} | null}。
// 诚实：guardrail_helped 正负如实（负=护栏帮倒忙，红色 delta 原样展示，别藏）。
import type { AblationReport, ArmResult } from "../lib/eval";
import { useLang } from "../lib/i18n";

const num = (v: unknown, d = 2) =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(d) : "—";

const ARM_LABEL: Record<string, string> = {
  full: "full committee", no_risk_officer: "− risk officer", no_rag: "− RAG",
};

export default function AblationTable({ report }: { report: AblationReport }) {
  const { t } = useLang();
  const arms = report.arms ?? [];
  if (!arms.length) return <div className="text-xs text-slate-500">{t("noData")}</div>;
  const gv = report.guardrail_value;

  return (
    <div className="panel p-3 space-y-3">
      <div className="flex items-baseline gap-3 flex-wrap">
        <div className="hud-label text-loom-red">{t("ablationTitle")}</div>
        <span className="text-[10px] text-slate-500">{t("ablationHint")}</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-slate-500 text-[10px] uppercase tracking-wider">
              <th className="text-left py-1 pr-2">{t("arm")}</th>
              <th className="text-right py-1 px-2">net pnl</th>
              <th className="text-right py-1 px-2">return %</th>
              <th className="text-right py-1 px-2">max dd</th>
              <th className="text-right py-1 px-2">trades</th>
              <th className="text-right py-1 pl-2">{t("vetoes")}</th>
            </tr>
          </thead>
          <tbody>
            {arms.map((a: ArmResult) => {
              const s = a.summary ?? {};
              const pnl = s.net_pnl as number | undefined;
              const isFull = a.arm === "full";
              return (
                <tr key={a.arm}
                    className={`border-t border-edge/60 ${isFull ? "bg-loom-green/5" : ""}`}>
                  <td className="py-1 pr-2 text-slate-300">{ARM_LABEL[a.arm] ?? a.arm}</td>
                  <td className={`py-1 px-2 text-right ${typeof pnl === "number"
                    ? (pnl >= 0 ? "text-loom-green" : "text-loom-red") : "text-slate-500"}`}>
                    {num(pnl)}
                  </td>
                  <td className="py-1 px-2 text-right text-slate-400">{num(s.return_pct)}</td>
                  <td className="py-1 px-2 text-right text-slate-400">
                    {typeof s.max_drawdown === "number" ? `${(s.max_drawdown * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="py-1 px-2 text-right text-slate-400">{a.num_trades}</td>
                  <td className="py-1 pl-2 text-right text-loom-amber">{a.num_vetoes}</td>
                </tr>);
            })}
          </tbody>
        </table>
      </div>

      {/* 护栏价值：full − no_risk_officer 的净利差。正负如实（负 = 护栏帮倒忙）。 */}
      {gv ? (
        <div className="panel p-2 bg-void/40 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="hud-label text-[9px]">{t("guardrailValue")}</span>
            <span className={`font-mono text-sm ${gv.net_pnl_delta >= 0
              ? "text-loom-green" : "text-loom-red"}`}>
              {num(gv.net_pnl_delta)}
            </span>
            <span className={`px-1.5 py-0.5 rounded text-[10px] ${gv.guardrail_helped
              ? "bg-loom-green/20 text-loom-green" : "bg-loom-red/20 text-loom-red"}`}>
              {gv.guardrail_helped ? t("guardrailHelped") : t("guardrailHurt")}
            </span>
          </div>
          <div className="text-[9px] text-slate-500 font-mono">
            full {num(gv.net_pnl_full)} vs −risk {num(gv.net_pnl_no_risk_officer)}
            {" · "}return Δ {num(gv.return_pct_delta)} · dd Δ {num(gv.max_drawdown_delta, 4)}
            {" · "}vetoes {gv.num_vetoes_full}
          </div>
          <div className="text-[9px] text-slate-600 leading-tight">{gv.note}</div>
        </div>
      ) : (
        <div className="text-[10px] text-slate-500">
          {t("guardrailValue")}: {t("noData")} (missing full / no_risk_officer arm)
        </div>)}
    </div>
  );
}
