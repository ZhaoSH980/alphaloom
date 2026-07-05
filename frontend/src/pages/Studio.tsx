// frontend/src/pages/Studio.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, Background, Controls, addEdge, useEdgesState, useNodesState,
         type Connection, type Edge, type Node } from "@xyflow/react";
import NodeCard from "../components/NodeCard";
import ErrorPanel, { type CompileError } from "../components/ErrorPanel";
import CertPanel from "../components/CertPanel";
import PausedInspector from "../components/PausedInspector";
import { compileLoom, getBlueprint, getNodes, listBlueprints, saveBlueprint, startRun } from "../lib/api";
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
  }, [nodes.length, edges, currentLoom, setNodes]);

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

  const run = async () => {
    const { run_id } = await startRun({ blueprint: currentLoom(), inst: "BTC-USDT-SWAP",
      bar: "1m", playback_ms: 15, ws_wait_ms: 300, breakpoints: [...bps] });
    setRunState({ id: run_id, status: "running" });
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
      } else if (ev.type === "error") {
        setRunState((s) => ({ ...s, status: `error: ${ev.message}`, paused: null }));
      } else if (ev.type === "status") {
        setRunState((s) => ({ ...s, status: ev.status }));
      }
    });
  };

  const palette = useMemo(() => {
    const groups: Record<string, NodeDef[]> = {};
    Object.values(defs).forEach((d) => (groups[d.category] ??= []).push(d));
    return Object.entries(groups).sort();
  }, [defs]);

  return (
    <div className="h-full grid grid-cols-[200px_1fr_280px] gap-2 p-2">
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
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes}
                   onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                   onConnect={onConnect} onEdgeContextMenu={onEdgeContextMenu}
                   onNodeDoubleClick={onNodeDoubleClick} fitView proOptions={{ hideAttribution: true }}>
          <Background color="#1e2a44" gap={24} />
          <Controls />
        </ReactFlow>
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
          <button onClick={run} disabled={!!errors.length || !nodes.length}
                  className="pointer-events-auto ml-auto px-3 py-1 text-xs rounded bg-loom-gold/20 text-loom-gold disabled:opacity-30">
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
    </div>
  );
}
