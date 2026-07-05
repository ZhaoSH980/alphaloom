// frontend/src/App.tsx —— hash 路由外壳
import { useEffect, useState, lazy, Suspense } from "react";
import { useLang } from "./lib/i18n";

const Studio = lazy(() => import("./pages/Studio"));
const Terminal = lazy(() => import("./pages/Terminal"));

export default function App() {
  const { t, lang, setLang } = useLang();
  const [route, setRoute] = useState(location.hash || "#/studio");
  useEffect(() => {
    const f = () => setRoute(location.hash || "#/studio");
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  const tab = (h: string, label: string) => (
    <a href={h} className={`px-3 py-1.5 rounded-md text-sm ${route.startsWith(h)
      ? "bg-loom-blue/20 text-loom-blue" : "text-slate-400 hover:text-slate-200"}`}>{label}</a>
  );
  return (
    <div className="h-screen flex flex-col">
      <header className="flex items-center gap-4 px-4 py-2 border-b border-edge bg-panel/70">
        <div className="font-semibold tracking-wide text-loom-gold">AlphaLoom</div>
        <span className="hud-label">the graph is the agent</span>
        <nav className="flex gap-1 ml-6">{tab("#/studio", t("studio"))}{tab("#/terminal", t("terminal"))}</nav>
        <button className="ml-auto text-xs text-slate-400 hover:text-slate-200"
                onClick={() => setLang(lang === "zh" ? "en" : "zh")}>{lang === "zh" ? "EN" : "中"}</button>
      </header>
      <main className="flex-1 min-h-0">
        <Suspense fallback={<div className="p-8 text-slate-500">loading…</div>}>
          {route.startsWith("#/terminal") ? <Terminal /> : <Studio />}
        </Suspense>
      </main>
    </div>
  );
}
