// frontend/src/pages/Terminal.tsx
import { useEffect, useMemo, useState } from "react";
import CandleChart, { type Candle, type Fill } from "../components/CandleChart";
import EquityChart from "../components/EquityChart";
import SummaryCards from "../components/SummaryCards";
import TradesTable from "../components/TradesTable";
import { getCandles, getRun, getTrace, listRuns } from "../lib/api";
import { parseInsights, VERDICT_META,
         type CommitteeRole, type RunInsights, type TraceRow } from "../lib/insights";
import { inferCommitteeRole } from "../lib/eval";
import { useLang } from "../lib/i18n";

const BADGE: Record<string, string> = {
  completed: "bg-loom-green/20 text-loom-green", failed: "bg-loom-red/20 text-loom-red",
  halted: "bg-loom-amber/20 text-loom-amber", running: "bg-loom-blue/20 text-loom-blue",
};

// —— 单个委员会角色卡：展示解析后的角色 JSON 对象（dict）字段 ——
// 后端 committee_trace 是 list[dict]：策略师 {side,rationale,confidence} /
// 风控官 {veto,concern,confidence} / 主席 {side,rationale,confidence}。
// 角色标签**按元素形状推断**（inferCommitteeRole）——不按下标：消融 no_risk_officer
// 臂 trace 只有 [策略师,主席]，按下标会把主席误标成 risk officer（T4 审查遗留必修）。
// 已知字段结构化展示，其余字段回退 JSON。
function CommitteeRoleCard(
  { role, idx, trace }: { role: CommitteeRole; idx: number; trace: CommitteeRole[] },
) {
  const label = inferCommitteeRole(trace, idx);
  const side = typeof role.side === "string" ? role.side : undefined;
  const rationale = typeof role.rationale === "string" ? role.rationale : undefined;
  const concern = typeof role.concern === "string" ? role.concern : undefined;
  const confidence = typeof role.confidence === "number" ? role.confidence : undefined;
  const veto = typeof role.veto === "boolean" ? role.veto : undefined;
  const known = new Set(["side", "rationale", "concern", "confidence", "veto"]);
  const extras = Object.keys(role).filter((k) => !known.has(k));
  const structured = side !== undefined || rationale !== undefined
    || concern !== undefined || confidence !== undefined || veto !== undefined;

  return (
    <div className="text-[10px] border-l-2 border-loom-amber/50 pl-1.5 space-y-0.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="hud-label text-[9px] text-loom-amber">{label}</span>
        {side && <span className="font-mono text-slate-200">{side}</span>}
        {veto !== undefined && (
          <span className={`px-1 py-0.5 rounded text-[9px] ${veto
            ? "bg-loom-red/20 text-loom-red" : "bg-loom-green/20 text-loom-green"}`}>
            {veto ? "veto" : "no veto"}
          </span>)}
        {confidence !== undefined && (
          <span className="font-mono text-loom-green">{confidence.toFixed(2)}</span>)}
      </div>
      {rationale && <div className="text-slate-400">{rationale}</div>}
      {concern && <div className="text-slate-400">{concern}</div>}
      {(!structured || extras.length > 0) && (
        <pre className="text-[9px] font-mono text-slate-500 whitespace-pre-wrap max-h-24 overflow-auto">
          {JSON.stringify(structured ? Object.fromEntries(extras.map((k) => [k, role[k]])) : role, null, 2)}
        </pre>)}
    </div>
  );
}

// —— Agent 富信息面板：委员会轨迹 + RAG 引用 + 反思四象限 + 记忆开关 ——
// 全部从 run trace 读（getTrace(runId) 一次拉全节点），无相关节点则优雅显示"无"。
function AgentInsights({ runId }: { runId: string }) {
  const { t } = useLang();
  const [data, setData] = useState<RunInsights | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setData(null); setLoading(true);
    getTrace(runId, undefined, undefined, 2000)
      .then((rows) => { if (alive) setData(parseInsights(rows as TraceRow[])); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [runId]);

  if (loading)
    return <div className="panel p-3 text-xs text-slate-500">{t("loadingInsights")}</div>;
  if (!data || !data.hasAny)
    return <div className="panel p-3 text-xs text-slate-500">{t("noAgentData")}</div>;

  return (
    <div className="panel p-3 space-y-3">
      <div className="flex items-center gap-3">
        <div className="hud-label text-loom-violet">{t("insights")}</div>
        {/* 记忆使用指示 */}
        <span className={`px-1.5 py-0.5 rounded text-[10px] ${data.memoryUsed
          ? "bg-loom-green/20 text-loom-green" : "bg-edge/60 text-slate-500"}`}>
          {t("memory")}: {data.memoryUsed ? t("memoryOn") : t("memoryOff")}
        </span>
      </div>

      {/* RAG 引用徽章 */}
      {data.citations.length > 0 && (
        <div className="space-y-1">
          <div className="hud-label text-[10px]">{t("citations")} ({data.citations.length})</div>
          <div className="flex flex-wrap gap-1">
            {data.citations.map((c, i) => (
              <span key={i}
                    className="px-1.5 py-0.5 rounded bg-loom-violet/20 text-loom-violet text-[10px] font-mono">
                {c}
              </span>))}
          </div>
        </div>)}

      {/* 反思四象限 */}
      {data.verdicts.length > 0 && (
        <div className="space-y-1.5">
          <div className="hud-label text-[10px]">{t("verdicts")} ({data.verdicts.length})</div>
          <div className="space-y-1">
            {data.verdicts.map((v, i) => {
              const meta = VERDICT_META[v.verdict];
              const cls = meta?.cls ?? "bg-edge/60 text-slate-400";
              const label = meta ? t(meta.labelKey) : v.verdict;
              return (
                <div key={i}
                     className={`rounded px-2 py-1 text-[10px] ${meta?.signature ? "ring-1 ring-loom-amber/60" : ""}`}
                     style={{ background: "rgba(30,42,68,0.35)" }}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`px-1.5 py-0.5 rounded ${cls}`}>{label}</span>
                    <span className="text-slate-500">{t("regimeBucket")} </span>
                    <span className="font-mono text-slate-300">{v.bucket}</span>
                    <span className="text-slate-500 ml-1">{t("pnl")} </span>
                    <span className={`font-mono ${v.pnl >= 0 ? "text-loom-green" : "text-loom-red"}`}>
                      {v.pnl.toFixed(2)}
                    </span>
                  </div>
                  {v.lesson && <div className="text-slate-400 mt-0.5">{v.lesson}</div>}
                </div>);
            })}
          </div>
        </div>)}

      {/* 委员会三角色轨迹 */}
      {data.committees.map((c) => (
        <div key={c.nodeId} className="space-y-1">
          <div className="hud-label text-[10px]">
            {t("committee")} · {c.nodeId}
            {c.side && <span className="ml-2 font-mono text-loom-amber">{c.side}</span>}
            {typeof c.confidence === "number" && (
              <span className="ml-2 text-slate-500">{t("confidence")} </span>)}
            {typeof c.confidence === "number" && (
              <span className="font-mono text-loom-green">{c.confidence.toFixed(2)}</span>)}
          </div>
          {c.rationale && <div className="text-[10px] text-slate-400">{c.rationale}</div>}
          <div className="space-y-1">
            {c.trace.map((role, i) => (
              <CommitteeRoleCard key={i} role={role} idx={i} trace={c.trace} />))}
          </div>
        </div>))}
    </div>
  );
}

export default function Terminal() {
  const { t } = useLang();
  const [runs, setRuns] = useState<Record<string, any>[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [run, setRun] = useState<Record<string, any> | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);

  useEffect(() => {
    listRuns().then((rs) => {
      setRuns(rs);
      const fromHash = new URLSearchParams(location.hash.split("?")[1] ?? "").get("run");
      setSel(fromHash ?? rs[0]?.run_id ?? null);
    });
  }, []);

  useEffect(() => {
    if (!sel) return;
    getRun(sel).then((r) => {
      setRun(r);
      const p = r.params ?? {};
      if (r.report) getCandles(p.inst ?? "BTC-USDT-SWAP", p.bar ?? "1m").then(setCandles);
    });
  }, [sel]);

  const fills: Fill[] = useMemo(() => run?.report?.fills ?? [], [run]);
  const curve: [number, number][] = useMemo(() => run?.report?.equity_curve ?? [], [run]);

  if (!runs.length) return <div className="p-10 text-slate-500">{t("noRuns")}</div>;
  return (
    <div className="h-full overflow-auto p-3 space-y-3">
      <div className="flex gap-2 flex-wrap">
        {runs.map((r) => (
          <button key={r.run_id} onClick={() => setSel(r.run_id)}
                  className={`px-2 py-1 rounded text-xs font-mono border ${sel === r.run_id
                    ? "border-loom-gold text-loom-gold" : "border-edge text-slate-400"}`}>
            …{r.run_id.slice(-6)} · {r.blueprint_id}
            <span className={`ml-2 px-1.5 rounded ${BADGE[r.status] ?? ""}`}>{r.status}</span>
          </button>))}
      </div>
      {run?.status === "failed" && (
        <div className="panel p-3 border-loom-red/60 text-xs text-loom-red font-mono">
          {String(run.error)}
        </div>)}
      {run?.report && (
        <>
          <SummaryCards summary={run.report.summary ?? {}} />
          {sel && <AgentInsights runId={sel} />}
          <div className="panel p-2"><div className="hud-label mb-1">market · fills</div>
            <CandleChart candles={candles} fills={fills} /></div>
          <div className="panel p-2"><div className="hud-label mb-1">{t("equity")}</div>
            <EquityChart curve={curve} /></div>
          <div><div className="hud-label mb-1 px-1">{t("trades")} ({fills.length})</div>
            <TradesTable fills={fills} /></div>
        </>)}
    </div>
  );
}
