// frontend/src/App.tsx —— hash 路由外壳（HUD 指挥条）
import { useEffect, useState, lazy, Suspense } from "react";
import { useLang } from "./lib/i18n";

const Studio = lazy(() => import("./pages/Studio"));
const Terminal = lazy(() => import("./pages/Terminal"));
const Eval = lazy(() => import("./pages/Eval"));

export default function App() {
  const { t, lang, setLang } = useLang();
  const [route, setRoute] = useState(location.hash || "#/studio");
  useEffect(() => {
    const f = () => setRoute(location.hash || "#/studio");
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);

  const tab = (h: string, label: string) => {
    const active = route.startsWith(h);
    return (
      <a href={h}
         className={`relative px-3 py-1.5 font-display text-[13px] font-medium tracking-wide transition-colors ${
           active ? "text-loom-blue" : "text-slate-400 hover:text-slate-100"}`}>
        {label}
        {active && (
          <span className="absolute left-2.5 right-2.5 -bottom-[7px] h-px bg-loom-blue"
                style={{ boxShadow: "0 0 8px 1px rgba(56,189,248,0.85)" }} />
        )}
      </a>
    );
  };

  return (
    <div className="h-screen flex flex-col">
      <header className="relative flex items-center gap-5 px-5 h-14 shrink-0 border-b border-edge/70 bg-panel/50 backdrop-blur-md boot">
        <div className="flex items-baseline gap-2.5">
          <span className="font-display font-bold text-[19px] tracking-[0.02em] text-loom-gold"
                style={{ textShadow: "0 0 20px rgba(245,166,35,0.55)" }}>AlphaLoom</span>
          <span className="hud-label hidden sm:block">the graph is the agent</span>
        </div>
        <nav className="flex gap-1 ml-3">
          {tab("#/studio", t("studio"))}{tab("#/terminal", t("terminal"))}{tab("#/eval", t("evalLab"))}
        </nav>
        <div className="ml-auto flex items-center gap-5">
          <div className="hidden md:flex items-center gap-2">
            <span className="live-dot" />
            <span className="hud-label" style={{ color: "rgba(52,211,153,0.75)" }}>offline replay · zero-quota</span>
          </div>
          <button
            className="font-display text-[11px] font-semibold tracking-widest text-slate-400 hover:text-loom-blue transition-colors px-2 py-1 rounded border border-edge/60 hover:border-loom-blue/50"
            onClick={() => setLang(lang === "zh" ? "en" : "zh")}>{lang === "zh" ? "EN" : "中"}</button>
        </div>
        <div className="absolute inset-x-0 bottom-0 h-px sweep-line" />
      </header>
      <main className="flex-1 min-h-0">
        <Suspense fallback={
          <div className="p-8 hud-label text-slate-500 animate-pulse">initializing subsystem…</div>}>
          {route.startsWith("#/eval") ? <Eval />
            : route.startsWith("#/terminal") ? <Terminal /> : <Studio />}
        </Suspense>
      </main>
    </div>
  );
}
