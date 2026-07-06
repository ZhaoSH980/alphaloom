// frontend/src/components/ScorecardPanel.tsx —— 蓝图记分卡：综合分大字 + 四维分项 +
// 证据覆盖（缺证据如实标注）+ 权重可见（点开看批判 notes）。
// 后端 Scorecard.to_dict：{composite, components{4}, weights{4}, evidence_coverage{..+ratio},
// generalization_gap, in_sample_only, ..., notes[]}。诚实：缺证据/低分不美化。
import { useState } from "react";
import type { Scorecard } from "../lib/eval";
import { useLang } from "../lib/i18n";

const COMP_LABELS: Record<string, string> = {
  valid_performance: "valid perf", generalization: "generalization",
  fidelity: "fidelity", determinism: "determinism",
};
// evidence_coverage 里除 "ratio" 外的布尔证据维度。
const EVIDENCE_LABELS: Record<string, string> = {
  valid_window: "valid window", fidelity_ladder: "fidelity ladder",
  cost_certificate: "cost cert", ablation: "ablation", trading_activity: "trading",
};

function scoreColor(v: number): string {
  if (v >= 66) return "text-loom-green";
  if (v >= 40) return "text-loom-amber";
  return "text-loom-red";
}

export default function ScorecardPanel({ card }: { card: Scorecard }) {
  const { t } = useLang();
  const [showCritique, setShowCritique] = useState(false);
  const comp = card.composite ?? 0;

  return (
    <div className="panel hud-frame p-3 space-y-3">
      <div className="hud-label text-loom-violet">{t("scorecardTitle")}</div>

      <div className="flex items-center gap-4 flex-wrap">
        {/* 综合分大字 */}
        <div>
          <div className="hud-label text-[9px]">{t("composite")}</div>
          <div className={`font-mono text-4xl leading-none ${scoreColor(comp)}`}>
            {comp.toFixed(1)}
          </div>
        </div>
        {card.in_sample_only && (
          <span className="px-1.5 py-0.5 rounded bg-loom-amber/20 text-loom-amber text-[10px]">
            {t("inSampleOnly")}
          </span>)}
        {typeof card.generalization_gap === "number" && (
          <div className="text-[10px]">
            <span className="text-slate-500">{t("generalizationGap")} </span>
            <span className={`font-mono ${card.generalization_gap > 0 ? "text-loom-red" : "text-loom-green"}`}>
              {card.generalization_gap.toFixed(2)}
            </span>
          </div>)}
      </div>

      {/* 四维分项 + 权重 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {Object.entries(card.components ?? {}).map(([k, v]) => (
          <div key={k} className="panel p-2 bg-void/40">
            <div className="hud-label text-[9px]">{COMP_LABELS[k] ?? k}</div>
            <div className={`font-mono text-lg ${scoreColor(v)}`}>{v.toFixed(1)}</div>
            {typeof card.weights?.[k] === "number" && (
              <div className="text-[9px] text-slate-600">
                {t("weights")} {(card.weights[k] * 100).toFixed(0)}%
              </div>)}
          </div>))}
      </div>

      {/* 证据覆盖：缺证据如实显示（红叉），别假装满分 */}
      <div className="space-y-1">
        <div className="hud-label text-[9px]">
          {t("evidenceCoverage")}
          {typeof card.evidence_coverage?.ratio === "number" && (
            <span className="ml-2 font-mono text-slate-400">
              {(Number(card.evidence_coverage.ratio) * 100).toFixed(0)}%
            </span>)}
        </div>
        <div className="flex flex-wrap gap-1">
          {Object.entries(card.evidence_coverage ?? {})
            .filter(([k]) => k !== "ratio")
            .map(([k, present]) => (
              <span key={k}
                    className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${present
                      ? "bg-loom-green/15 text-loom-green" : "bg-loom-red/15 text-loom-red"}`}>
                {present ? "✓" : "✗"} {EVIDENCE_LABELS[k] ?? k}
              </span>))}
        </div>
      </div>

      {/* 点开看批判：notes 如实列缺证据/局限 */}
      {(card.notes?.length ?? 0) > 0 && (
        <div>
          <button onClick={() => setShowCritique((s) => !s)}
                  className="text-[10px] text-loom-blue hover:underline">
            {showCritique ? t("hideCritique") : t("showCritique")} ({card.notes.length})
          </button>
          {showCritique && (
            <ul className="mt-1 space-y-0.5 text-[10px] text-slate-400 list-disc list-inside">
              {card.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>)}
        </div>)}
    </div>
  );
}
