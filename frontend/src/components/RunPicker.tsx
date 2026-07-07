export type RunPickerItem = {
  run_id: string;
  blueprint_id?: string;
  status?: string;
};

type Props = {
  runs: RunPickerItem[];
  selectedId: string | null;
  label: string;
  onSelect: (runId: string) => void;
};

const STATUS_CLASS: Record<string, string> = {
  completed: "border-loom-green/40 bg-loom-green/12 text-loom-green",
  failed: "border-loom-red/40 bg-loom-red/12 text-loom-red",
  halted: "border-loom-amber/40 bg-loom-amber/12 text-loom-amber",
  running: "border-loom-blue/40 bg-loom-blue/12 text-loom-blue",
};

function shortRunId(runId: string): string {
  return `...${runId.slice(-6)}`;
}

function optionLabel(run: RunPickerItem): string {
  return `${shortRunId(run.run_id)} · ${run.blueprint_id ?? "unknown"} · ${run.status ?? "unknown"}`;
}

export default function RunPicker({ runs, selectedId, label, onSelect }: Props) {
  const selected = runs.find((run) => run.run_id === selectedId) ?? runs[0];
  const status = selected?.status ?? "unknown";
  const statusClass = STATUS_CLASS[status] ?? "border-edge bg-bg/40 text-slate-400";

  return (
    <section className="panel px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-[92px]">
          <div className="hud-label">{label}</div>
          <div className="font-mono text-[10px] text-slate-500">{runs.length} runs</div>
        </div>
        <select
          className="min-w-[260px] flex-1 rounded border border-edge bg-bg px-2 py-1.5 font-mono text-xs text-slate-100"
          value={selected?.run_id ?? ""}
          onChange={(event) => onSelect(event.target.value)}
        >
          {runs.map((run) => (
            <option key={run.run_id} value={run.run_id}>
              {optionLabel(run)}
            </option>
          ))}
        </select>
        {selected && (
          <div className="min-w-0 rounded border border-edge/70 bg-bg/35 px-2 py-1">
            <div className="max-w-[320px] truncate text-xs font-semibold text-slate-100">
              {selected.blueprint_id ?? "unknown blueprint"}
            </div>
            <div className="mt-1 flex items-center gap-2">
              <span className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${statusClass}`}>
                {status}
              </span>
              <span className="font-mono text-[10px] text-slate-500">{shortRunId(selected.run_id)}</span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
