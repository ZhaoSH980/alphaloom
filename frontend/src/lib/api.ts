// frontend/src/lib/api.ts
import type { Loom, NodeDef } from "./loom";
import type { MarketWindow } from "./backtestConfig";

export type RuntimeMode = "offline" | "live" | "none";
export type RuntimeStatus = { llm_mode: RuntimeMode; model: string | null };

async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status, body: await res.text() });
  return res.json();
}
export const getNodes = () => j<NodeDef[]>(fetch("/api/nodes"));
export const getStatus = () =>
  j<RuntimeStatus>(fetch("/api/status"));
export const setRuntimeMode = (mode: RuntimeMode) =>
  j<RuntimeStatus>(fetch("/api/runtime-mode", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }) }));
export const compileLoom = (blueprint: Loom, bar = "1m") =>
  j<{ ok: boolean; errors: { code: string; message: string; node_id?: string; port?: string; fix_hint?: string }[];
      certificate: Record<string, unknown> | null; order: string[] }>(
    fetch("/api/compile", { method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ blueprint, bar }) }));
export const listBlueprints = () =>
  j<{ id: string; name: string; meta: Record<string, unknown>; source: string }[]>(fetch("/api/blueprints"));
export const getBlueprint = (id: string) => j<Loom>(fetch(`/api/blueprints/${id}`));
export const saveBlueprint = (blueprint: Loom) =>
  j<{ id: string }>(fetch("/api/blueprints", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ blueprint }) }));
export const startRun = (body: Record<string, unknown>) =>
  j<{ run_id: string }>(fetch("/api/runs", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
export const startLive = (body: Record<string, unknown>) =>
  j<{ session_id: string; run_id: string }>(fetch("/api/live", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
export const stopLive = (sessionId: string) =>
  j<{ session_id: string; status: string }>(
    fetch(`/api/live/${sessionId}/stop`, { method: "POST" }));
export const getRun = (id: string) => j<Record<string, any>>(fetch(`/api/runs/${id}`));
export const listRuns = () => j<Record<string, any>[]>(fetch("/api/runs"));
export const getMarketCatalog = () =>
  j<MarketWindow[]>(fetch("/api/market/catalog"));
export const getCandles = (
  inst: string,
  bar: string,
  opts: number | { start?: number; end?: number; limit?: number } = 5000,
) => {
  const limit = typeof opts === "number" ? opts : opts.limit ?? 5000;
  const q = new URLSearchParams();
  q.set("inst", inst);
  q.set("bar", bar);
  q.set("limit", String(limit));
  if (typeof opts !== "number" && opts.start !== undefined) q.set("start", String(opts.start));
  if (typeof opts !== "number" && opts.end !== undefined) q.set("end", String(opts.end));
  return j<{ ts: number; open: number; high: number; low: number; close: number; volume: number }[]>(
    fetch(`/api/market/candles?${q.toString()}`));
};
export const getTrace = (runId: string, nodeId?: string, eventIdx?: number, limit = 200) => {
  const q = new URLSearchParams();
  if (nodeId) q.set("node_id", nodeId);
  if (eventIdx !== undefined) q.set("event_idx", String(eventIdx));
  q.set("limit", String(limit));
  return j<Record<string, any>[]>(fetch(`/api/runs/${runId}/trace?${q}`));
};
export const getLiveAnalysis = (sessionId: string, limit = 200) =>
  j<Record<string, any>[]>(fetch(`/api/live/${sessionId}/analysis?limit=${limit}`));

// —— Eval / Evolve 端点（D4-T6，全 POST，返回对应 to_dict JSON，无 envelope）——
import type { LadderReport, Board, AblationReport, Scorecard, Genealogy } from "./eval";

const post = <T>(url: string, body: Record<string, unknown>) =>
  j<T>(fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body) }));

/** 保真度阶梯：从一个已完成 run 取 fills+candles 重放（零 LLM）。 */
export const evalFidelity = (runId: string, opts: Record<string, unknown> = {}) =>
  post<LadderReport>("/api/eval/fidelity", { run_id: runId, ...opts });

/** 基线排行榜：三基线 + 可选指定蓝图，同窗对比。 */
export const evalLeaderboard = (body: Record<string, unknown>) =>
  post<Board>("/api/eval/leaderboard", body);

/** 委员会消融三臂：full / no_risk_officer / no_rag（LLM 蓝图须 offline）。 */
export const evalAblation = (body: Record<string, unknown>) =>
  post<AblationReport>("/api/eval/ablation", body);

/** 蓝图记分卡：把已算好的证据碎片拼成权威综合分（评分实现只在后端）。 */
export const evalScorecard = (body: Record<string, unknown>) =>
  post<Scorecard>("/api/eval/scorecard", body);

/** 进化实验室：LLM 变异算子 + 编译守门 + 谱系树（规模硬锁定）。 */
export const evolve = (body: Record<string, unknown>) =>
  post<Genealogy>("/api/evolve", body);
