// frontend/src/components/ErrorPanel.tsx
export interface CompileError {
  code: string; message: string; node_id?: string | null; port?: string | null; fix_hint?: string | null;
}
export default function ErrorPanel({ errors, onFocus }: {
  errors: CompileError[]; onFocus: (nodeId: string) => void }) {
  if (!errors.length) return null;
  return (
    <div className="panel p-3 space-y-2 max-h-64 overflow-auto">
      <div className="hud-label text-loom-red">compile errors</div>
      {errors.map((e, i) => (
        <div key={i} className="text-xs border-l-2 border-loom-red pl-2 cursor-pointer"
             onClick={() => e.node_id && onFocus(e.node_id)}>
          <div className="font-mono text-loom-red">{e.code}</div>
          <div className="text-slate-300">{e.message}</div>
          {e.fix_hint && <div className="text-slate-500 italic">💡 {e.fix_hint}</div>}
        </div>
      ))}
    </div>
  );
}
