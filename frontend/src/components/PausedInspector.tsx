// frontend/src/components/PausedInspector.tsx
import { useLang } from "../lib/i18n";
export default function PausedInspector({ ev, onCmd }: {
  ev: { node_id: string; ts: number; inputs: Record<string, unknown> } | null;
  onCmd: (c: "resume" | "step" | "stop") => void }) {
  const { t } = useLang();
  if (!ev) return null;
  return (
    <div className="panel p-3 border-loom-amber/60 space-y-2">
      <div className="hud-label text-loom-amber">{t("paused")} · {ev.node_id} @ {ev.ts}</div>
      <pre className="text-[10px] font-mono text-slate-300 max-h-48 overflow-auto whitespace-pre-wrap">
        {JSON.stringify(ev.inputs, null, 2)}
      </pre>
      <div className="flex gap-2">
        <button className="px-2 py-1 text-xs rounded bg-loom-green/20 text-loom-green"
                onClick={() => onCmd("resume")}>{t("resume")}</button>
        <button className="px-2 py-1 text-xs rounded bg-loom-blue/20 text-loom-blue"
                onClick={() => onCmd("step")}>{t("step")}</button>
        <button className="px-2 py-1 text-xs rounded bg-loom-red/20 text-loom-red"
                onClick={() => onCmd("stop")}>{t("stop")}</button>
      </div>
    </div>
  );
}
