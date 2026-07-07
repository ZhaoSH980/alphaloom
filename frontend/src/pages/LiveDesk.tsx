import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import CandleChart, { type Candle, type Fill } from "../components/CandleChart";
import EquityChart from "../components/EquityChart";
import NodeCard from "../components/NodeCard";
import {
  compileLoom,
  getBlueprint,
  getCandles,
  getMarketCatalog,
  getNodes,
  getRun,
  getTrace,
  listBlueprints,
  startLive as startLiveSession,
  stopLive as stopLiveSession,
} from "../lib/api";
import { buildBacktestRunBody, initialBacktestConfig, replayCandlesForCursor,
         toDatetimeLocal, fromDatetimeLocal, type BacktestConfig,
         type BacktestSpeed, type MarketWindow } from "../lib/backtestConfig";
import { pickDefaultBlueprint, type GalleryItem } from "../lib/demoDefaults";
import { parseInsights, type TraceRow } from "../lib/insights";
import { buildLiveStageSnapshots, currentLiveCandle } from "../lib/liveDesk";
import { localize, loomToFlow, type Loom, type NodeDef } from "../lib/loom";
import { openLiveSocket } from "../lib/ws";
import { useLang } from "../lib/i18n";

const EMPTY: Loom = { id: "empty", name: "No blueprint", nodes: [], edges: [], meta: {} };
const nodeTypes = { loomNode: NodeCard };
const LIVE_BAR_OPTIONS = ["1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D"];

const labels = {
  zh: {
    title: "Live Desk",
    subtitle: "PA_Agent 风格纸面实时台：蓝图在左，K 线在中，右侧同步看诊断、门控、反思。",
    blueprint: "蓝图",
    market: "市场",
    bar: "周期",
    start: "开始",
    end: "结束",
    speed: "流速",
    startLive: "启动实时",
    step: "单步",
    resume: "继续",
    stop: "停止",
    dataSource: "数据源",
    paperLive: "Paper live · recorded OKX candles",
    compile: "编译",
    compiled: "通过",
    failed: "失败",
    status: "状态",
    noBlueprint: "正在装载蓝图...",
    noCandles: "当前窗口暂无 K 线",
    activeNodes: "活跃节点",
    noActiveNodes: "等待下一根 bar",
    protocol: "门控协议",
    currentBar: "当前 Bar",
    ohlc: "OHLC",
    volume: "成交量",
    progress: "进度",
    equity: "权益",
    fills: "成交",
    analysis: "右侧分析",
    committee: "委员会",
    reflection: "反思",
    citations: "引用",
    gateLiveTitle: "门控路径正在运行",
    gateIdleTitle: "等待实时流",
    activePath: "活跃路径",
    noFillYet: "暂无成交：RiskGate 只会放行带盖章的订单。",
    latestFill: "最近成交",
    barExplain: "当前 bar 正在驱动蓝图节点，右侧门控会同步更新。",
    noTrace: "运行中会持续点亮节点；完成后这里展示委员会/反思 trace。",
  },
  en: {
    title: "Live Desk",
    subtitle: "PA_Agent-style paper-live desk: blueprint left, candles center, diagnosis/gates/reflection right.",
    blueprint: "Blueprint",
    market: "Market",
    bar: "Bar",
    start: "Start",
    end: "End",
    speed: "Pace",
    startLive: "Start Live",
    step: "Step",
    resume: "Resume",
    stop: "Stop",
    dataSource: "Data source",
    paperLive: "Paper live · recorded OKX candles",
    compile: "Compile",
    compiled: "pass",
    failed: "failed",
    status: "Status",
    noBlueprint: "Loading blueprint...",
    noCandles: "No candles in this window",
    activeNodes: "Active nodes",
    noActiveNodes: "Waiting for next bar",
    protocol: "Gate protocol",
    currentBar: "Current bar",
    ohlc: "OHLC",
    volume: "Volume",
    progress: "Progress",
    equity: "Equity",
    fills: "Fills",
    analysis: "Right analysis",
    committee: "Committee",
    reflection: "Reflection",
    citations: "Citations",
    gateLiveTitle: "Gate path live",
    gateIdleTitle: "Waiting for live stream",
    activePath: "Active path",
    noFillYet: "No fill yet; RiskGate only lets stamped orders reach execution.",
    latestFill: "Latest fill",
    barExplain: "The current bar is driving the blueprint nodes while gate states update on the right.",
    noTrace: "Nodes light up while streaming; committee/reflection trace appears after the run records evidence.",
  },
} as const;

function liveInitialConfig(loom: Loom, catalog: MarketWindow[]): BacktestConfig {
  return { ...initialBacktestConfig(loom, catalog), speed: "1x" };
}

export default function LiveDesk() {
  const { lang } = useLang();
  const l = labels[lang];
  const [defs, setDefs] = useState<Record<string, NodeDef>>({});
  const [gallery, setGallery] = useState<GalleryItem[]>([]);
  const [catalog, setCatalog] = useState<MarketWindow[]>([]);
  const [blueprint, setBlueprint] = useState<Loom>(EMPTY);
  const [config, setConfig] = useState<BacktestConfig>(() => liveInitialConfig(EMPTY, []));
  const [candles, setCandles] = useState<Candle[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);
  const [equityCurve, setEquityCurve] = useState<[number, number][]>([]);
  const [traceRows, setTraceRows] = useState<TraceRow[]>([]);
  const [liveAnalyses, setLiveAnalyses] = useState<Record<string, any>[]>([]);
  const [activeNodeIds, setActiveNodeIds] = useState<string[]>([]);
  const [compileErrors, setCompileErrors] = useState<{ node_id?: string; message: string }[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runState, setRunState] = useState<{ id?: string; status?: string; cursor?: number; equity?: number;
    mode?: "live" | "replay" }>({});
  const sock = useRef<ReturnType<typeof openLiveSocket> | null>(null);
  const liveSessionId = useRef<string | null>(null);
  const traceTimer = useRef<number>();

  const stopActiveSession = useCallback((closeSocket = false) => {
    const sessionId = liveSessionId.current;
    if (sessionId) {
      sock.current?.send("stop");
      void stopLiveSession(sessionId).catch(() => {});
    }
    if (closeSocket) {
      sock.current?.close();
      sock.current = null;
      liveSessionId.current = null;
    }
  }, []);

  useEffect(() => {
    let alive = true;
    getNodes().then((ns) => {
      if (alive) setDefs(Object.fromEntries(ns.map((node) => [node.type, node])));
    }).catch((err) => {
      if (alive) setLoadError(String((err as Error).message ?? err));
    });
    listBlueprints().then((items) => { if (alive) setGallery(items); }).catch((err) => {
      if (alive) setLoadError(String((err as Error).message ?? err));
    });
    getMarketCatalog().then((windows) => { if (alive) setCatalog(windows); }).catch((err) => {
      if (alive) setLoadError(String((err as Error).message ?? err));
    });
    return () => {
      alive = false;
      stopActiveSession(true);
      window.clearTimeout(traceTimer.current);
    };
  }, [stopActiveSession]);

  const loadBlueprint = useCallback(async (id: string) => {
    const loom = await getBlueprint(id);
    setBlueprint(loom);
    setConfig(liveInitialConfig(loom, catalog));
    setCandles([]);
    setFills([]);
    setEquityCurve([]);
    setTraceRows([]);
    setLiveAnalyses([]);
    setActiveNodeIds([]);
    setRunState({});
  }, [catalog]);

  useEffect(() => {
    if (!Object.keys(defs).length || !gallery.length || blueprint !== EMPTY) return;
    const id = pickDefaultBlueprint(gallery) ?? gallery[0]?.id;
    if (id) void loadBlueprint(id);
  }, [blueprint, defs, gallery, loadBlueprint]);

  useEffect(() => {
    if (!blueprint.nodes.length) return;
    let cancelled = false;
    compileLoom(blueprint, config.bar)
      .then((result) => {
        if (!cancelled) setCompileErrors(result.ok ? [] : result.errors);
      })
      .catch((err) => {
        if (!cancelled) setCompileErrors([{ message: String((err as Error).message ?? err) }]);
      });
    return () => { cancelled = true; };
  }, [blueprint, config.bar]);

  useEffect(() => {
    if (!config.inst || !config.bar) return;
    let cancelled = false;
    getCandles(config.inst, config.bar, {
      start: config.start_ms,
      end: config.end_ms,
      limit: 5000,
    }).then((rows) => {
      if (!cancelled) setCandles(rows);
    }).catch(() => {
      if (!cancelled) setCandles([]);
    });
    return () => { cancelled = true; };
  }, [config.inst, config.bar, config.start_ms, config.end_ms]);

  const refreshTrace = useCallback((runId: string) => {
    getTrace(runId, undefined, undefined, 1200)
      .then((rows) => setTraceRows(rows as TraceRow[]))
      .catch(() => {});
  }, []);

  const scheduleTraceRefresh = useCallback((runId: string) => {
    window.clearTimeout(traceTimer.current);
    traceTimer.current = window.setTimeout(() => refreshTrace(runId), 180);
  }, [refreshTrace]);

  const runLive = useCallback(async () => {
    if (!blueprint.nodes.length || compileErrors.length) return;
    stopActiveSession(true);
    window.clearTimeout(traceTimer.current);
    setFills([]);
    setEquityCurve([]);
    setTraceRows([]);
    setLiveAnalyses([]);
    setActiveNodeIds([]);
    let session_id: string;
    try {
      ({ session_id } = await startLiveSession({
        ...buildBacktestRunBody(blueprint, config, []),
        poll_ms: config.speed === "instant" ? 250 : config.speed === "4x" ? 1_000 : 5_000,
        analysis: true,
        analysis_every: 1,
        context_bars: 30,
        fetch_limit: 5,
      }));
    } catch (err) {
      setRunState({ status: `error: ${String((err as Error).message ?? err)}` });
      return;
    }
    liveSessionId.current = session_id;
    setRunState({ id: session_id, status: "running", cursor: -1, equity: config.cash, mode: "live" });
    sock.current = openLiveSocket(session_id, (ev) => {
      if (ev.type === "bar") {
        const nextActive = (ev.active as string[] | undefined) ?? [];
        setActiveNodeIds(nextActive);
        setRunState((state) => ({ ...state, status: "running", cursor: ev.idx, equity: ev.equity }));
        if (ev.candle && typeof ev.candle.ts === "number") {
          setCandles((prev) => {
            const seen = new Map(prev.map((c) => [c.ts, c]));
            seen.set(ev.candle.ts, ev.candle as Candle);
            return [...seen.values()].sort((a, b) => a.ts - b.ts).slice(-500);
          });
        }
        if (typeof ev.ts === "number" && typeof ev.equity === "number")
          setEquityCurve((curve) => [...curve, [ev.ts, ev.equity]]);
        setFills((prev) => [...prev, ...((ev.fills as Fill[] | undefined) ?? [])]);
        scheduleTraceRefresh(session_id);
      } else if (ev.type === "analysis") {
        setLiveAnalyses((prev) => [...prev, ev].slice(-20));
      } else if (ev.type === "paused") {
        setRunState((state) => ({ ...state, status: "paused" }));
        scheduleTraceRefresh(session_id);
      } else if (ev.type === "done") {
        liveSessionId.current = null;
        setRunState((state) => ({ ...state, status: "done" }));
        setActiveNodeIds([]);
        getRun(session_id).then((r) => {
          const report = r.report as { fills?: Fill[]; equity_curve?: [number, number][] } | undefined;
          setFills(report?.fills ?? []);
          setEquityCurve(report?.equity_curve ?? []);
        }).catch(() => {});
        refreshTrace(session_id);
      } else if (ev.type === "error") {
        liveSessionId.current = null;
        setRunState((state) => ({ ...state, status: `error: ${ev.message}` }));
      } else if (ev.type === "status") {
        setRunState((state) => ({ ...state, status: ev.status }));
      }
    }, () => {
      if (liveSessionId.current !== session_id) return;
      setRunState((state) => {
        if (state.status === "done" || state.status === "stopped"
          || String(state.status ?? "").startsWith("error")) return state;
        return { ...state, status: "disconnected" };
      });
    });
  }, [blueprint, compileErrors.length, config, refreshTrace, scheduleTraceRefresh, stopActiveSession]);

  const sendCommand = useCallback((cmd: "resume" | "step" | "stop") => {
    if (cmd === "stop") {
      stopActiveSession(false);
      return;
    }
    sock.current?.send(cmd);
  }, [stopActiveSession]);

  const updateMarket = (inst: string, bar: string) => {
    const window = catalog.find((item) => item.inst === inst && item.bar === bar);
    setConfig((current) => ({
      ...current,
      inst,
      bar,
      ...(window ? { start_ms: window.start_ms, end_ms: window.end_ms } : {}),
    }));
  };

  const marketOptions = [...new Set(catalog.map((item) => item.inst))];
  const barOptions = [...new Set([
    ...catalog.filter((item) => item.inst === config.inst).map((item) => item.bar),
    ...LIVE_BAR_OPTIONS,
  ])];
  const flow = useMemo(() => loomToFlow(blueprint, defs), [blueprint, defs]);
  const blockedIds = useMemo(() => new Set(compileErrors.map((err) => err.node_id).filter(Boolean)), [compileErrors]);
  const nodes = useMemo(() => flow.nodes.map((node) => ({
    ...node,
    data: {
      ...node.data,
      active: activeNodeIds.includes(node.id),
      blocked: blockedIds.has(node.id),
    },
  })) as Node[], [activeNodeIds, blockedIds, flow.nodes]);
  const edges = useMemo(() => flow.edges.map((edge) => ({
    ...edge,
    animated: activeNodeIds.includes(edge.source) || edge.data.feedback,
    style: activeNodeIds.includes(edge.source) ? { stroke: "#34d399", strokeWidth: 2 } : undefined,
  })) as Edge[], [activeNodeIds, flow.edges]);

  const streaming = runState.status === "running" || runState.status === "paused";
  const visibleCandles = runState.mode === "live"
    ? candles
    : streaming ? replayCandlesForCursor(candles, runState.cursor) : candles;
  const currentCandle = runState.mode === "live"
    ? candles.at(-1) ?? null
    : currentLiveCandle(candles, runState.cursor);
  const visibleLastTs = visibleCandles.at(-1)?.ts;
  const visibleFills = visibleLastTs === undefined ? fills : fills.filter((fill) => fill.ts <= visibleLastTs);
  const lastFill = visibleFills.at(-1);
  const stages = useMemo(() => buildLiveStageSnapshots({
    loom: blueprint,
    activeNodeIds,
    traceRows,
  }), [activeNodeIds, blueprint, traceRows]);
  const insights = useMemo(() => parseInsights(traceRows), [traceRows]);
  const latestAnalysis = liveAnalyses.at(-1);
  const barsSeen = Math.max(0, (runState.cursor ?? -1) + 1);
  const liveNarrative = useMemo(() => {
    const path = activeNodeIds.length ? activeNodeIds.join(" -> ") : "-";
    if (!streaming) {
      return {
        title: l.gateIdleTitle,
        body: l.noTrace,
        path,
        fill: "-",
      };
    }
    const fillText = lastFill
      ? `${l.latestFill}: ${lastFill.side} ${lastFill.qty.toFixed(4)} @ ${lastFill.price.toFixed(2)}`
      : l.noFillYet;
    const analysisOutput = latestAnalysis?.output as Record<string, unknown> | undefined;
    if (analysisOutput) {
      return {
        title: String(analysisOutput.market_state ?? l.gateLiveTitle),
        body: `${analysisOutput.current_gate ?? "-"} · ${analysisOutput.risk_reason ?? "-"}`,
        path,
        fill: `${fillText} · ${analysisOutput.suggestion ?? ""}`,
      };
    }
    return {
      title: l.gateLiveTitle,
      body: currentCandle
        ? `${l.barExplain} close=${currentCandle.close.toFixed(2)} volume=${currentCandle.volume.toFixed(2)}`
        : l.barExplain,
      path,
      fill: fillText,
    };
  }, [activeNodeIds, currentCandle, l, lastFill, latestAnalysis, streaming]);

  return (
    <div className="h-full min-h-0 overflow-auto p-2 xl:overflow-hidden">
      <div className="grid min-h-full grid-rows-[auto_1fr] gap-2 xl:h-full xl:min-h-0">
        <section className="panel px-3 py-2">
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-[220px] mr-auto">
              <div className="font-display text-lg font-semibold text-slate-100">{l.title}</div>
              <div className="max-w-[520px] truncate text-[11px] text-slate-500">{l.subtitle}</div>
              {loadError && (
                <div className="max-w-[520px] truncate font-mono text-[11px] text-loom-red">{loadError}</div>
              )}
            </div>
            <label className="min-w-[220px] flex-1 space-y-1 xl:flex-none">
              <span className="hud-label">{l.blueprint}</span>
              <select className="w-full rounded border border-edge bg-bg px-2 py-1 text-xs text-slate-100"
                      value={blueprint.id === EMPTY.id ? "" : blueprint.id}
                      onChange={(event) => void loadBlueprint(event.target.value)}>
                {gallery.map((item) => (
                  <option key={`${item.source}:${item.id}`} value={item.id}>{item.name}</option>
                ))}
              </select>
            </label>
            <label className="min-w-[160px] space-y-1">
              <span className="hud-label">{l.market}</span>
              <select className="w-full rounded border border-edge bg-bg px-2 py-1 text-xs text-slate-100"
                      value={config.inst}
                      onChange={(event) => updateMarket(event.target.value, config.bar)}>
                {(marketOptions.length ? marketOptions : [config.inst]).map((inst) => (
                  <option key={inst} value={inst}>{inst}</option>
                ))}
              </select>
            </label>
            <label className="w-24 space-y-1">
              <span className="hud-label">{l.bar}</span>
              <select className="w-full rounded border border-edge bg-bg px-2 py-1 text-xs text-slate-100"
                      value={config.bar}
                      onChange={(event) => updateMarket(config.inst, event.target.value)}>
                {(barOptions.length ? barOptions : [config.bar]).map((bar) => (
                  <option key={bar} value={bar}>{bar}</option>
                ))}
              </select>
            </label>
            <label className="w-[150px] space-y-1">
              <span className="hud-label">{l.start}</span>
              <input type="datetime-local"
                     className="w-full rounded border border-edge bg-bg px-2 py-1 text-xs text-slate-100"
                     value={toDatetimeLocal(config.start_ms)}
                     onChange={(event) => setConfig({ ...config, start_ms: fromDatetimeLocal(event.target.value) })} />
            </label>
            <label className="w-[150px] space-y-1">
              <span className="hud-label">{l.end}</span>
              <input type="datetime-local"
                     className="w-full rounded border border-edge bg-bg px-2 py-1 text-xs text-slate-100"
                     value={toDatetimeLocal(config.end_ms)}
                     onChange={(event) => setConfig({ ...config, end_ms: fromDatetimeLocal(event.target.value) })} />
            </label>
            <label className="w-28 space-y-1">
              <span className="hud-label">{l.speed}</span>
              <select className="w-full rounded border border-edge bg-bg px-2 py-1 text-xs text-slate-100"
                      value={config.speed}
                      onChange={(event) => setConfig({ ...config, speed: event.target.value as BacktestSpeed })}>
                <option value="1x">1x</option>
                <option value="4x">4x</option>
                <option value="instant">instant</option>
              </select>
            </label>
          </div>
        </section>

        <main className="grid min-h-0 grid-cols-1 gap-2 xl:grid-cols-[minmax(480px,1.15fr)_minmax(430px,1fr)_320px]">
          <section className="panel min-h-[430px] overflow-hidden xl:min-h-0">
            <div className="flex items-center justify-between border-b border-edge/70 px-3 py-2">
              <div>
                <div className="hud-label">{l.blueprint}</div>
                <div className="text-sm font-semibold text-slate-100">{blueprint.name || l.noBlueprint}</div>
              </div>
              <div className="text-right text-[11px]">
                <div className="hud-label">{l.compile}</div>
                <div className={compileErrors.length ? "text-loom-red" : "text-loom-green"}>
                  {compileErrors.length ? l.failed : l.compiled}
                </div>
              </div>
            </div>
            <div className="h-[calc(100%-58px)]">
              {nodes.length ? (
                <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes}
                           fitView nodesDraggable={false} nodesConnectable={false}
                           elementsSelectable={false} fitViewOptions={{ padding: 0.1 }}
                           proOptions={{ hideAttribution: true }}>
                  <Background color="#1e2a44" gap={24} />
                  <Controls />
                </ReactFlow>
              ) : (
                <div className="grid h-full place-items-center text-xs text-slate-500">{l.noBlueprint}</div>
              )}
            </div>
          </section>

          <section className="grid min-h-[650px] grid-rows-[auto_minmax(0,1fr)_154px] gap-2 xl:min-h-0">
            <div className="panel p-3">
              <div className="grid grid-cols-[1fr_auto] gap-3">
                <div className="grid grid-cols-4 gap-2 text-xs">
                  <Metric label={l.dataSource} value={l.paperLive} tone="blue" />
                  <Metric label={l.status} value={runState.status ?? "idle"} tone={runState.status === "running" ? "green" : "slate"} />
                  <Metric label={l.progress}
                          value={runState.mode === "live" ? `${barsSeen} live bars` : `${barsSeen} / ${candles.length}`}
                          tone="amber" />
                  <Metric label={l.equity} value={runState.equity?.toFixed(2) ?? "-"} tone="green" />
                </div>
                <div className="grid grid-cols-4 gap-2">
                  <button type="button" onClick={() => void runLive()}
                          disabled={!blueprint.nodes.length || !!compileErrors.length || streaming}
                          className="col-span-2 rounded bg-loom-gold/20 px-3 py-2 text-xs font-semibold text-loom-gold disabled:opacity-30">
                    ▶ {l.startLive}
                  </button>
                  <button type="button" onClick={() => sendCommand("step")}
                          disabled={!runState.id || runState.mode === "live"}
                          className="rounded bg-loom-violet/15 px-2 py-2 text-xs text-loom-violet disabled:opacity-30">
                    {l.step}
                  </button>
                  <button type="button" onClick={() => sendCommand("stop")}
                          disabled={!runState.id || runState.status === "done"}
                          className="rounded bg-loom-red/15 px-2 py-2 text-xs text-loom-red disabled:opacity-30">
                    {l.stop}
                  </button>
                  <button type="button" onClick={() => sendCommand("resume")}
                          disabled={!runState.id || runState.mode === "live"}
                          className="col-span-4 rounded bg-loom-green/15 px-2 py-1.5 text-xs text-loom-green disabled:opacity-30">
                    {l.resume}
                  </button>
                </div>
              </div>
            </div>

            <div className="panel min-h-0 overflow-hidden p-2">
              {visibleCandles.length ? (
                <CandleChart candles={visibleCandles} fills={visibleFills} height={360} />
              ) : (
                <div className="grid h-full place-items-center text-xs text-slate-500">{l.noCandles}</div>
              )}
            </div>

            <div className="grid min-h-0 grid-cols-[1fr_1fr] gap-2">
              <div className="panel p-3">
                <div className="hud-label mb-2">{l.currentBar}</div>
                {currentCandle ? (
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <Metric label="time" value={new Date(currentCandle.ts).toLocaleString()} tone="slate" />
                    <Metric label={l.volume} value={currentCandle.volume.toFixed(2)} tone="blue" />
                    <Metric label={l.ohlc} value={`${currentCandle.open.toFixed(2)} / ${currentCandle.high.toFixed(2)}`} tone="green" />
                    <Metric label="" value={`${currentCandle.low.toFixed(2)} / ${currentCandle.close.toFixed(2)}`} tone="amber" />
                  </div>
                ) : (
                  <div className="text-xs text-slate-500">{l.noActiveNodes}</div>
                )}
              </div>
              <div className="panel overflow-hidden p-3">
                <div className="hud-label mb-2">{l.equity}</div>
                <EquityChart curve={equityCurve} height={96} />
              </div>
            </div>
          </section>

          <aside className="min-h-[420px] space-y-2 overflow-auto xl:min-h-0">
            <section className="panel p-3">
              <div className="hud-label mb-2">{l.activeNodes}</div>
              {activeNodeIds.length ? (
                <div className="flex flex-wrap gap-1">
                  {activeNodeIds.map((id) => <Badge key={id} text={id} tone="green" />)}
                </div>
              ) : (
                <div className="text-xs text-slate-500">{l.noActiveNodes}</div>
              )}
            </section>

            <section className="panel p-3">
              <div className="hud-label mb-2">{l.analysis}</div>
              <div className="mb-3 rounded border border-loom-green/35 bg-loom-green/10 p-2">
                <div className="text-xs font-semibold text-slate-100">{liveNarrative.title}</div>
                <div className="mt-1 text-[11px] leading-snug text-slate-400">{liveNarrative.body}</div>
                <div className="mt-2">
                  <div className="hud-label mb-1 text-[9px]">{l.activePath}</div>
                  <div className="break-all font-mono text-[11px] text-loom-green">{liveNarrative.path}</div>
                </div>
                <div className="mt-2 font-mono text-[11px] text-loom-gold">{liveNarrative.fill}</div>
              </div>
              {!!liveAnalyses.length && (
                <LiveAnalysisCards rows={liveAnalyses}
                                   label={lang === "zh" ? "LLM 实时分析" : "LLM live analysis"} />
              )}
              {insights.hasAny ? (
                <div className="space-y-3">
                  {insights.committees.slice(0, 2).map((committee) => (
                    <div key={committee.nodeId} className="rounded border border-loom-amber/35 bg-loom-amber/10 p-2">
                      <div className="text-xs font-semibold text-slate-100">{l.committee} · {committee.nodeId}</div>
                      {committee.side && <div className="mt-1 font-mono text-xs text-loom-amber">{committee.side}</div>}
                      {committee.rationale && <div className="mt-1 text-[11px] text-slate-400">{committee.rationale}</div>}
                    </div>
                  ))}
                  {insights.verdicts.slice(-3).map((verdict) => (
                    <div key={`${verdict.trade_key}:${verdict.verdict}`} className="rounded border border-loom-cyan/35 bg-loom-cyan/10 p-2">
                      <div className="text-xs font-semibold text-slate-100">{l.reflection} · {verdict.verdict}</div>
                      <div className={`mt-1 font-mono text-xs ${verdict.pnl >= 0 ? "text-loom-green" : "text-loom-red"}`}>
                        pnl {verdict.pnl.toFixed(2)} · {verdict.bucket}
                      </div>
                      {verdict.lesson && <div className="mt-1 text-[11px] text-slate-400">{verdict.lesson}</div>}
                    </div>
                  ))}
                  {!!insights.citations.length && (
                    <div>
                      <div className="hud-label mb-1">{l.citations}</div>
                      <div className="flex flex-wrap gap-1">
                        {insights.citations.map((citation) => <Badge key={citation} text={citation} tone="violet" />)}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-xs leading-relaxed text-slate-500">{l.noTrace}</div>
              )}
            </section>

            <section className="panel p-3">
              <div className="hud-label mb-3">{l.protocol}</div>
              <div className="space-y-2">
                {stages.map((stage, idx) => (
                  <div key={stage.id}
                       className={`rounded border px-3 py-2 ${stage.state === "active"
                         ? "border-loom-green/70 bg-loom-green/10"
                         : stage.state === "seen"
                           ? "border-loom-blue/50 bg-loom-blue/10"
                           : "border-edge bg-bg/30"}`}>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-slate-500">{String(idx + 1).padStart(2, "0")}</span>
                      <span className="text-xs font-semibold text-slate-100">{localize(stage.title, lang)}</span>
                      <span className="ml-auto"><Badge text={stage.state} tone={stage.state === "active" ? "green" : stage.state === "seen" ? "blue" : "slate"} /></span>
                    </div>
                    <div className="mt-1 text-[11px] leading-snug text-slate-400">{localize(stage.body, lang)}</div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {stage.nodeIds.map((id) => <Badge key={id} text={id} tone={activeNodeIds.includes(id) ? "green" : "slate"} />)}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="panel p-3">
              <div className="hud-label mb-2">{l.fills}</div>
              <div className="max-h-40 overflow-auto">
                {visibleFills.slice(-8).map((fill, idx) => (
                  <div key={`${fill.ts}:${idx}`} className="grid grid-cols-[48px_1fr_1fr] gap-2 border-b border-edge/50 py-1 text-[11px] font-mono text-slate-300">
                    <span className={fill.side === "buy" ? "text-loom-green" : "text-loom-red"}>{fill.side}</span>
                    <span>{fill.qty.toFixed(4)}</span>
                    <span>{fill.price.toFixed(2)}</span>
                  </div>
                ))}
                {!visibleFills.length && <div className="text-xs text-slate-500">-</div>}
              </div>
            </section>
          </aside>
        </main>
      </div>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone: "slate" | "blue" | "green" | "amber" }) {
  const color = tone === "blue" ? "text-loom-blue"
    : tone === "green" ? "text-loom-green"
      : tone === "amber" ? "text-loom-gold" : "text-slate-300";
  return (
    <div className="min-w-0 rounded border border-edge/70 bg-bg/35 px-2 py-1.5">
      {label && <div className="hud-label truncate text-[9px]">{label}</div>}
      <div className={`truncate font-mono text-xs ${color}`}>{value}</div>
    </div>
  );
}

function LiveAnalysisCards({ rows, label }: { rows: Record<string, any>[]; label: string }) {
  return (
    <div className="mb-3 space-y-2">
      <div className="hud-label">{label}</div>
      {rows.slice(-3).map((row) => {
        const output = (row.output ?? {}) as Record<string, unknown>;
        return (
          <div key={`${row.event_idx}:${row.prompt_hash}`}
               className="rounded border border-loom-blue/35 bg-loom-blue/10 p-2">
            <div className="text-xs font-semibold text-slate-100">
              {String(output.market_state ?? "-")}
            </div>
            <div className="mt-1 text-[11px] leading-snug text-slate-400">
              {String(output.current_gate ?? "-")}
            </div>
            <div className="mt-1 text-[11px] leading-snug text-slate-400">
              {String(output.risk_reason ?? "-")}
            </div>
            {output.suggestion !== undefined && (
              <div className="mt-1 text-[11px] leading-snug text-slate-500">
                {String(output.suggestion)}
              </div>
            )}
            <div className="mt-2 font-mono text-[10px] text-slate-500">
              prompt {String(row.prompt_hash ?? "").slice(0, 12)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Badge({ text, tone }: { text: string; tone: "slate" | "blue" | "green" | "violet" }) {
  const cls = tone === "blue" ? "border-loom-blue/35 bg-loom-blue/10 text-loom-blue"
    : tone === "green" ? "border-loom-green/35 bg-loom-green/10 text-loom-green"
      : tone === "violet" ? "border-loom-violet/35 bg-loom-violet/10 text-loom-violet"
        : "border-edge bg-bg/40 text-slate-400";
  return <span className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${cls}`}>{text}</span>;
}
