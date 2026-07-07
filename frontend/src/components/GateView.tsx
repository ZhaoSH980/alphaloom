import { useMemo } from "react";
import { useLang } from "../lib/i18n";
import {
  isGateProtocol,
  localize,
  type GateProtocol,
  type GateProtocolCard,
  type GateProtocolTone,
  type Loom,
} from "../lib/loom";

const labels = {
  en: {
    evidence: "Evidence sidecar",
    invariant: "Invariant",
    nodes: "Nodes",
  },
  zh: {
    evidence: "证据旁路",
    invariant: "不变量",
    nodes: "节点",
  },
} as const;

const fallbackProtocol: GateProtocol = {
  title: {
    en: "Two-stage gated trading protocol",
    zh: "两阶段门控交易协议",
  },
  subtitle: {
    en: "A PA_Agent-style path: diagnose first, short-circuit weak setups, then stamp risk before execution.",
    zh: "PA_Agent 风格路径：先诊断，弱机会短路，再通过 RiskGate 盖章后才允许执行。",
  },
  evidence: {
    en: "market bars, indicators, RAG citations, experience memory",
    zh: "行情 K 线、指标、RAG 引用、经验记忆",
  },
  steps: [
    {
      id: "market",
      tone: "data",
      eyebrow: { en: "Inputs", zh: "输入" },
      title: { en: "Market Snapshot", zh: "市场快照" },
      body: {
        en: "market bars, indicators, RAG citations, experience memory",
        zh: "行情 K 线、指标、RAG 引用、经验记忆",
      },
      nodes: ["feed", "ema", "atr", "kb", "xp"],
    },
    {
      id: "stage1",
      tone: "stage",
      eyebrow: { en: "Stage 1", zh: "阶段一" },
      title: { en: "Market Diagnosis", zh: "市场诊断" },
      body: {
        en: "classify regime, direction, setup quality",
        zh: "判断结构、方向、机会质量",
      },
      nodes: ["committee"],
    },
    {
      id: "diagnosis_gate",
      tone: "gate",
      eyebrow: { en: "Gate 1", zh: "门 1" },
      title: { en: "Diagnosis Gate", zh: "诊断门控" },
      body: { en: "proceed / wait / unknown", zh: "通过 / 等待 / 未知" },
      nodes: ["committee", "cite_gate"],
    },
    {
      id: "stage2",
      tone: "stage",
      eyebrow: { en: "Stage 2", zh: "阶段二" },
      title: { en: "Order Proposal", zh: "订单提案" },
      body: { en: "entry, stop, target, confidence", zh: "入场、止损、止盈、置信度" },
      nodes: ["sizer"],
    },
    {
      id: "validity_gate",
      tone: "gate",
      eyebrow: { en: "Gate 2", zh: "门 2" },
      title: { en: "Order Validity Gate", zh: "订单合法性门控" },
      body: { en: "contract, citations, prices, trace", zh: "结构、引用、价格、路径追踪" },
      nodes: ["cite_gate", "sizer"],
    },
    {
      id: "risk_gate",
      tone: "risk",
      eyebrow: { en: "Gate 3", zh: "门 3" },
      title: { en: "RiskGate Stamp", zh: "RiskGate 盖章" },
      body: { en: "size, stop, exposure contract", zh: "仓位、止损、敞口契约" },
      nodes: ["risk"],
    },
    {
      id: "runtime",
      tone: "exec",
      eyebrow: { en: "Runtime", zh: "运行时" },
      title: { en: "Execute / Simulate", zh: "执行 / 模拟" },
      body: { en: "only stamped orders enter runtime", zh: "只有盖章订单能进入运行时" },
      nodes: ["exec"],
    },
  ],
  sidecars: [
    {
      id: "stage1_fail",
      tone: "fail",
      title: { en: "wait / unknown", zh: "wait / unknown" },
      body: { en: "no trade, record reason", zh: "不下单，记录原因" },
      nodes: ["committee"],
    },
    {
      id: "stage2_fail",
      tone: "fail",
      title: { en: "reject / repair", zh: "reject / repair" },
      body: { en: "invalid order cannot reach RiskGate", zh: "非法订单不能进入 RiskGate" },
      nodes: ["cite_gate", "sizer"],
    },
    {
      id: "reflection",
      tone: "loop",
      title: { en: "Replay + Eval + Reflection", zh: "回放 + 评估 + 反思" },
      body: {
        en: "record every call, score real data, write lessons back",
        zh: "记录每次调用，用真实数据评分，把教训写回",
      },
      nodes: ["reflector", "xp_write"],
    },
  ],
  invariant: {
    en: "Raw LLM output never enters execution; every order path is gate-stamped first.",
    zh: "裸 LLM 输出永远不能直接执行；每条订单路径必须先通过门控盖章。",
  },
};

const toneClass: Record<GateProtocolTone, string> = {
  data: "border-loom-blue/60 bg-loom-blue/10 text-loom-blue",
  stage: "border-loom-violet/60 bg-loom-violet/10 text-loom-violet",
  gate: "border-loom-amber/70 bg-loom-amber/10 text-loom-amber",
  risk: "border-loom-green/70 bg-loom-green/10 text-loom-green",
  exec: "border-slate-300/60 bg-slate-200/10 text-slate-100",
  fail: "border-loom-red/60 bg-loom-red/10 text-loom-red",
  loop: "border-loom-cyan/60 bg-loom-cyan/10 text-loom-cyan",
};

function nodeIds(card: GateProtocolCard, validNodeIds?: Set<string>): string[] {
  return Array.isArray(card.nodes)
    ? card.nodes.filter((id): id is string =>
        typeof id === "string" && id.length > 0 && (!validNodeIds || validNodeIds.has(id)))
    : [];
}

function useProtocol(loom?: Loom): GateProtocol {
  return useMemo(() => {
    const raw = loom?.meta?.gateProtocol;
    if (!isGateProtocol(raw)) return fallbackProtocol;
    return {
      title: raw.title ?? fallbackProtocol.title,
      subtitle: raw.subtitle ?? fallbackProtocol.subtitle,
      evidence: raw.evidence ?? fallbackProtocol.evidence,
      steps: raw.steps?.length ? raw.steps : fallbackProtocol.steps,
      sidecars: raw.sidecars ?? [],
      invariant: raw.invariant ?? fallbackProtocol.invariant,
    };
  }, [loom]);
}

function StepCard({
  card,
  selectedNodeIds,
  showNodeMappings,
  validNodeIds,
  onFocusNodes,
}: {
  card: GateProtocolCard;
  selectedNodeIds: string[];
  showNodeMappings: boolean;
  validNodeIds?: Set<string>;
  onFocusNodes?: (nodeIds: string[]) => void;
}) {
  const { lang } = useLang();
  const l = labels[lang];
  const ids = showNodeMappings ? nodeIds(card, validNodeIds) : [];
  const tone = card.tone ?? "stage";
  const selected = ids.some((id) => selectedNodeIds.includes(id));
  const className = [
    "relative min-h-[136px] rounded-md border px-4 py-3 text-left shadow-hud transition",
    toneClass[tone],
    ids.length && onFocusNodes ? "cursor-pointer hover:-translate-y-0.5 hover:border-slate-200/70" : "",
    selected ? "ring-2 ring-loom-blue/80" : "",
  ].join(" ");
  const body = (
    <>
      <div className="hud-label mb-2 text-[9px] leading-none">{localize(card.eyebrow, lang)}</div>
      <div className="font-display text-base font-semibold leading-tight text-slate-100">
        {localize(card.title, lang)}
      </div>
      <div className="mt-2 text-[12px] leading-snug text-slate-400">{localize(card.body, lang)}</div>
      {ids.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          <span className="hud-label mr-1 text-[8px] text-slate-500">{l.nodes}</span>
          {ids.map((id) => (
            <span key={id} className="rounded border border-slate-600/60 bg-black/20 px-1.5 py-0.5 font-mono text-[9px] text-slate-300">
              {id}
            </span>
          ))}
        </div>
      )}
    </>
  );

  if (ids.length && onFocusNodes) {
    return (
      <button type="button" className={className} onClick={() => onFocusNodes(ids)}>
        {body}
      </button>
    );
  }
  return <div className={className}>{body}</div>;
}

export default function GateView({
  loom,
  selectedNodeIds = [],
  onFocusNodes,
}: {
  loom?: Loom;
  selectedNodeIds?: string[];
  onFocusNodes?: (nodeIds: string[]) => void;
}) {
  const { lang } = useLang();
  const l = labels[lang];
  const hasGateProtocol = isGateProtocol(loom?.meta?.gateProtocol);
  const protocol = useProtocol(loom);
  const validNodeIds = useMemo(() => new Set((loom?.nodes ?? []).map((node) => node.id)), [loom]);
  const steps = protocol.steps?.length ? protocol.steps : fallbackProtocol.steps ?? [];
  const sidecars = protocol.sidecars ?? fallbackProtocol.sidecars ?? [];

  return (
    <div className="h-full overflow-auto px-5 pb-5 pt-16">
      <div className="mx-auto flex min-h-full max-w-[1320px] flex-col justify-center gap-5">
        <div>
          <div className="font-display text-2xl font-semibold leading-tight text-slate-100">
            {localize(protocol.title, lang, localize(fallbackProtocol.title, lang))}
          </div>
          <div className="mt-1 max-w-3xl text-sm text-slate-400">
            {localize(protocol.subtitle, lang, localize(fallbackProtocol.subtitle, lang))}
          </div>
        </div>

        <div className="rounded-md border border-edge/80 bg-void/35 p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]">
            <span className="hud-label text-loom-blue">{l.evidence}</span>
            <span className="rounded border border-loom-blue/30 bg-loom-blue/10 px-2 py-1 text-slate-300">
              {localize(protocol.evidence, lang, localize(fallbackProtocol.evidence, lang))}
            </span>
          </div>

          <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}>
            {steps.map((card) => (
              <StepCard
                key={card.id}
                card={card}
                selectedNodeIds={selectedNodeIds}
                showNodeMappings={hasGateProtocol}
                validNodeIds={validNodeIds}
                onFocusNodes={onFocusNodes}
              />
            ))}
          </div>

          {sidecars.length > 0 && (
            <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr_1.3fr]">
              {sidecars.map((card) => (
                <StepCard
                  key={card.id}
                  card={card}
                  selectedNodeIds={selectedNodeIds}
                  showNodeMappings={hasGateProtocol}
                  validNodeIds={validNodeIds}
                  onFocusNodes={onFocusNodes}
                />
              ))}
            </div>
          )}
        </div>

        <div className="rounded-md border border-loom-gold/40 bg-loom-gold/10 px-4 py-3">
          <span className="hud-label mr-3 text-loom-gold">{l.invariant}</span>
          <span className="text-sm text-slate-300">
            {localize(protocol.invariant, lang, localize(fallbackProtocol.invariant, lang))}
          </span>
        </div>
      </div>
    </div>
  );
}
