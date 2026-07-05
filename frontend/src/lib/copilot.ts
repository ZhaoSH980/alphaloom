// frontend/src/lib/copilot.ts —— Copilot 元 Agent API 调用（D3 Task 9）
// 后端契约见 api/app.py + copilot/blueprint.py：
//   POST /api/copilot/blueprint  {nl}                 -> {loom, notes}
//   POST /api/copilot/explain    {blueprint}          -> {explanation}
//   POST /api/copilot/optimize   {blueprint, report}  -> {loom, diff, notes}
// 失败（生成不出可编译图 / 无 LLM 客户端）→ 非 2xx，j() 抛带 status/body 的 Error。
import type { Loom, LoomEdge, LoomNode } from "./loom";

async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  if (!res.ok)
    throw Object.assign(new Error(`HTTP ${res.status}`),
      { status: res.status, body: await res.text() });
  return res.json();
}

function post<T>(url: string, body: unknown): Promise<T> {
  return j<T>(fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

// 后端 diff_blueprints 的结构化 diff（copilot/blueprint.py::diff_blueprints）。
export interface LoomDiff {
  added: LoomNode[];
  removed: LoomNode[];
  changed: { id: string; before: LoomNode; after: LoomNode }[];
  added_edges: LoomEdge[];
  removed_edges: LoomEdge[];
}

export interface BlueprintResult {
  loom: Loom;
  notes: string[];
}
export interface OptimizeResult {
  loom: Loom;
  diff: LoomDiff;
  notes: string[];
}

export const postCopilotBlueprint = (nl: string) =>
  post<BlueprintResult>("/api/copilot/blueprint", { nl });

export const postExplain = (blueprint: Loom) =>
  post<{ explanation: string }>("/api/copilot/explain", { blueprint });

export const postOptimize = (blueprint: Loom, report?: Record<string, unknown>) =>
  post<OptimizeResult>("/api/copilot/optimize", { blueprint, report: report ?? {} });

// —— 前端侧 diff 计算（对比 before/after 两个 loom）——
// 后端 optimize 已返回 diff；blueprint 生成走的是"空画布→新图"，前端复用同一算法
// 对比"当前画布 loom"与"新生成 loom"，让用户看到将新增/删除/改动什么再决定应用。
// 语义与后端 diff_blueprints 对齐（节点 by id，改动 = type 或 params 不同；边 by from/to/feedback）。
type DiffKind = "added" | "removed" | "changed";

function edgeKey(e: LoomEdge): string {
  return `${e.from}|${e.to}|${e.feedback ? 1 : 0}`;
}

export interface ComputedDiff {
  /** node id -> diff 类别（供画布高亮：added 绿 / removed 红 / changed 黄）。 */
  nodeKind: Record<string, DiffKind>;
  addedEdges: Set<string>;   // edgeKey 集合
  removedEdges: Set<string>;
  counts: { added: number; removed: number; changed: number;
            addedEdges: number; removedEdges: number };
}

export function computeDiff(before: Loom, after: Loom): ComputedDiff {
  const beforeNodes = new Map(before.nodes.map((n) => [n.id, n]));
  const afterNodes = new Map(after.nodes.map((n) => [n.id, n]));
  const nodeKind: Record<string, DiffKind> = {};

  for (const id of afterNodes.keys())
    if (!beforeNodes.has(id)) nodeKind[id] = "added";
  for (const id of beforeNodes.keys())
    if (!afterNodes.has(id)) nodeKind[id] = "removed";
  for (const id of afterNodes.keys()) {
    const b = beforeNodes.get(id);
    const a = afterNodes.get(id);
    if (!b || !a) continue;
    if (b.type !== a.type || JSON.stringify(b.params) !== JSON.stringify(a.params))
      nodeKind[id] = "changed";
  }

  const beforeEdges = new Set(before.edges.map(edgeKey));
  const afterEdges = new Set(after.edges.map(edgeKey));
  const addedEdges = new Set([...afterEdges].filter((k) => !beforeEdges.has(k)));
  const removedEdges = new Set([...beforeEdges].filter((k) => !afterEdges.has(k)));

  const kinds = Object.values(nodeKind);
  return {
    nodeKind, addedEdges, removedEdges,
    counts: {
      added: kinds.filter((k) => k === "added").length,
      removed: kinds.filter((k) => k === "removed").length,
      changed: kinds.filter((k) => k === "changed").length,
      addedEdges: addedEdges.size,
      removedEdges: removedEdges.size,
    },
  };
}
