import { lazy, Suspense, useEffect, useState } from "react";
import { getStatus, setRuntimeMode, type RuntimeMode } from "./lib/api";
import { useLang } from "./lib/i18n";

const Studio = lazy(() => import("./pages/Studio"));
const LiveDesk = lazy(() => import("./pages/LiveDesk"));
const Terminal = lazy(() => import("./pages/Terminal"));
const Eval = lazy(() => import("./pages/Eval"));

const MODE_STYLE = {
  offline: { color: "rgba(52,211,153,0.8)", dot: "#34d399" },
  live: { color: "rgba(245,166,35,0.9)", dot: "#f5a623" },
  none: { color: "rgba(148,163,184,0.7)", dot: "#64748b" },
} as const;

const MODE_LABEL = {
  offline: { zh: "离线", en: "Offline" },
  live: { zh: "实时", en: "Live" },
  none: { zh: "无LLM", en: "No LLM" },
} as const;

const RUNTIME_MODES: RuntimeMode[] = ["offline", "live", "none"];

function modeErrorMessage(err: unknown): string {
  const body = (err as { body?: string }).body;
  let message = (err as Error).message;
  if (!body) return message;
  try {
    const parsed = JSON.parse(body) as { detail?: string };
    return parsed.detail || message;
  } catch {
    return body;
  }
}

export default function App() {
  const { t, lang, setLang } = useLang();
  const [route, setRoute] = useState(location.hash || "#/studio");
  const [llmMode, setLlmMode] = useState<RuntimeMode | null>(null);
  const [modeBusy, setModeBusy] = useState<RuntimeMode | null>(null);
  const [modeError, setModeError] = useState<string | null>(null);

  useEffect(() => {
    const f = () => setRoute(location.hash || "#/studio");
    addEventListener("hashchange", f);
    getStatus().then((s) => setLlmMode(s.llm_mode)).catch(() => setLlmMode(null));
    return () => removeEventListener("hashchange", f);
  }, []);

  const switchRuntimeMode = async (mode: RuntimeMode) => {
    setModeError(null);
    if (modeBusy || mode === llmMode) return;
    setModeBusy(mode);
    try {
      const next = await setRuntimeMode(mode);
      setLlmMode(next.llm_mode);
    } catch (err) {
      setModeError(modeErrorMessage(err));
      getStatus().then((s) => setLlmMode(s.llm_mode)).catch(() => setLlmMode(null));
    } finally {
      setModeBusy(null);
    }
  };

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
          <span className="hud-label hidden sm:block">{t("tagline")}</span>
        </div>
        <nav className="flex gap-1 ml-3">
          {tab("#/studio", t("studio"))}{tab("#/live", t("liveDesk"))}{tab("#/terminal", t("terminal"))}{tab("#/eval", t("evalLab"))}
        </nav>
        <div className="ml-auto flex items-center gap-5">
          {llmMode && (
            <div className="hidden md:flex items-center gap-2"
                 aria-label={lang === "zh" ? "运行模式" : "Runtime mode"}>
              <span className="live-dot" style={{ background: MODE_STYLE[llmMode].dot,
                boxShadow: `0 0 8px 1px ${MODE_STYLE[llmMode].dot}` }} />
              <div className="flex items-center gap-1 rounded border border-edge/70 bg-bg/30 p-0.5">
                {RUNTIME_MODES.map((mode) => {
                  const active = llmMode === mode;
                  return (
                    <button key={mode}
                            type="button"
                            aria-pressed={active}
                            disabled={modeBusy !== null}
                            title={mode === "live" ? "real endpoint - live calls burn real quota" : undefined}
                            onClick={() => void switchRuntimeMode(mode)}
                            className={`font-display min-w-[58px] px-2 py-1 text-[10px] font-semibold tracking-wide rounded-sm transition-colors ${
                              active
                                ? "bg-loom-blue/14 text-slate-50 shadow-[0_0_12px_rgba(56,189,248,0.2)]"
                                : "text-slate-500 hover:text-slate-200 disabled:opacity-50"
                            }`}
                            style={active ? { color: MODE_STYLE[mode].color } : undefined}>
                      {MODE_LABEL[mode][lang]}
                    </button>
                  );
                })}
              </div>
              {modeError && (
                <span className="hidden xl:block max-w-[520px] truncate text-[10px] text-loom-red"
                      title={modeError}>
                  {modeError}
                </span>
              )}
            </div>
          )}
          <div className="flex items-center gap-1 rounded border border-edge/70 bg-bg/30 p-0.5"
               aria-label={t("language")}>
            {(["zh", "en"] as const).map((l) => (
              <button key={l}
                      type="button"
                      aria-pressed={lang === l}
                      onClick={() => setLang(l)}
                      className={`font-display min-w-9 px-2 py-1 text-[11px] font-semibold tracking-wide rounded-sm transition-colors ${
                        lang === l
                          ? "bg-loom-blue/18 text-loom-blue shadow-[0_0_12px_rgba(56,189,248,0.22)]"
                          : "text-slate-500 hover:text-slate-200"
                      }`}>
                {l === "zh" ? "中文" : "EN"}
              </button>
            ))}
          </div>
        </div>
        <div className="absolute inset-x-0 bottom-0 h-px sweep-line" />
      </header>
      <main className="flex-1 min-h-0">
        <Suspense fallback={
          <div className="p-8 hud-label text-slate-500 animate-pulse">{t("loadingApp")}</div>}>
          {route.startsWith("#/eval") ? <Eval />
            : route.startsWith("#/live") ? <LiveDesk />
            : route.startsWith("#/terminal") ? <Terminal /> : <Studio />}
        </Suspense>
      </main>
    </div>
  );
}
