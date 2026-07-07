// frontend/src/pages/Studio.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, Background, Controls, addEdge, useEdgesState, useNodesState,
         ConnectionLineType, MarkerType,
         type Connection, type DefaultEdgeOptions, type Edge, type Node } from "@xyflow/react";
import NodeCard from "../components/NodeCard";
import GateView from "../components/GateView";
import ErrorPanel, { type CompileError } from "../components/ErrorPanel";
import CertPanel from "../components/CertPanel";
import PausedInspector from "../components/PausedInspector";
import CopilotPanel, { type Preview } from "../components/CopilotPanel";
import BacktestLab from "../components/BacktestLab";
import type { Candle, Fill } from "../components/CandleChart";
import { compileLoom, getBlueprint, getCandles, getMarketCatalog, getNodes, getRun,
         listBlueprints, saveBlueprint, startRun } from "../lib/api";
import { pickDefaultBlueprint, type GalleryItem } from "../lib/demoDefaults";
import { buildBacktestRunBody, initialBacktestConfig,
         type BacktestConfig, type MarketWindow } from "../lib/backtestConfig";
import { openRunSocket } from "../lib/ws";
import { CATEGORY_COLORS, PIN_COLORS, flowToLoom, loomToFlow, nextNodeId,
         layoutLoomPositions, pinTypeForHandle, validateFlowConnection, type Loom, type NodeDef } from "../lib/loom";
import { useLang } from "../lib/i18n";

const EMPTY: Loom = { id: "untitled", name: "Untitled", nodes: [], edges: [], meta: {} };
const nodeTypes = { loomNode: NodeCard };
const EDGE_PATH_OPTIONS = { offset: 34, borderRadius: 16 };
const DEFAULT_EDGE_OPTIONS: DefaultEdgeOptions = {
  type: "smoothstep",
  style: { stroke: "#64748b", strokeWidth: 2, opacity: 0.85 },
  markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b", width: 16, height: 16 },
};

function readableEdge(edge: Edge, flowNodes: Node[]): Edge {
  const pin = pinTypeForHandle(
    flowNodes as never,
    edge.source,
    edge.sourceHandle,
    "out",
  );
  const fallbackStroke = pin ? PIN_COLORS[pin] : "#64748b";
  const stroke = typeof edge.style?.stroke === "string" ? edge.style.stroke : fallbackStroke;
  const feedback = !!(edge.data as { feedback?: boolean } | undefined)?.feedback;
  return {
    ...edge,
    type: "smoothstep",
    pathOptions: EDGE_PATH_OPTIONS,
    animated: feedback || edge.animated,
    markerEnd: edge.markerEnd ?? { type: MarkerType.ArrowClosed, color: stroke, width: 16, height: 16 },
    style: {
      stroke,
      strokeWidth: feedback ? 1.8 : 2.2,
      opacity: feedback ? 0.62 : 0.88,
      ...(feedback ? { strokeDasharray: "6 4" } : {}),
      ...(edge.style ?? {}),
    },
  } as Edge;
}

const STUDIO_COPY = {
  zh: {
    workspace: "工作台",
    activeBlueprint: "当前蓝图",
    presets: "预设蓝图",
    nodeLibrary: "节点库",
    canvas: "蓝图画布",
    organizeLayout: "整理布局",
    copilotTab: "Copilot",
    inspectTab: "Inspect",
    inspectTitle: "编译与运行检查",
  },
  en: {
    workspace: "Workspace",
    activeBlueprint: "Active blueprint",
    presets: "Preset blueprints",
    nodeLibrary: "Node library",
    canvas: "Blueprint canvas",
    organizeLayout: "Auto layout",
    copilotTab: "Copilot",
    inspectTab: "Inspect",
    inspectTitle: "Compile and run checks",
  },
} as const;

export default function Studio() {
  const { t, lang } = useLang();
  const l = STUDIO_COPY[lang];
  const [defs, setDefs] = useState<Record<string, NodeDef>>({});
  const [gallery, setGallery] = useState<GalleryItem[]>([]);
  const [base, setBase] = useState<Loom>(EMPTY);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [errors, setErrors] = useState<CompileError[]>([]);
  const [cert, setCert] = useState<Record<string, unknown> | null>(null);
  const [bps, setBps] = useState<Set<string>>(new Set());
  const [runState, setRunState] = useState<{ id?: string; bar?: number; equity?: number;
    status?: string; paused?: { node_id: string; ts: number; inputs: Record<string, unknown> } | null }>({});
  const sock = useRef<ReturnType<typeof openRunSocket> | null>(null);
  const glowTimer = useRef<number>();
  const didAutoLoad = useRef(false);
  const didBacktestCatalogSync = useRef(false);
  // Run 模式：backtest（默认）| replay（加速回放，走真实 LLM/录制）。
  // 纯 run-time 参数——不进 structuralKey（切模式不触发重编译，防 D2 编译循环）。
  const [blueprintView, setBlueprintView] = useState<"gate" | "graph">("gate");
  // Copilot：预览态 + 选中节点 + 最近报告（optimize 读它）。
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [marketCatalog, setMarketCatalog] = useState<MarketWindow[]>([]);
  const [backtestConfig, setBacktestConfig] = useState<BacktestConfig>(
    () => initialBacktestConfig(EMPTY, []));
  const [rightTab, setRightTab] = useState<"copilot" | "inspect">("copilot");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [flowKey, setFlowKey] = useState(0);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);
  const [equityCurve, setEquityCurve] = useState<[number, number][]>([]);
  const defsReady = Object.keys(defs).length > 0;

  const toggleBp = useCallback((id: string) => {
    setBps((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }, []);

  useEffect(() => {
    getNodes().then((ns) => {
      setDefs(Object.fromEntries(ns.map((n) => [n.type, n])));
    }).catch((err) => setLoadError(String((err as Error).message ?? err)));
    listBlueprints().then(setGallery)
      .catch((err) => setLoadError(String((err as Error).message ?? err)));
    getMarketCatalog().then(setMarketCatalog)
      .catch((err) => setLoadError(String((err as Error).message ?? err)));
    return () => {
      sock.current?.send("stop");
      sock.current?.close();
    };
  }, []);

  const currentLoom = useCallback(() =>
    flowToLoom(nodes as never, edges as never, base), [nodes, edges, base]);

  useEffect(() => {
    if (didBacktestCatalogSync.current || !marketCatalog.length || !nodes.length) return;
    didBacktestCatalogSync.current = true;
    setBacktestConfig(initialBacktestConfig(currentLoom(), marketCatalog));
  }, [currentLoom, marketCatalog, nodes.length]);

  useEffect(() => {
    if (!backtestConfig.inst || !backtestConfig.bar) {
      setCandles([]);
      return;
    }
    let cancelled = false;
    getCandles(backtestConfig.inst, backtestConfig.bar, {
      start: backtestConfig.start_ms,
      end: backtestConfig.end_ms,
      limit: 5000,
    }).then((rows) => {
      if (!cancelled) setCandles(rows);
    }).catch(() => {
      if (!cancelled) setCandles([]);
    });
    return () => { cancelled = true; };
  }, [backtestConfig.inst, backtestConfig.bar, backtestConfig.start_ms, backtestConfig.end_ms]);

  // —— Copilot diff 预览：派生渲染层，绝不 setNodes/setEdges，故不参与 structuralKey，
  // 不触发编译（避免重蹈 D2 编译循环）。预览态下画布只读展示"将变什么"。 ——
  const displayNodes = useMemo(() => {
    if (!preview) return nodes;
    const kind = preview.diff.nodeKind;
    // 现有节点：打 diff 标记（removed/changed；added 属于新图，下面补）。
    const marked = nodes.map((n) => ({ ...n,
      data: { ...n.data, diff: kind[n.id] === "changed" || kind[n.id] === "removed"
        ? kind[n.id] : undefined } }));
    // 新增节点：来自预览 loom，用其 meta.positions 定位，绿高亮。
    const existing = new Set(nodes.map((n) => n.id));
    const pos = (preview.loom.meta?.positions ?? {}) as Record<string, { x: number; y: number }>;
    const addable = loomToFlow(preview.loom, defs).nodes
      .filter((fn) => kind[fn.id] === "added" && !existing.has(fn.id))
      .map((fn) => ({ ...fn, position: pos[fn.id] ?? fn.position,
        data: { ...fn.data, diff: "added" as const } }));
    return [...marked, ...addable] as typeof nodes;
  }, [preview, nodes, defs]);

  const displayEdges = useMemo(() => {
    if (!preview) return edges.map((edge) => readableEdge(edge, nodes as Node[])) as typeof edges;
    const pf = loomToFlow(preview.loom, defs);
    // 预览态下展示新图的边（新增绿虚线），叠加当前边。去重按 handle 三元组。
    const seen = new Set(edges.map((e) => `${e.source}|${e.sourceHandle}|${e.target}|${e.targetHandle}`));
    const addedEdges = pf.edges
      .filter((e) => !seen.has(`${e.source}|${e.sourceHandle}|${e.target}|${e.targetHandle}`))
      .map((e) => readableEdge({
        ...e,
        animated: true,
        style: { stroke: "#34d399", strokeDasharray: "5 3", strokeWidth: 2.2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#34d399", width: 16, height: 16 },
      } as Edge, pf.nodes as unknown as Node[]));
    return [...edges.map((edge) => readableEdge(edge, nodes as Node[])), ...addedEdges] as typeof edges;
  }, [preview, edges, defs, nodes]);

  // 结构签名：只含影响编译的字段（不含瞬态 blocked/active），做编译 effect 的闸门。
  const structuralKey = useMemo(
    () => JSON.stringify({
      n: nodes.map((n) => ({ id: n.id, t: (n.data as { def: { type: string } }).def.type,
                             p: (n.data as { params: unknown }).params,
                             x: Math.round(n.position.x), y: Math.round(n.position.y) })),
      e: edges.map((e) => ({ s: e.source, sh: e.sourceHandle, t: e.target, th: e.targetHandle,
                             f: !!(e.data as { feedback?: boolean } | undefined)?.feedback })),
    }),
    [nodes, edges],
  );

  // 500ms 防抖编译
  const gateLoom = useMemo(() => currentLoom(), [currentLoom, structuralKey]);

  useEffect(() => {
    if (!nodes.length) { setErrors([]); setCert(null); return; }
    const h = setTimeout(() => {
      compileLoom(currentLoom()).then((r) => {
        setErrors(r.errors);
        setCert(r.ok ? r.certificate : null);
        const bad = new Set(r.errors.map((e) => e.node_id).filter(Boolean));
        setNodes((ns) => ns.map((n) => ({ ...n,
          data: { ...n.data, blocked: bad.has(n.id) } })));
      }).catch((err) => {
        const message = String((err as Error).message ?? err);
        setErrors([{ code: "COMPILE_UNREACHABLE", message }]);
        setCert(null);
        setNodes((ns) => ns.map((n) => ({ ...n,
          data: { ...n.data, blocked: true } })));
      });
    }, 500);
    return () => clearTimeout(h);
    // 只以 structuralKey 为闸门；currentLoom()/nodes 经闭包取最新值。
    // 切勿把 currentLoom 加回依赖——它身份每次 setNodes(blocked/active) 都变，会造成 500ms 无限编译。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structuralKey, setNodes]);

  const load = async (id: string) => {
    if (!defsReady) return;
    if (nodes.length && !confirm("覆盖当前画布?")) return;
    const loom = await getBlueprint(id);
    setBase(loom);
    const f = loomToFlow(loom, defs);
    const nextNodes = f.nodes.map((n) => ({ ...n, data: { ...n.data,
      breakpoint: false, onToggleBreakpoint: toggleBp } })) as unknown as Node[];
    setNodes(nextNodes as never);
    setEdges(f.edges.map((e) => readableEdge(e as unknown as Edge, nextNodes)) as never);
    setBps(new Set());
    setSelectedNodeId(null);
    setSelectedNodeIds([]);
    setConnectionError(null);
    didBacktestCatalogSync.current = marketCatalog.length > 0;
    setBacktestConfig(initialBacktestConfig(loom, marketCatalog));
    setRunState({});
    setReport(null);
    setFills([]);
    setEquityCurve([]);
  };

  useEffect(() => {
    if (didAutoLoad.current || !defsReady || nodes.length || !gallery.length) return;
    const id = pickDefaultBlueprint(gallery);
    if (!id) return;
    didAutoLoad.current = true;
    void load(id);
    // load captures current defs/nodes intentionally; this effect is one-shot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defsReady, gallery, nodes.length]);

  useEffect(() => {
    setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, breakpoint: bps.has(n.id),
      onToggleBreakpoint: toggleBp } })));
  }, [bps, setNodes, toggleBp]);

  const addNode = (type: string) => {
    const def = defs[type];
    if (!def) return;
    const id = nextNodeId(new Set(nodes.map((n) => n.id)), type);
    setNodes((ns) => [...ns, { id, type: "loomNode",
      position: { x: 120 + Math.random() * 240, y: 120 + Math.random() * 160 },
      data: { def, params: {}, onToggleBreakpoint: toggleBp } } as never]);
  };

  const isValidConnection = useCallback((c: Connection | Edge) =>
    validateFlowConnection(nodes as never, edges as never, c).ok, [nodes, edges]);

  const onConnect = useCallback((c: Connection) => {
    const validation = validateFlowConnection(nodes as never, edges as never, c);
    if (!validation.ok) {
      setConnectionError(validation.reason ?? "Invalid connection.");
      return;
    }
    setConnectionError(null);
    setEdges((es) => addEdge(
      readableEdge({ ...c, data: { feedback: false } } as unknown as Edge, nodes as Node[]),
      es,
    ));
  }, [edges, nodes, setEdges]);

  const onEdgeContextMenu = useCallback((e: React.MouseEvent, edge: Edge) => {
    e.preventDefault();
    setEdges((es) => es.map((x) => {
      if (x.id !== edge.id) return x;
      const feedback = !(x.data as { feedback?: boolean } | undefined)?.feedback;
      return readableEdge({
        ...x,
        data: { ...x.data, feedback },
        animated: feedback,
      } as Edge, nodes as Node[]);
    }));
  }, [nodes, setEdges]);

  const onNodeDoubleClick = useCallback((_: unknown, node: Node) => {
    const txt = prompt("params JSON", JSON.stringify((node.data as { params?: unknown }).params ?? {}));
    if (txt == null) return;
    try {
      const p = JSON.parse(txt);
      setNodes((ns) => ns.map((n) => n.id === node.id
        ? { ...n, data: { ...n.data, params: p } } : n));
    } catch { alert("bad JSON"); }
  }, [setNodes]);

  const focusProtocolNodes = useCallback((ids: string[]) => {
    if (!ids.length) return;
    const wanted = new Set(ids);
    setSelectedNodeId(ids[0]);
    setSelectedNodeIds(ids);
    setBlueprintView("graph");
    setNodes((ns) => ns.map((n) => ({ ...n, selected: wanted.has(n.id) })));
  }, [setNodes]);

  const updateBacktestConfig = useCallback((next: BacktestConfig) => {
    setBacktestConfig(next);
    setRunState({});
    setReport(null);
    setFills([]);
    setEquityCurve([]);
  }, []);

  const organizeLayout = useCallback(() => {
    const positions = layoutLoomPositions(currentLoom());
    setBlueprintView("graph");
    setSelectedNodeId(null);
    setSelectedNodeIds([]);
    setConnectionError(null);
    setNodes((ns) => ns.map((node) => ({
      ...node,
      selected: false,
      position: positions[node.id] ?? node.position,
    })));
    setFlowKey((key) => key + 1);
  }, [currentLoom, setNodes]);

  const run = useCallback(async (loom?: Loom) => {
    if (!loom && (errors.length || !nodes.length || preview)) return;
    sock.current?.close();               // 关旧连接，防重复 Run 泄漏 WS（T6 审查 Important）
    // Backtest Lab owns the explicit market window and playback speed.
    const blueprint = loom ?? currentLoom();
    const config = loom ? initialBacktestConfig(blueprint, marketCatalog) : backtestConfig;
    if (loom) setBacktestConfig(config);
    setFills([]);
    setEquityCurve([]);
    setReport(null);
    let run_id: string;
    try {
      ({ run_id } = await startRun(buildBacktestRunBody(blueprint, config, [...bps])));
    } catch (err) {
      setRunState({ status: `error: ${String((err as Error).message ?? err)}` });
      return;
    }
    setRunState({ id: run_id, status: "running", bar: -1 });
    sock.current = openRunSocket(run_id, (ev) => {
      if (ev.type === "bar") {
        setRunState((s) => ({ ...s, bar: ev.idx, equity: ev.equity }));
        if (typeof ev.ts === "number" && typeof ev.equity === "number") {
          setEquityCurve((curve) => [...curve, [ev.ts, ev.equity]]);
        }
        setFills((prev) => [...prev, ...((ev.fills as Fill[] | undefined) ?? [])]);
        setNodes((ns) => ns.map((n) => ({ ...n,
          data: { ...n.data, active: (ev.active as string[] | undefined)?.includes(n.id) } })));
        window.clearTimeout(glowTimer.current);
        glowTimer.current = window.setTimeout(() => setNodes((ns) =>
          ns.map((n) => ({ ...n, data: { ...n.data, active: false } }))), 150);
      } else if (ev.type === "paused") {
        setRunState((s) => ({ ...s, status: "paused", paused: ev as unknown as
          { node_id: string; ts: number; inputs: Record<string, unknown> } }));
      } else if (ev.type === "done") {
        setRunState((s) => ({ ...s, status: "done", paused: null }));
        getRun(run_id).then((r) => {
          const rep = (r.report as Record<string, unknown>) ?? null;
          setReport(rep);
          setFills((rep?.fills as Fill[] | undefined) ?? []);
          setEquityCurve((rep?.equity_curve as [number, number][] | undefined) ?? []);
        }).catch(() => {});
      } else if (ev.type === "error") {
        setRunState((s) => ({ ...s, status: `error: ${ev.message}`, paused: null }));
      } else if (ev.type === "status") {
        setRunState((s) => ({ ...s, status: ev.status }));
      }
    }, () => {
      setRunState((s) => {
        if (s.status !== "running" && s.status !== "paused") return s;
        return { ...s, status: "disconnected", paused: null };
      });
    });
  }, [backtestConfig, bps, currentLoom, errors.length, marketCatalog, nodes.length, preview, setNodes]);

  // —— Copilot 应用：把预览 loom 一次性落地画布（setNodes/setEdges 各一次，
  // structuralKey 变一次 → 编译触发一次，非循环）。base 同步为新 loom 保证 roundtrip 一致。 ——
  const applyPreview = useCallback((): Loom | null => {
    if (!preview) return null;
    const loom = preview.loom;
    setBase(loom);
    const f = loomToFlow(loom, defs);
    const nextNodes = f.nodes.map((n) => ({ ...n, data: { ...n.data,
      breakpoint: false, onToggleBreakpoint: toggleBp } })) as unknown as Node[];
    setNodes(nextNodes as never);
    setEdges(f.edges.map((e) => readableEdge(e as unknown as Edge, nextNodes)) as never);
    setBps(new Set());
    setPreview(null);
    setSelectedNodeId(null);
    setSelectedNodeIds([]);
    setConnectionError(null);
    return loom;
  }, [preview, defs, setNodes, setEdges, toggleBp]);

  const onCopilotPreview = useCallback((p: Preview) => {
    setBlueprintView("graph");
    setRightTab("copilot");
    setPreview(p);
  }, []);
  const onCopilotApply = useCallback(() => { applyPreview(); }, [applyPreview]);
  const onCopilotDiscard = useCallback(() => setPreview(null), []);
  const onCopilotApplyRun = useCallback(() => {
    const loom = applyPreview();
    if (loom) run(loom);          // 用刚落地的 loom 直接回测（不依赖异步 state）
  }, [applyPreview, run]);

  const palette = useMemo(() => {
    const groups: Record<string, NodeDef[]> = {};
    Object.values(defs).forEach((d) => (groups[d.category] ??= []).push(d));
    return Object.entries(groups).sort();
  }, [defs]);

  return (
    <div className="grid h-full min-h-0 gap-3 overflow-auto p-3 xl:grid-cols-[280px_minmax(0,1fr)_430px] xl:overflow-hidden 2xl:grid-cols-[320px_minmax(0,1fr)_460px] 2xl:p-4">
      <aside className="panel flex min-h-[420px] flex-col overflow-hidden xl:min-h-0">
        <div className="border-b border-edge/70 p-3">
          <div className="hud-label text-loom-gold">{l.workspace}</div>
          <div className="mt-2 text-sm font-semibold text-slate-100">{base.name || EMPTY.name}</div>
          <div className="mt-1 truncate font-mono text-[11px] text-slate-500">{base.id}</div>
          {loadError && (
            <div className="mt-2 truncate font-mono text-[11px] text-loom-red">{loadError}</div>
          )}
          <button onClick={() => saveBlueprint(currentLoom())
                    .then(() => listBlueprints().then(setGallery))
                    .catch((err) => setLoadError(String((err as Error).message ?? err)))}
                  className="mt-3 w-full rounded bg-loom-blue/20 px-2 py-1.5 text-xs font-semibold text-loom-blue">
            {t("save")}
          </button>
        </div>
        <div className="grid min-h-0 flex-1 grid-rows-[minmax(150px,0.42fr)_minmax(240px,0.58fr)] gap-3 overflow-hidden p-3">
          <section className="min-h-0 overflow-hidden">
            <div className="hud-label mb-2">{l.presets}</div>
            <div className="h-[calc(100%-22px)] overflow-auto pr-1">
              {gallery.map((g) => (
                <button key={`${g.source}:${g.id}`} onClick={() => load(g.id)} disabled={!defsReady}
                        className={`mb-1 block w-full rounded border px-2 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                          g.id === base.id
                            ? "border-loom-blue/50 bg-loom-blue/12 text-slate-100"
                            : "border-edge/50 bg-bg/20 text-slate-400 hover:border-loom-blue/35 hover:text-slate-100"
                        }`}>
                  <span className="block truncate font-semibold">{g.name}</span>
                  <span className="font-mono text-[10px] text-slate-600">{g.source}</span>
                </button>
              ))}
            </div>
          </section>
          <section className="min-h-0 overflow-hidden border-t border-edge/60 pt-3">
            <div className="hud-label mb-2">{l.nodeLibrary}</div>
            <div className="h-[calc(100%-22px)] overflow-auto pr-1">
              {palette.map(([cat, list]) => (
                <div key={cat} className="mb-3">
                  <div className="mb-1 font-display text-[11px] font-semibold uppercase"
                       style={{ color: CATEGORY_COLORS[cat] }}>{cat}</div>
                  <div className="grid grid-cols-1 gap-1">
                    {list.map((d) => (
                      <button key={d.type} onClick={() => addNode(d.type)} disabled={!defsReady}
                              className="rounded border border-edge/50 bg-bg/20 px-2 py-1.5 text-left font-mono text-[11px] text-slate-400 hover:border-loom-blue/35 hover:text-slate-100 disabled:opacity-40">
                        {d.type}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </aside>
      <main className="grid min-h-[900px] grid-rows-[minmax(480px,1fr)_360px] gap-3 xl:h-full xl:min-h-0 xl:grid-rows-[minmax(520px,1fr)_360px] 2xl:grid-rows-[minmax(620px,1fr)_370px]">
      <section className="panel relative overflow-hidden">
        {/* 预览态下画布只读展示 diff（onNodesChange/onConnect 挂空避免误编辑派生层）。 */}
        {blueprintView === "gate" ? (
          <GateView loom={gateLoom} selectedNodeIds={selectedNodeIds} onFocusNodes={focusProtocolNodes} />
        ) : (
        <ReactFlow key={flowKey} nodes={displayNodes} edges={displayEdges} nodeTypes={nodeTypes}
                   defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
                   connectionLineType={ConnectionLineType.SmoothStep}
                   connectionLineStyle={{ stroke: "#38bdf8", strokeWidth: 2, opacity: 0.75 }}
                   isValidConnection={preview ? undefined : isValidConnection}
                   onNodesChange={preview ? undefined : onNodesChange}
                   onEdgesChange={preview ? undefined : onEdgesChange}
                   onConnect={preview ? undefined : onConnect}
                   onEdgeContextMenu={preview ? undefined : onEdgeContextMenu}
                   onNodeDoubleClick={preview ? undefined : onNodeDoubleClick}
                   onNodeClick={(_, n) => { setSelectedNodeId(n.id); setSelectedNodeIds([n.id]); }}
                   onPaneClick={() => { setSelectedNodeId(null); setSelectedNodeIds([]); setConnectionError(null); }}
                   fitView proOptions={{ hideAttribution: true }}>
          <Background color="#1e2a44" gap={24} />
          <Controls />
        </ReactFlow>
        )}
        {preview && (
          <div className="absolute top-2 left-1/2 -translate-x-1/2 px-3 py-1 rounded
                          bg-loom-amber/20 text-loom-amber text-xs pointer-events-none">
            {t("diffPreview")}
          </div>)}
        {connectionError && !preview && blueprintView === "graph" && (
          <div className="pointer-events-none absolute bottom-3 left-1/2 max-w-[560px] -translate-x-1/2 rounded border border-loom-red/40 bg-loom-red/14 px-3 py-2 text-center text-xs font-semibold text-loom-red shadow-glow">
            {connectionError}
          </div>)}
        <div className="absolute top-3 left-3 right-3 flex items-center gap-3 pointer-events-none">
          <div>
            <div className="hud-label text-[9px]">{l.canvas}</div>
            <div className="max-w-[360px] truncate text-sm font-semibold text-slate-100">{base.name || EMPTY.name}</div>
          </div>
          <span className={`w-2 h-2 rounded-full ${errors.length ? "bg-loom-red" : "bg-loom-green"}`} />
          <span className="text-xs text-slate-400">
            {errors.length ? t("compileFail") : t("compileOk")}
          </span>
          <div className="pointer-events-auto flex items-center rounded border border-edge overflow-hidden text-xs">
            {(["gate", "graph"] as const).map((v) => (
              <button key={v} onClick={() => setBlueprintView(v)}
                      disabled={!!preview && v === "gate"}
                      className={`px-2 py-1 ${blueprintView === v
                        ? "bg-loom-blue/18 text-loom-blue" : "text-slate-500 hover:text-slate-300"}
                        disabled:cursor-not-allowed disabled:opacity-35`}>
                {v === "gate" ? t("gateView") : t("graphView")}
              </button>))}
          </div>
          <button type="button" onClick={organizeLayout}
                  disabled={!!preview || !nodes.length}
                  className="pointer-events-auto rounded border border-edge bg-bg/45 px-2 py-1 text-xs text-slate-300 transition-colors hover:border-loom-blue/45 hover:text-loom-blue disabled:cursor-not-allowed disabled:opacity-35">
            {l.organizeLayout}
          </button>
          {runState.id && (
            <span className="text-xs text-loom-blue font-mono">
              bar {runState.bar ?? "-"} · eq {runState.equity?.toFixed(2) ?? "-"} · {runState.status}
              {runState.status === "done" && (
                <a className="pointer-events-auto underline ml-2"
                   href={`#/terminal?run=${runState.id}`}>→ {t("terminal")}</a>)}
            </span>)}
          <div className="ml-auto" />
        </div>
      </section>
      <BacktestLab
        blueprint={currentLoom()}
        catalog={marketCatalog}
        config={backtestConfig}
        onConfigChange={updateBacktestConfig}
        candles={candles}
        fills={fills}
        equityCurve={equityCurve}
        cursor={runState.bar}
        status={runState.status}
        disabled={!!errors.length || !nodes.length || !!preview}
        onRun={() => run()}
        onCommand={(cmd) => sock.current?.send(cmd)}
      />
      </main>
      <aside className="panel mt-0 flex min-h-[620px] flex-col overflow-hidden xl:min-h-0">
        <div className="flex items-center gap-2 border-b border-edge/70 p-3">
          <div className="mr-auto">
            <div className="hud-label text-loom-blue">{l.activeBlueprint}</div>
            <div className="max-w-[260px] truncate text-sm font-semibold text-slate-100">{base.name || EMPTY.name}</div>
          </div>
          {(["copilot", "inspect"] as const).map((tab) => (
            <button key={tab} type="button" onClick={() => setRightTab(tab)}
                    className={`rounded border px-3 py-1.5 text-xs font-semibold transition-colors ${
                      rightTab === tab
                        ? "border-loom-blue/50 bg-loom-blue/18 text-loom-blue"
                        : "border-edge/70 bg-bg/20 text-slate-500 hover:text-slate-200"
                    }`}>
              {tab === "copilot" ? l.copilotTab : l.inspectTab}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-hidden p-3">
          {rightTab === "copilot" ? (
          <CopilotPanel getCurrentLoom={currentLoom}
                        onPreview={onCopilotPreview} onApply={onCopilotApply}
                        onApplyRun={onCopilotApplyRun} onDiscard={onCopilotDiscard}
                        preview={preview} selectedNodeId={selectedNodeId}
                        runId={runState.id} report={report} />
          ) : (
            <div className="h-full overflow-auto pr-1">
              <div className="hud-label mb-2">{l.inspectTitle}</div>
              <div className="space-y-2">
                <CertPanel cert={cert} />
                <ErrorPanel errors={errors} onFocus={(id) => focusProtocolNodes([id])} />
                <PausedInspector ev={runState.paused ?? null}
                                 onCmd={(c) => sock.current?.send(c)} />
                <div className="rounded border border-edge/70 bg-bg/25 p-2 text-[10px] text-slate-500">
                  {t("breakpointHint")}
                </div>
              </div>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
