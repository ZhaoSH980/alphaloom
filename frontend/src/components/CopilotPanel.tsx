// frontend/src/components/CopilotPanel.tsx —— Copilot 侧栏（D3 Task 9）
// 聊天输入 → POST /api/copilot/blueprint → 画布 diff 预览（新增绿/删除红/改动黄）→
// 应用落地 + 一键回测。explain/optimize 按钮。选中节点时展示 signal 富字段
// （rationale/confidence/citations/committee_trace）——从 run 的 trace 读。
import { useEffect, useState } from "react";
import { getTrace } from "../lib/api";
import {
  computeDiff, postCopilotBlueprint, postExplain, postOptimize,
  type ComputedDiff,
} from "../lib/copilot";
import type { Loom } from "../lib/loom";
import { useLang } from "../lib/i18n";

// 一次预览：新生成/优化的 loom + 相对当前画布的 diff。
export interface Preview {
  loom: Loom;
  diff: ComputedDiff;
  source: "blueprint" | "optimize";
}

interface ChatMsg { role: "user" | "agent"; text: string; }

export interface CopilotPanelProps {
  /** 取当前画布的 loom（Studio 的 currentLoom()）。 */
  getCurrentLoom: () => Loom;
  /** 生成/优化出新图后回调，Studio 存为预览并在画布高亮。 */
  onPreview: (p: Preview) => void;
  /** 应用预览：Studio 一次性 setNodes/setEdges 落地。 */
  onApply: () => void;
  /** 应用并立即回测。 */
  onApplyRun: () => void;
  /** 放弃预览。 */
  onDiscard: () => void;
  /** 当前预览（null=无预览）。 */
  preview: Preview | null;
  /** 选中节点 id（用于信号轨迹展示）。 */
  selectedNodeId: string | null;
  /** 最近一次 run 的 id（有则可读 trace）。 */
  runId?: string;
  /** 最近一次 run 的报告（optimize 读它提建议；可空）。 */
  report?: Record<string, unknown> | null;
}

function DiffSummary({ diff }: { diff: ComputedDiff }) {
  const { t } = useLang();
  const c = diff.counts;
  const chip = (n: number, label: string, cls: string) =>
    n > 0 ? <span className={`px-1.5 py-0.5 rounded ${cls}`}>{n} {label}</span> : null;
  const empty = c.added + c.removed + c.changed + c.addedEdges + c.removedEdges === 0;
  return (
    <div className="flex flex-wrap gap-1 text-[10px]">
      {chip(c.added, t("added"), "bg-loom-green/20 text-loom-green")}
      {chip(c.changed, t("changed"), "bg-loom-amber/20 text-loom-amber")}
      {chip(c.removed, t("removed"), "bg-loom-red/20 text-loom-red")}
      {chip(c.addedEdges, `+${t("edges")}`, "bg-loom-green/10 text-loom-green")}
      {chip(c.removedEdges, `-${t("edges")}`, "bg-loom-red/10 text-loom-red")}
      {empty && <span className="text-slate-500">no structural change</span>}
    </div>
  );
}

// —— signal 富字段可视化：读选中节点在 run trace 里的 signal 输出 ——
interface SignalFields {
  side?: string; rationale?: string; confidence?: number;
  citations?: string[]; committee_trace?: string[];
}

function pickSignal(outputs: Record<string, unknown>): SignalFields | null {
  // 节点输出里找 signal（LLMAnalyst/Committee/RiskGate 透传）——值形如 {as_of, value}
  // （trace 序列化）或直接 dict。取第一个含 rationale/committee_trace/side 的输出。
  for (const raw of Object.values(outputs)) {
    const v = (raw && typeof raw === "object" && "value" in (raw as object)
      ? (raw as { value: unknown }).value : raw) as SignalFields | null;
    if (v && typeof v === "object" &&
        ("rationale" in v || "committee_trace" in v || "side" in v || "citations" in v))
      return v;
  }
  return null;
}

function SignalInspector({ nodeId, runId }: { nodeId: string | null; runId?: string }) {
  const { t } = useLang();
  const [sig, setSig] = useState<SignalFields | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setSig(null);
    if (!nodeId || !runId) return;
    setLoading(true);
    // 取该节点最后一条 trace 事件的输出（limit=1 但需最新——拿一批取末条）。
    getTrace(runId, nodeId, undefined, 200)
      .then((rows) => {
        if (!alive) return;
        for (let i = rows.length - 1; i >= 0; i -= 1) {
          const s = pickSignal((rows[i].outputs ?? {}) as Record<string, unknown>);
          if (s) { setSig(s); return; }
        }
        setSig(null);
      })
      .catch(() => { if (alive) setSig(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [nodeId, runId]);

  if (!nodeId) return <div className="text-[10px] text-slate-500">{t("selectNodeHint")}</div>;
  if (loading) return <div className="text-[10px] text-slate-500">{t("thinking")}</div>;
  if (!sig) return <div className="text-[10px] text-slate-500">{t("noTrace")}</div>;

  return (
    <div className="space-y-1.5 text-[11px]">
      <div className="hud-label text-loom-blue">{t("signalInspect")} · {nodeId}</div>
      {sig.side && (
        <div><span className="text-slate-500">side </span>
          <span className="font-mono text-slate-200">{sig.side}</span>
          {typeof sig.confidence === "number" && (
            <span className="ml-2 text-slate-500">{t("confidence")} </span>)}
          {typeof sig.confidence === "number" && (
            <span className="font-mono text-loom-green">{sig.confidence.toFixed(2)}</span>)}
        </div>)}
      {sig.rationale && (
        <div><div className="text-slate-500">{t("rationale")}</div>
          <div className="text-slate-300">{sig.rationale}</div></div>)}
      {sig.citations && sig.citations.length > 0 && (
        <div><div className="text-slate-500">{t("citations")}</div>
          <div className="flex flex-wrap gap-1">
            {sig.citations.map((c, i) => (
              <span key={i} className="px-1 py-0.5 rounded bg-loom-violet/20 text-loom-violet text-[10px]">
                {c}
              </span>))}
          </div></div>)}
      {sig.committee_trace && sig.committee_trace.length > 0 && (
        <div><div className="text-slate-500">{t("committee")}</div>
          <div className="space-y-1">
            {sig.committee_trace.map((role, i) => (
              <pre key={i} className="text-[9px] font-mono text-slate-400 whitespace-pre-wrap
                                      border-l-2 border-loom-amber/50 pl-1.5 max-h-24 overflow-auto">
                {role}
              </pre>))}
          </div></div>)}
    </div>
  );
}

export default function CopilotPanel(props: CopilotPanelProps) {
  const { getCurrentLoom, onPreview, onApply, onApplyRun, onDiscard,
          preview, selectedNodeId, runId, report } = props;
  const { t } = useLang();
  const [nl, setNl] = useState("");
  const [busy, setBusy] = useState(false);
  const [chat, setChat] = useState<ChatMsg[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const push = (role: ChatMsg["role"], text: string) =>
    setChat((c) => [...c, { role, text }]);

  const generate = async () => {
    const prompt = nl.trim();
    if (!prompt || busy) return;
    setBusy(true); setErr(null);
    push("user", prompt);
    setNl("");
    try {
      const { loom, notes } = await postCopilotBlueprint(prompt);
      const diff = computeDiff(getCurrentLoom(), loom);
      onPreview({ loom, diff, source: "blueprint" });
      push("agent", notes.length ? notes[notes.length - 1] : "blueprint ready");
    } catch (e) {
      setErr(errText(e)); push("agent", `${t("copilotError")}: ${errText(e)}`);
    } finally { setBusy(false); }
  };

  const explain = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const { explanation } = await postExplain(getCurrentLoom());
      push("agent", explanation);
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };

  const optimize = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const { loom, diff: backendDiff, notes } =
        await postOptimize(getCurrentLoom(), report ?? undefined);
      // 后端已返回 diff，但前端画布高亮统一走 computeDiff（对齐当前画布身份）。
      void backendDiff;
      const diff = computeDiff(getCurrentLoom(), loom);
      onPreview({ loom, diff, source: "optimize" });
      push("agent", notes.length ? notes[notes.length - 1] : "optimization ready");
    } catch (e) {
      setErr(errText(e)); push("agent", `${t("copilotError")}: ${errText(e)}`);
    } finally { setBusy(false); }
  };

  return (
    <div className="panel flex flex-col h-full p-2 gap-2 min-h-0">
      <div className="hud-label text-loom-blue">{t("copilot")}</div>

      {/* 对话历史 */}
      <div className="flex-1 min-h-0 overflow-auto space-y-2 text-[11px]">
        {chat.length === 0 && (
          <div className="text-slate-500">{t("copilotHint")}</div>)}
        {chat.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : ""}>
            <div className={`inline-block px-2 py-1 rounded max-w-[95%] text-left ${
              m.role === "user"
                ? "bg-loom-blue/15 text-slate-200"
                : "bg-edge/60 text-slate-300"}`}>
              {m.text}
            </div>
          </div>))}
      </div>

      {/* 预览态：diff 摘要 + 应用/放弃 */}
      {preview && (
        <div className="border border-loom-amber/50 rounded p-2 space-y-2">
          <div className="hud-label text-loom-amber">{t("diffPreview")}</div>
          <DiffSummary diff={preview.diff} />
          <div className="flex gap-1.5">
            <button onClick={onApplyRun}
                    className="flex-1 px-2 py-1 text-xs rounded bg-loom-green/20 text-loom-green">
              ▶ {t("applyRun")}
            </button>
            <button onClick={onApply}
                    className="px-2 py-1 text-xs rounded bg-loom-blue/20 text-loom-blue">
              {t("apply")}
            </button>
            <button onClick={onDiscard}
                    className="px-2 py-1 text-xs rounded bg-loom-red/20 text-loom-red">
              {t("discard")}
            </button>
          </div>
        </div>)}

      {err && !preview && (
        <div className="text-[10px] text-loom-red border-l-2 border-loom-red pl-2">{err}</div>)}

      {/* 输入区 */}
      <div className="space-y-1.5">
        <textarea value={nl} onChange={(e) => setNl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); generate(); }
                  }}
                  placeholder={t("copilotHint")} rows={2}
                  className="w-full bg-void/60 border border-edge rounded px-2 py-1 text-xs
                             text-slate-200 resize-none focus:outline-none focus:border-loom-blue/60" />
        <div className="flex gap-1.5">
          <button onClick={generate} disabled={busy || !nl.trim()}
                  className="flex-1 px-2 py-1 text-xs rounded bg-loom-gold/20 text-loom-gold disabled:opacity-30">
            {busy ? t("thinking") : `✨ ${t("generate")}`}
          </button>
          <button onClick={explain} disabled={busy}
                  className="px-2 py-1 text-xs rounded bg-edge/60 text-slate-300 disabled:opacity-30">
            {t("explain")}
          </button>
          <button onClick={optimize} disabled={busy}
                  className="px-2 py-1 text-xs rounded bg-edge/60 text-slate-300 disabled:opacity-30">
            {t("optimize")}
          </button>
        </div>
      </div>

      {/* signal 富字段（选中节点） */}
      <div className="border-t border-edge pt-2">
        <SignalInspector nodeId={selectedNodeId} runId={runId} />
      </div>
    </div>
  );
}

function errText(e: unknown): string {
  if (e && typeof e === "object" && "body" in e) {
    const body = (e as { body?: string }).body;
    if (body) {
      try {
        const parsed = JSON.parse(body);
        const detail = parsed.detail ?? parsed;
        return typeof detail === "string" ? detail
          : detail.message ?? JSON.stringify(detail);
      } catch { return body; }
    }
  }
  return e instanceof Error ? e.message : String(e);
}
