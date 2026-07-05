// frontend/src/components/GenealogyTree.tsx —— 进化谱系树（React Flow）
// 后端 Genealogy.to_dict：{nodes:[{id,gen,parent_id,mutation_summary,fitness,
// compile_status,blueprint_json,survived,error}], winner{id,train_fitness,valid_fitness,
// generalization_gap,train_summary,valid_summary}, param_only, population, generations}。
//
// 布局：gen 分层（x 按 gen、y 按代内序）；parent_id 连边。compile_status 上色
// （ok/repaired/stillborn/runtime_error 各异）；winner 金色高亮；survived 加实边框。
// 诚实：winner 的 valid num_trades 摆在 valid_fitness 旁（T5 审查：区分"没交易"
// vs"交易亏光"——0 交易 = 0 分是零证据，不是真亏）。
import { useMemo } from "react";
import { ReactFlow, Background, Controls, Handle, Position, type Edge, type Node } from "@xyflow/react";
import type { Genealogy, GenealogyNode, CompileStatus } from "../lib/eval";
import { useLang } from "../lib/i18n";

// compile_status → 颜色（诚实：失败态醒目，不藏）
const STATUS_COLOR: Record<CompileStatus, { border: string; badge: string }> = {
  ok: { border: "#34d399", badge: "bg-loom-green/20 text-loom-green" },
  repaired: { border: "#38bdf8", badge: "bg-loom-blue/20 text-loom-blue" },
  stillborn: { border: "#64748b", badge: "bg-slate-600/30 text-slate-400" },
  runtime_error: { border: "#ef4444", badge: "bg-loom-red/20 text-loom-red" },
};

const GEN_X = 240, ROW_Y = 110;

// 谱系 → React Flow nodes/edges（纯函数，可单测）。
export function genealogyToFlow(g: Genealogy, winnerId: string | null):
    { nodes: Node[]; edges: Edge[] } {
  const byGen = new Map<number, GenealogyNode[]>();
  for (const n of g.nodes ?? []) {
    const arr = byGen.get(n.gen) ?? [];
    arr.push(n);
    byGen.set(n.gen, arr);
  }
  const rowIndex = new Map<string, number>();
  for (const [, arr] of byGen) arr.forEach((n, i) => rowIndex.set(n.id, i));

  const nodes: Node[] = (g.nodes ?? []).map((n) => ({
    id: n.id,
    position: { x: n.gen * GEN_X + 20, y: (rowIndex.get(n.id) ?? 0) * ROW_Y + 20 },
    data: { gnode: n, isWinner: n.id === winnerId },
    type: "genealogyNode",
    draggable: false,
  }));
  const nodeIds = new Set((g.nodes ?? []).map((n) => n.id));
  const edges: Edge[] = (g.nodes ?? [])
    .filter((n) => n.parent_id != null && nodeIds.has(n.parent_id))
    .map((n) => ({
      id: `${n.parent_id}->${n.id}`, source: n.parent_id as string, target: n.id,
      animated: n.survived, style: { stroke: "#334155" },
    }));
  return { nodes, edges };
}

function GenealogyNodeCard({ data }: { data: { gnode: GenealogyNode; isWinner: boolean } }) {
  const { gnode: n, isWinner } = data;
  const sc = STATUS_COLOR[n.compile_status] ?? STATUS_COLOR.stillborn;
  return (
    <div className="rounded px-2 py-1.5 text-[10px] w-[200px] bg-panel"
         style={{ border: `1px solid ${isWinner ? "#f59e0b" : sc.border}`,
                  boxShadow: isWinner ? "0 0 10px 1px rgba(245,158,11,0.5)" : undefined }}>
      {/* React Flow 边靠 Handle 锚定：自定义节点必须自渲染 target/source Handle，
          否则 parent→child 连线无锚点、整棵树只剩散落的框（T10 live 走查抓到）。
          布局左→右（gen 递增），故 target 在左（接父）、source 在右（连子）。 */}
      <Handle type="target" position={Position.Left} isConnectable={false}
              style={{ opacity: 0, width: 1, height: 1, border: "none", minWidth: 0, minHeight: 0 }} />
      <Handle type="source" position={Position.Right} isConnectable={false}
              style={{ opacity: 0, width: 1, height: 1, border: "none", minWidth: 0, minHeight: 0 }} />
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="font-mono text-slate-300">{n.id}</span>
        {isWinner && <span className="px-1 rounded bg-loom-gold/25 text-loom-gold text-[9px]">★</span>}
        <span className={`px-1 rounded text-[9px] ${sc.badge}`}>{n.compile_status}</span>
      </div>
      <div className="text-slate-500 leading-tight mt-0.5 line-clamp-2">{n.mutation_summary}</div>
      <div className="flex items-center gap-2 mt-0.5">
        <span className="text-slate-600">fit</span>
        <span className={`font-mono ${n.fitness == null ? "text-slate-600"
          : n.fitness > 0 ? "text-loom-green" : n.fitness < 0 ? "text-loom-red" : "text-slate-400"}`}>
          {n.fitness == null ? "—" : n.fitness.toFixed(2)}
        </span>
        {n.survived && <span className="text-loom-green text-[9px]">survived</span>}
      </div>
      {n.error && <div className="text-loom-red text-[9px] mt-0.5 line-clamp-2">{n.error}</div>}
    </div>
  );
}

const nodeTypes = { genealogyNode: GenealogyNodeCard };

export default function GenealogyTree({ genealogy }: { genealogy: Genealogy }) {
  const { t } = useLang();
  const w = genealogy.winner as Genealogy["winner"] & { id?: string };
  const winnerId = typeof w?.id === "string" ? w.id : null;
  const { nodes, edges } = useMemo(
    () => genealogyToFlow(genealogy, winnerId), [genealogy, winnerId]);

  if (!nodes.length)
    return <div className="panel p-3 text-xs text-slate-500">{t("genealogyEmpty")}</div>;

  // winner valid 成交数：区分"0 交易=0 分零证据" vs"交易亏光"（T5 审查）。
  const validTrades = winnerId
    ? Number((w as { valid_summary?: Record<string, unknown> }).valid_summary?.num_trades ?? NaN)
    : NaN;

  return (
    <div className="panel p-3 space-y-2">
      <div className="flex items-baseline gap-3 flex-wrap">
        <div className="hud-label text-loom-violet">{t("evolutionTitle")}</div>
        <span className="text-[10px] text-slate-500">{t("evolutionHint")}</span>
        <span className="text-[10px] text-slate-500 font-mono ml-auto">
          pop {genealogy.population} · gen {genealogy.generations}
          {genealogy.param_only && <span className="ml-1 text-loom-amber">· {t("paramOnly")}</span>}
        </span>
      </div>

      {/* winner 摘要：train/valid 适应度 + valid 成交数（诚实区分零交易 vs 亏光） */}
      {winnerId && (
        <div className="panel p-2 bg-void/40 flex items-center gap-3 flex-wrap text-[10px]">
          <span className="px-1.5 py-0.5 rounded bg-loom-gold/20 text-loom-gold">
            ★ {t("winner")} {winnerId}
          </span>
          <span className="text-slate-500">{t("trainFitness")}
            <span className="ml-1 font-mono text-slate-300">
              {typeof w.train_fitness === "number" ? w.train_fitness.toFixed(2) : "—"}
            </span>
          </span>
          <span className="text-slate-500">{t("validFitness")}
            <span className={`ml-1 font-mono ${typeof w.valid_fitness === "number"
              ? (w.valid_fitness >= 0 ? "text-loom-green" : "text-loom-red") : "text-slate-400"}`}>
              {typeof w.valid_fitness === "number" ? w.valid_fitness.toFixed(2) : "—"}
            </span>
          </span>
          <span className="text-slate-500">{t("validTrades")}
            <span className="ml-1 font-mono text-slate-300">
              {Number.isFinite(validTrades) ? validTrades : "—"}
            </span>
          </span>
          {typeof w.generalization_gap === "number" && (
            <span className="text-slate-500">{t("generalizationGap")}
              <span className={`ml-1 font-mono ${w.generalization_gap > 0
                ? "text-loom-red" : "text-loom-green"}`}>
                {w.generalization_gap.toFixed(2)}
              </span>
            </span>)}
        </div>)}

      <div style={{ height: 340 }} className="rounded border border-edge/60">
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes}
                   fitView proOptions={{ hideAttribution: true }}
                   nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}>
          <Background color="#1e2a44" gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}
