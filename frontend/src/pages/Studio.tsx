// frontend/src/pages/Studio.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, Background, Controls, addEdge, useEdgesState, useNodesState,
         type Connection, type Edge, type Node } from "@xyflow/react";
import NodeCard from "../components/NodeCard";
import ErrorPanel, { type CompileError } from "../components/ErrorPanel";
import CertPanel from "../components/CertPanel";
import PausedInspector from "../components/PausedInspector";
import CopilotPanel, { type Preview } from "../components/CopilotPanel";
import { compileLoom, getBlueprint, getNodes, getRun, listBlueprints, saveBlueprint, startRun } from "../lib/api";
import { openRunSocket } from "../lib/ws";
import { CATEGORY_COLORS, flowToLoom, loomToFlow, nextNodeId,
         type Loom, type NodeDef } from "../lib/loom";
import { useLang } from "../lib/i18n";

const EMPTY: Loom = { id: "untitled", name: "Untitled", nodes: [], edges: [], meta: {} };
const nodeTypes = { loomNode: NodeCard };

export default function Studio() {
  const { t } = useLang();
  const [defs, setDefs] = useState<Record<string, NodeDef>>({});
  const [gallery, setGallery] = useState<{ id: string; name: string; source: string }[]>([]);
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
  // Run 模式：backtest（默认）| replay（加速回放，走真实 LLM/录制）。
  // 纯 run-time 参数——不进 structuralKey（切模式不触发重编译，防 D2 编译循环）。
  const [mode, setMode] = useState<"backtest" | "replay">("backtest");
  // Copilot：预览态 + 选中节点 + 最近报告（optimize 读它）。
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [report, setReport] = useState<Record<string, unknown> | null>(null);

  const toggleBp = useCallback((id: string) => {
    setBps((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }, []);

  useEffect(() => {
    getNodes().then((ns) => setDefs(Object.fromEntries(ns.map((n) => [n.type, n]))));
    listBlueprints().then(setGallery);
    return () => sock.current?.close();
  }, []);

  const currentLoom = useCallback(() =>
    flowToLoom(nodes as never, edges as never, base), [nodes, edges, base]);

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
    if (!preview) return edges;
    const pf = loomToFlow(preview.loom, defs);
    // 预览态下展示新图的边（新增绿虚线），叠加当前边。去重按 handle 三元组。
    const seen = new Set(edges.map((e) => `${e.source}|${e.sourceHandle}|${e.target}|${e.targetHandle}`));
    const addedEdges = pf.edges
      .filter((e) => !seen.has(`${e.source}|${e.sourceHandle}|${e.target}|${e.targetHandle}`))
      .map((e) => ({ ...e, animated: true,
        style: { stroke: "#34d399", strokeDasharray: "5 3" } }));
    return [...edges, ...addedEdges] as typeof edges;
  }, [preview, edges, defs]);

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
  useEffect(() => {
    if (!nodes.length) { setErrors([]); setCert(null); return; }
    const h = setTimeout(() => {
      compileLoom(currentLoom()).then((r) => {
        setErrors(r.errors);
        setCert(r.ok ? r.certificate : null);
        const bad = new Set(r.errors.map((e) => e.node_id).filter(Boolean));
        setNodes((ns) => ns.map((n) => ({ ...n,
          data: { ...n.data, blocked: bad.has(n.id) } })));
      }).catch(() => {});
    }, 500);
    return () => clearTimeout(h);
    // 只以 structuralKey 为闸门；currentLoom()/nodes 经闭包取最新值。
    // 切勿把 currentLoom 加回依赖——它身份每次 setNodes(blocked/active) 都变，会造成 500ms 无限编译。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structuralKey, setNodes]);

  const load = async (id: string) => {
    if (nodes.length && !confirm("覆盖当前画布?")) return;
    const loom = await getBlueprint(id);
    setBase(loom);
    const f = loomToFlow(loom, defs);
    setNodes(f.nodes.map((n) => ({ ...n, data: { ...n.data,
      breakpoint: false, onToggleBreakpoint: toggleBp } })) as never);
    setEdges(f.edges.map((e) => ({ ...e, animated: e.data.feedback,
      style: e.data.feedback ? { strokeDasharray: "6 3" } : undefined })) as never);
    setBps(new Set());
  };

  useEffect(() => {
    setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, breakpoint: bps.has(n.id),
      onToggleBreakpoint: toggleBp } })));
  }, [bps, setNodes, toggleBp]);

  const addNode = (type: string) => {
    const def = defs[type];
    const id = nextNodeId(new Set(nodes.map((n) => n.id)), type);
    setNodes((ns) => [...ns, { id, type: "loomNode",
      position: { x: 120 + Math.random() * 240, y: 120 + Math.random() * 160 },
      data: { def, params: {}, onToggleBreakpoint: toggleBp } } as never]);
  };

  const onConnect = useCallback((c: Connection) =>
    setEdges((es) => addEdge({ ...c, data: { feedback: false } }, es)), [setEdges]);

  const onEdgeContextMenu = useCallback((e: React.MouseEvent, edge: Edge) => {
    e.preventDefault();
    setEdges((es) => es.map((x) => x.id === edge.id
      ? { ...x, data: { feedback: !(x.data as { feedback?: boolean } | undefined)?.feedback },
          animated: !(x.data as { feedback?: boolean } | undefined)?.feedback,
          style: !(x.data as { feedback?: boolean } | undefined)?.feedback ? { strokeDasharray: "6 3" } : undefined }
      : x));
  }, [setEdges]);

  const onNodeDoubleClick = useCallback((_: unknown, node: Node) => {
    const txt = prompt("params JSON", JSON.stringify((node.data as { params?: unknown }).params ?? {}));
    if (txt == null) return;
    try {
      const p = JSON.parse(txt);
      setNodes((ns) => ns.map((n) => n.id === node.id
        ? { ...n, data: { ...n.data, params: p } } : n));
    } catch { alert("bad JSON"); }
  }, [setNodes]);

  const run = useCallback(async (loom?: Loom) => {
    sock.current?.close();               // 关旧连接，防重复 Run 泄漏 WS（T6 审查 Important）
    // replay 加速：playback_ms 更小（4 vs 15），mode 透传后端（RunIn.mode）。
    const replay = mode === "replay";
    const { run_id } = await startRun({ blueprint: loom ?? currentLoom(), inst: "BTC-USDT-SWAP",
      bar: "1m", playback_ms: replay ? 4 : 15, ws_wait_ms: 300, breakpoints: [...bps],
      mode });
    setRunState({ id: run_id, status: "running" });
    setReport(null);
    sock.current = openRunSocket(run_id, (ev) => {
      if (ev.type === "bar") {
        setRunState((s) => ({ ...s, bar: ev.idx, equity: ev.equity }));
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
        getRun(run_id).then((r) => setReport((r.report as Record<string, unknown>) ?? null))
          .catch(() => {});
      } else if (ev.type === "error") {
        setRunState((s) => ({ ...s, status: `error: ${ev.message}`, paused: null }));
      } else if (ev.type === "status") {
        setRunState((s) => ({ ...s, status: ev.status }));
      }
    });
  }, [currentLoom, bps, setNodes, mode]);

  // —— Copilot 应用：把预览 loom 一次性落地画布（setNodes/setEdges 各一次，
  // structuralKey 变一次 → 编译触发一次，非循环）。base 同步为新 loom 保证 roundtrip 一致。 ——
  const applyPreview = useCallback((): Loom | null => {
    if (!preview) return null;
    const loom = preview.loom;
    setBase(loom);
    const f = loomToFlow(loom, defs);
    setNodes(f.nodes.map((n) => ({ ...n, data: { ...n.data,
      breakpoint: false, onToggleBreakpoint: toggleBp } })) as never);
    setEdges(f.edges.map((e) => ({ ...e, animated: e.data.feedback,
      style: e.data.feedback ? { strokeDasharray: "6 3" } : undefined })) as never);
    setBps(new Set());
    setPreview(null);
    return loom;
  }, [preview, defs, setNodes, setEdges, toggleBp]);

  const onCopilotPreview = useCallback((p: Preview) => setPreview(p), []);
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
    <div className="h-full grid grid-cols-[170px_1fr_260px_290px] gap-2 p-2">
      <aside className="panel p-2 overflow-auto space-y-3">
        {palette.map(([cat, list]) => (
          <div key={cat}>
            <div className="hud-label mb-1" style={{ color: CATEGORY_COLORS[cat] }}>{cat}</div>
            {list.map((d) => (
              <button key={d.type} onClick={() => addNode(d.type)}
                      className="block w-full text-left text-xs px-2 py-1 rounded hover:bg-edge/60">
                {d.type}
              </button>
            ))}
          </div>
        ))}
        <div className="border-t border-edge pt-2">
          <div className="hud-label mb-1">{t("gallery")}</div>
          {gallery.map((g) => (
            <button key={g.id} onClick={() => load(g.id)}
                    className="block w-full text-left text-xs px-2 py-1 rounded hover:bg-edge/60">
              {g.name} <span className="text-slate-600">({g.source})</span>
            </button>
          ))}
        </div>
      </aside>
      <section className="panel relative">
        {/* 预览态下画布只读展示 diff（onNodesChange/onConnect 挂空避免误编辑派生层）。 */}
        <ReactFlow nodes={displayNodes} edges={displayEdges} nodeTypes={nodeTypes}
                   onNodesChange={preview ? undefined : onNodesChange}
                   onEdgesChange={preview ? undefined : onEdgesChange}
                   onConnect={preview ? undefined : onConnect}
                   onEdgeContextMenu={preview ? undefined : onEdgeContextMenu}
                   onNodeDoubleClick={preview ? undefined : onNodeDoubleClick}
                   onNodeClick={(_, n) => setSelectedNodeId(n.id)}
                   onPaneClick={() => setSelectedNodeId(null)}
                   fitView proOptions={{ hideAttribution: true }}>
          <Background color="#1e2a44" gap={24} />
          <Controls />
        </ReactFlow>
        {preview && (
          <div className="absolute top-2 left-1/2 -translate-x-1/2 px-3 py-1 rounded
                          bg-loom-amber/20 text-loom-amber text-xs pointer-events-none">
            {t("diffPreview")}
          </div>)}
        <div className="absolute top-2 left-2 right-2 flex items-center gap-3 pointer-events-none">
          <span className={`w-2 h-2 rounded-full ${errors.length ? "bg-loom-red" : "bg-loom-green"}`} />
          <span className="text-xs text-slate-400">
            {errors.length ? t("compileFail") : t("compileOk")}
          </span>
          {runState.id && (
            <span className="text-xs text-loom-blue font-mono">
              bar {runState.bar ?? "-"} · eq {runState.equity?.toFixed(2) ?? "-"} · {runState.status}
              {runState.status === "done" && (
                <a className="pointer-events-auto underline ml-2"
                   href={`#/terminal?run=${runState.id}`}>→ {t("terminal")}</a>)}
            </span>)}
          <div className="pointer-events-auto ml-auto flex items-center rounded border border-edge overflow-hidden text-xs">
            {(["backtest", "replay"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)}
                      className={`px-2 py-1 ${mode === m
                        ? "bg-loom-violet/20 text-loom-violet" : "text-slate-500 hover:text-slate-300"}`}>
                {m === "backtest" ? t("modeBacktest") : `⏩ ${t("modeReplay")}`}
              </button>))}
          </div>
          <button onClick={() => run()} disabled={!!errors.length || !nodes.length || !!preview}
                  className="pointer-events-auto px-3 py-1 text-xs rounded bg-loom-gold/20 text-loom-gold disabled:opacity-30">
            ▶ {t("run")}
          </button>
        </div>
      </section>
      <aside className="space-y-2 overflow-auto">
        <CertPanel cert={cert} />
        <ErrorPanel errors={errors} onFocus={() => {}} />
        <PausedInspector ev={runState.paused ?? null}
                         onCmd={(c) => sock.current?.send(c)} />
        <div className="panel p-2 text-[10px] text-slate-500">{t("breakpointHint")}</div>
        <button onClick={() => saveBlueprint(currentLoom()).then(() => listBlueprints().then(setGallery))}
                className="w-full px-2 py-1 text-xs rounded bg-loom-blue/20 text-loom-blue">
          {t("save")}
        </button>
      </aside>
      <aside className="min-h-0 overflow-hidden">
        <CopilotPanel getCurrentLoom={currentLoom}
                      onPreview={onCopilotPreview} onApply={onCopilotApply}
                      onApplyRun={onCopilotApplyRun} onDiscard={onCopilotDiscard}
                      preview={preview} selectedNodeId={selectedNodeId}
                      runId={runState.id} report={report} />
      </aside>
    </div>
  );
}
