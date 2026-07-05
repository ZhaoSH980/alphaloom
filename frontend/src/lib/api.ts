// frontend/src/lib/api.ts
import type { Loom, NodeDef } from "./loom";

async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status, body: await res.text() });
  return res.json();
}
export const getNodes = () => j<NodeDef[]>(fetch("/api/nodes"));
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
export const getRun = (id: string) => j<Record<string, any>>(fetch(`/api/runs/${id}`));
export const listRuns = () => j<Record<string, any>[]>(fetch("/api/runs"));
export const getCandles = (inst: string, bar: string, limit = 5000) =>
  j<{ ts: number; open: number; high: number; low: number; close: number; volume: number }[]>(
    fetch(`/api/market/candles?inst=${encodeURIComponent(inst)}&bar=${bar}&limit=${limit}`));
export const getTrace = (runId: string, nodeId?: string, eventIdx?: number, limit = 200) => {
  const q = new URLSearchParams();
  if (nodeId) q.set("node_id", nodeId);
  if (eventIdx !== undefined) q.set("event_idx", String(eventIdx));
  q.set("limit", String(limit));
  return j<Record<string, any>[]>(fetch(`/api/runs/${runId}/trace?${q}`));
};
