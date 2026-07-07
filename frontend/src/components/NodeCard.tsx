// frontend/src/components/NodeCard.tsx
import { useMemo, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { CATEGORY_COLORS, PIN_COLORS, type NodeDef, type PinType } from "../lib/loom";

export interface NodeCardData {
  def: NodeDef; params: Record<string, unknown>;
  active?: boolean; blocked?: boolean; breakpoint?: boolean;
  diff?: "added" | "removed" | "changed";   // Copilot diff 预览高亮（绿/红/黄）
  onToggleBreakpoint?: (id: string) => void;
}

const DIFF_RING: Record<NonNullable<NodeCardData["diff"]>, string> = {
  added: "0 0 0 2px #34d399, 0 0 14px 3px rgba(52,211,153,0.55)",
  removed: "0 0 0 2px #ef4444, 0 0 14px 3px rgba(239,68,68,0.55)",
  changed: "0 0 0 2px #fbbf24, 0 0 14px 3px rgba(251,191,36,0.55)",
};

function summarizeParams(params: Record<string, unknown>): string {
  const keys = Object.keys(params);
  if (keys.length === 0) return "no params";
  const preview = keys.slice(0, 3).join(", ");
  return `${keys.length} params - ${preview}${keys.length > 3 ? ", ..." : ""}`;
}

function Pin({ side, port, pin, idx }: { side: "in" | "out"; port: string; pin: PinType; idx: number }) {
  const y = 34 + idx * 18;
  return (
    <Handle id={`${side}:${port}`} type={side === "in" ? "target" : "source"}
            position={side === "in" ? Position.Left : Position.Right}
            style={{ top: y, background: PIN_COLORS[pin], width: 9, height: 9,
                     border: pin === "risk_stamped_signal" ? "2px solid #f59e0b" : "1px solid #0b1020" }}>
      <span className={`absolute text-[9px] text-slate-400 ${side === "in" ? "left-3" : "right-3"}`}
            style={{ top: -6, whiteSpace: "nowrap" }}>{port}</span>
    </Handle>
  );
}

export default function NodeCard({ id, data }: NodeProps) {
  const d = data as unknown as NodeCardData;
  const [paramsOpen, setParamsOpen] = useState(false);
  const color = CATEGORY_COLORS[d.def.category] ?? "#64748b";
  const rows = Math.max(Object.keys(d.def.inputs).length, Object.keys(d.def.outputs).length);
  const hasParams = Object.keys(d.params).length > 0;
  const paramsJson = useMemo(() => JSON.stringify(d.params, null, 2), [d.params]);
  return (
    <div className={`panel w-[220px] pb-2 ${d.active ? "node-glow" : ""} ${d.blocked ? "node-blocked" : ""} ${d.diff === "removed" ? "opacity-60" : ""}`}
         style={{ minHeight: 40 + rows * 18, boxShadow: d.diff ? DIFF_RING[d.diff] : undefined }}>
      <div className="flex items-center gap-2 px-2 py-1 rounded-t-lg"
           style={{ background: `${color}22`, borderBottom: `1px solid ${color}55` }}>
        <span className="w-2 h-2 rounded-full" style={{ background: color }} />
        <span className="text-xs font-medium">{d.def.type}</span>
        <span className="text-[9px] text-slate-500 ml-auto">{id}</span>
        <button title="breakpoint" onClick={(e) => { e.stopPropagation(); d.onToggleBreakpoint?.(id); }}
                className={`w-3 h-3 rounded-full border ${d.breakpoint ? "bg-loom-red border-loom-red" : "border-slate-600"}`} />
      </div>
      {Object.entries(d.def.inputs).map(([p, t], i) => <Pin key={p} side="in" port={p} pin={t} idx={i} />)}
      {Object.entries(d.def.outputs).map(([p, t], i) => <Pin key={p} side="out" port={p} pin={t} idx={i} />)}
      <div className="px-2 pt-1 text-[9px] text-slate-500 font-mono" style={{ marginTop: rows * 18 }}>
        <div className="flex min-w-0 items-center gap-1.5">
          {hasParams && (
            <button type="button"
                    aria-label="Toggle node params"
                    aria-expanded={paramsOpen}
                    title={paramsOpen ? "hide params" : "show params"}
                    onClick={(e) => { e.stopPropagation(); setParamsOpen((open) => !open); }}
                    className="nodrag nopan shrink-0 rounded border border-edge/70 px-1 py-0.5 text-[8px] text-slate-400 hover:border-loom-blue/50 hover:text-loom-blue">
              {paramsOpen ? "hide" : "params"}
            </button>
          )}
          <span className="min-w-0 truncate" title={paramsJson}>{summarizeParams(d.params)}</span>
        </div>
        {paramsOpen && (
          <pre className="nodrag nopan mt-1 max-h-28 overflow-auto rounded border border-edge/60 bg-bg/70 p-1 text-[8px] leading-snug text-slate-400 whitespace-pre-wrap break-all">
            {paramsJson}
          </pre>
        )}
      </div>
    </div>
  );
}
