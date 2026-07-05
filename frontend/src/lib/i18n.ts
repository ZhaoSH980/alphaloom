// frontend/src/lib/i18n.ts
import { useSyncExternalStore } from "react";

const dict = {
  zh: { studio: "蓝图工坊", terminal: "交易终端", run: "回测运行", compileOk: "编译通过",
        compileFail: "编译失败", cost: "成本证书", gallery: "蓝图库", save: "保存",
        resume: "继续", step: "单步", stop: "停止", paused: "已暂停", trades: "成交",
        equity: "权益", summary: "汇总", noRuns: "暂无运行", breakpointHint: "点节点圆点设断点" },
  en: { studio: "Studio", terminal: "Terminal", run: "Run backtest", compileOk: "Compiled",
        compileFail: "Compile failed", cost: "Cost certificate", gallery: "Gallery", save: "Save",
        resume: "Resume", step: "Step", stop: "Stop", paused: "Paused", trades: "Trades",
        equity: "Equity", summary: "Summary", noRuns: "No runs yet", breakpointHint: "Click node dot to set breakpoint" },
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
