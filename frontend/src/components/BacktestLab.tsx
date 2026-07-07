import CandleChart, { type Candle, type Fill } from "./CandleChart";
import EquityChart from "./EquityChart";
import {
  fromDatetimeLocal,
  replayCandlesForCursor,
  toDatetimeLocal,
  type BacktestConfig,
  type BacktestSpeed,
  type MarketWindow,
} from "../lib/backtestConfig";
import type { Loom } from "../lib/loom";
import { useLang } from "../lib/i18n";

type Props = {
  blueprint: Loom;
  catalog: MarketWindow[];
  config: BacktestConfig;
  onConfigChange: (next: BacktestConfig) => void;
  candles: Candle[];
  fills: Fill[];
  equityCurve: [number, number][];
  cursor?: number;
  status?: string;
  disabled?: boolean;
  onRun: () => void;
  onCommand: (cmd: "resume" | "step" | "stop") => void;
};

const LABELS = {
  zh: {
    title: "回测实验室",
    blueprint: "当前蓝图",
    market: "市场",
    bar: "周期",
    start: "开始",
    end: "结束",
    cash: "资金",
    fee: "手续费",
    speed: "速度",
    startRun: "开始",
    resume: "继续",
    step: "单步",
    stop: "停止",
    chart: "市场回放",
    equity: "权益",
    fills: "成交",
    empty: "当前窗口暂无 K 线",
    status: "状态",
  },
  en: {
    title: "Backtest Lab",
    blueprint: "Current blueprint",
    market: "Market",
    bar: "Bar",
    start: "Start",
    end: "End",
    cash: "Cash",
    fee: "Fee",
    speed: "Speed",
    startRun: "Start",
    resume: "Resume",
    step: "Step",
    stop: "Stop",
    chart: "Market replay",
    equity: "Equity",
    fills: "Fills",
    empty: "No candles in this window",
    status: "Status",
  },
} as const;

export default function BacktestLab({
  blueprint,
  catalog,
  config,
  onConfigChange,
  candles,
  fills,
  equityCurve,
  cursor,
  status,
  disabled,
  onRun,
  onCommand,
}: Props) {
  const { lang } = useLang();
  const l = LABELS[lang];
  const marketOptions = [...new Set(catalog.map((w) => w.inst))];
  const barOptions = [...new Set(catalog
    .filter((w) => w.inst === config.inst)
    .map((w) => w.bar))];
  const running = status === "running" || status === "paused";
  const visibleCandles = running ? replayCandlesForCursor(candles, cursor) : candles;
  const visibleEquity = running && cursor !== undefined
    ? equityCurve.slice(0, Math.min(equityCurve.length, cursor + 1))
    : equityCurve;
  const visibleLastTs = visibleCandles.at(-1)?.ts;
  const visibleFills = visibleLastTs === undefined
    ? []
    : fills.filter((fill) => fill.ts <= visibleLastTs);
  const progress = cursor === undefined
    ? 0
    : Math.min(candles.length, Math.max(0, cursor + 1));

  const updateFromMarket = (inst: string, bar: string) => {
    const window = catalog.find((w) => w.inst === inst && w.bar === bar)
      ?? catalog.find((w) => w.inst === inst)
      ?? catalog[0];
    onConfigChange({
      ...config,
      inst: window?.inst ?? inst,
      bar: window?.bar ?? bar,
      ...(window ? { start_ms: window.start_ms, end_ms: window.end_ms } : {}),
    });
  };

  const setNumber = (key: "cash" | "fee_rate", value: string) => {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) onConfigChange({ ...config, [key]: parsed });
  };

  return (
    <div className="panel h-full min-h-0 grid grid-cols-[300px_minmax(0,1fr)] gap-3 p-3 overflow-hidden">
      <div className="min-w-0 space-y-3 overflow-auto pr-1">
        <div>
          <div className="hud-label mb-1">{l.title}</div>
          <div className="text-sm font-semibold text-slate-100 truncate">{blueprint.name}</div>
          <div className="text-[11px] text-slate-500 truncate">{l.blueprint}: {blueprint.id}</div>
        </div>

        <div className="grid grid-cols-4 gap-2">
          <button
            onClick={onRun}
            disabled={disabled || running}
            className="col-span-2 px-2 py-2 rounded bg-loom-gold/20 text-loom-gold text-xs font-semibold disabled:opacity-30"
          >
            ▶ {l.startRun}
          </button>
          <button
            onClick={() => onCommand("step")}
            disabled={!status}
            className="px-2 py-2 rounded bg-loom-violet/15 text-loom-violet text-xs disabled:opacity-30"
          >
            {l.step}
          </button>
          <button
            onClick={() => onCommand("stop")}
            disabled={!status || status === "done"}
            className="px-2 py-2 rounded bg-loom-red/15 text-loom-red text-xs disabled:opacity-30"
          >
            {l.stop}
          </button>
        </div>
        <button
          onClick={() => onCommand("resume")}
          disabled={!status}
          className="w-full px-2 py-2 rounded bg-loom-green/15 text-loom-green text-xs disabled:opacity-30"
        >
          {l.resume}
        </button>

        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1">
            <span className="hud-label">{l.market}</span>
            <select
              className="w-full bg-bg border border-edge rounded px-2 py-1 text-xs text-slate-100"
              value={config.inst}
              onChange={(e) => updateFromMarket(e.target.value, config.bar)}
            >
              {(marketOptions.length ? marketOptions : [config.inst]).map((inst) => (
                <option key={inst} value={inst}>{inst}</option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="hud-label">{l.bar}</span>
            <select
              className="w-full bg-bg border border-edge rounded px-2 py-1 text-xs text-slate-100"
              value={config.bar}
              onChange={(e) => updateFromMarket(config.inst, e.target.value)}
            >
              {(barOptions.length ? barOptions : [config.bar]).map((bar) => (
                <option key={bar} value={bar}>{bar}</option>
              ))}
            </select>
          </label>
        </div>

        <label className="block space-y-1">
          <span className="hud-label">{l.start}</span>
          <input
            type="datetime-local"
            className="w-full bg-bg border border-edge rounded px-2 py-1 text-xs text-slate-100"
            value={toDatetimeLocal(config.start_ms)}
            onChange={(e) => onConfigChange({ ...config, start_ms: fromDatetimeLocal(e.target.value) })}
          />
        </label>
        <label className="block space-y-1">
          <span className="hud-label">{l.end}</span>
          <input
            type="datetime-local"
            className="w-full bg-bg border border-edge rounded px-2 py-1 text-xs text-slate-100"
            value={toDatetimeLocal(config.end_ms)}
            onChange={(e) => onConfigChange({ ...config, end_ms: fromDatetimeLocal(e.target.value) })}
          />
        </label>

        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1">
            <span className="hud-label">{l.cash}</span>
            <input
              type="number"
              min="0"
              step="100"
              className="w-full bg-bg border border-edge rounded px-2 py-1 text-xs text-slate-100"
              value={config.cash}
              onChange={(e) => setNumber("cash", e.target.value)}
            />
          </label>
          <label className="space-y-1">
            <span className="hud-label">{l.fee}</span>
            <input
              type="number"
              min="0"
              step="0.0001"
              className="w-full bg-bg border border-edge rounded px-2 py-1 text-xs text-slate-100"
              value={config.fee_rate}
              onChange={(e) => setNumber("fee_rate", e.target.value)}
            />
          </label>
        </div>

        <div className="space-y-1">
          <div className="hud-label">{l.speed}</div>
          <div className="grid grid-cols-3 border border-edge rounded overflow-hidden text-xs">
            {(["1x", "4x", "instant"] as BacktestSpeed[]).map((speed) => (
              <button
                key={speed}
                onClick={() => onConfigChange({ ...config, speed })}
                className={`px-2 py-1 ${config.speed === speed
                  ? "bg-loom-blue/20 text-loom-blue" : "text-slate-400 hover:text-slate-100"}`}
              >
                {speed === "instant" ? "0x" : speed}
              </button>
            ))}
          </div>
        </div>

      </div>

      <div className="min-w-0 min-h-0 grid grid-rows-[auto_minmax(0,1fr)_86px] gap-2">
        <div className="flex items-center justify-between gap-3 text-xs">
          <div>
            <span className="hud-label">{l.chart}</span>
            <span className="ml-3 font-mono text-loom-blue">{progress} / {candles.length}</span>
          </div>
          <div className="truncate text-slate-400">
            {l.status}: <span className="font-mono text-slate-200">{status ?? "idle"}</span>
          </div>
        </div>

        <div className="min-h-0 grid grid-cols-[minmax(0,1.35fr)_minmax(220px,0.65fr)] gap-2">
          <div className="min-w-0 min-h-0 rounded border border-edge/70 p-2 overflow-hidden">
            {candles.length ? (
              <CandleChart candles={visibleCandles} fills={visibleFills} height={220} />
            ) : (
              <div className="h-full grid place-items-center text-xs text-slate-500">{l.empty}</div>
            )}
          </div>
          <div className="min-w-0 rounded border border-edge/70 p-2 overflow-hidden">
            <div className="hud-label mb-1">{l.equity}</div>
            <EquityChart curve={visibleEquity} height={200} />
          </div>
        </div>

        <div className="min-h-0 overflow-hidden rounded border border-edge/70">
          <div className="grid grid-cols-[80px_52px_1fr_1fr_80px] gap-2 px-2 py-1 border-b border-edge/60 hud-label">
            <span>{l.fills}</span><span>side</span><span>qty</span><span>price</span><span>tag</span>
          </div>
          <div className="max-h-[58px] overflow-auto">
            {visibleFills.slice(-8).map((fill, idx) => (
              <div key={`${fill.ts}-${idx}`} className="grid grid-cols-[80px_52px_1fr_1fr_80px] gap-2 px-2 py-1 text-[11px] text-slate-300 font-mono">
                <span>{new Date(fill.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                <span className={fill.side === "buy" ? "text-loom-green" : "text-loom-red"}>{fill.side}</span>
                <span>{fill.qty.toFixed(4)}</span>
                <span>{fill.price.toFixed(2)}</span>
                <span className="truncate">{fill.tag}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
