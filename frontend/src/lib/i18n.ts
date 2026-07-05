// frontend/src/lib/i18n.ts
import { useSyncExternalStore } from "react";

const dict = {
  zh: { studio: "蓝图工坊", terminal: "交易终端", run: "回测运行", compileOk: "编译通过",
        compileFail: "编译失败", cost: "成本证书", gallery: "蓝图库", save: "保存",
        resume: "继续", step: "单步", stop: "停止", paused: "已暂停", trades: "成交",
        equity: "权益", summary: "汇总", noRuns: "暂无运行", breakpointHint: "点节点圆点设断点",
        copilot: "Copilot 助手", copilotHint: "用一句话描述你想要的策略",
        generate: "生成蓝图", explain: "解释图", optimize: "优化图", apply: "应用", discard: "放弃",
        applyRun: "应用并回测", thinking: "思考中…", diffPreview: "变更预览",
        added: "新增", removed: "删除", changed: "改动", nodes: "节点", edges: "边",
        signalInspect: "信号详情", rationale: "决策理由", confidence: "置信度",
        citations: "引用", committee: "委员会", selectNodeHint: "选中节点查看其信号轨迹",
        noTrace: "该节点暂无信号轨迹（先回测）", copilotError: "生成失败",
        mode: "模式", modeBacktest: "回测", modeReplay: "加速回放",
        insights: "Agent 洞察", reflection: "反思", memory: "记忆",
        memoryOn: "已启用经验库", memoryOff: "未启用经验库",
        noAgentData: "此 run 无委员会/反思数据", loadingInsights: "加载轨迹中…",
        verdictReasonableRight: "合理且正确", verdictReasonableWrong: "合理但错误",
        verdictLucky: "侥幸", verdictBadProcess: "过程有误", lesson: "教训",
        regimeBucket: "市场状态", pnl: "盈亏", verdicts: "反思判定" },
  en: { studio: "Studio", terminal: "Terminal", run: "Run backtest", compileOk: "Compiled",
        compileFail: "Compile failed", cost: "Cost certificate", gallery: "Gallery", save: "Save",
        resume: "Resume", step: "Step", stop: "Stop", paused: "Paused", trades: "Trades",
        equity: "Equity", summary: "Summary", noRuns: "No runs yet", breakpointHint: "Click node dot to set breakpoint",
        copilot: "Copilot", copilotHint: "Describe the strategy you want in one line",
        generate: "Generate", explain: "Explain", optimize: "Optimize", apply: "Apply", discard: "Discard",
        applyRun: "Apply & run", thinking: "Thinking…", diffPreview: "Change preview",
        added: "added", removed: "removed", changed: "changed", nodes: "nodes", edges: "edges",
        signalInspect: "Signal detail", rationale: "Rationale", confidence: "Confidence",
        citations: "Citations", committee: "Committee", selectNodeHint: "Select a node to view its signal trace",
        noTrace: "No signal trace for this node (run a backtest first)", copilotError: "Generation failed",
        mode: "Mode", modeBacktest: "Backtest", modeReplay: "Fast replay",
        insights: "Agent insights", reflection: "Reflection", memory: "Memory",
        memoryOn: "Experience store engaged", memoryOff: "No experience store",
        noAgentData: "No committee / reflection data in this run", loadingInsights: "Loading trace…",
        verdictReasonableRight: "reasonable & right", verdictReasonableWrong: "reasonable but wrong",
        verdictLucky: "lucky", verdictBadProcess: "bad process", lesson: "Lesson",
        regimeBucket: "Regime", pnl: "PnL", verdicts: "Verdicts" },
} as const;
export type LangKey = keyof typeof dict.zh;

let lang: "zh" | "en" = (localStorage.getItem("alphaloom.lang") as "zh" | "en") || "zh";
const subs = new Set<() => void>();
export function setLang(l: "zh" | "en") {
  lang = l; localStorage.setItem("alphaloom.lang", l); subs.forEach((f) => f());
}
export function useLang() {
  const l = useSyncExternalStore((cb) => { subs.add(cb); return () => subs.delete(cb); }, () => lang);
  return { lang: l, t: (k: LangKey) => dict[l][k], setLang };
}
