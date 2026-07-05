// frontend/src/pages/Terminal.tsx
import { useEffect, useMemo, useState } from "react";
import CandleChart, { type Candle, type Fill } from "../components/CandleChart";
import EquityChart from "../components/EquityChart";
import SummaryCards from "../components/SummaryCards";
import TradesTable from "../components/TradesTable";
import { getCandles, getRun, listRuns } from "../lib/api";
import { useLang } from "../lib/i18n";

const BADGE: Record<string, string> = {
  completed: "bg-loom-green/20 text-loom-green", failed: "bg-loom-red/20 text-loom-red",
  halted: "bg-loom-amber/20 text-loom-amber", running: "bg-loom-blue/20 text-loom-blue",
};

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
