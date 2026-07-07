import { runDefaultsFromLoom } from "./demoDefaults";
import type { Loom } from "./loom";

export type MarketWindow = {
  inst: string;
  bar: string;
  start_ms: number;
  end_ms: number;
  count: number;
};

export type BacktestSpeed = "1x" | "4x" | "instant";

export type BacktestConfig = {
  inst: string;
  bar: string;
  start_ms?: number;
  end_ms?: number;
  cash: number;
  fee_rate: number;
  speed: BacktestSpeed;
};

export const PLAYBACK_MS_BY_SPEED: Record<BacktestSpeed, number> = {
  "1x": 80,
  "4x": 20,
  instant: 0,
};

export function initialBacktestConfig(
  loom: Loom,
  catalog: MarketWindow[] = [],
): BacktestConfig {
  const defaults = runDefaultsFromLoom(loom);
  const exact = catalog.find((w) => w.inst === defaults.inst && w.bar === defaults.bar);
  const market = exact ?? catalog[0];
  const inst = exact ? defaults.inst : market?.inst ?? defaults.inst;
  const bar = exact ? defaults.bar : market?.bar ?? defaults.bar;
  const rawStart = defaults.start_ms ?? market?.start_ms;
  const rawEnd = defaults.end_ms ?? market?.end_ms;
  const start = clampWindowValue(rawStart, market);
  const end = clampWindowValue(rawEnd, market);
  const validWindow = start === undefined || end === undefined || start <= end;

  return {
    inst,
    bar,
    ...(validWindow && start !== undefined ? { start_ms: start } : market ? { start_ms: market.start_ms } : {}),
    ...(validWindow && end !== undefined ? { end_ms: end } : market ? { end_ms: market.end_ms } : {}),
    cash: 10_000,
    fee_rate: 0.0005,
    speed: "4x",
  };
}

export function buildBacktestRunBody(
  blueprint: Loom,
  config: BacktestConfig,
  breakpoints: string[] = [],
): Record<string, unknown> {
  return {
    blueprint,
    inst: config.inst,
    bar: config.bar,
    ...(config.start_ms !== undefined ? { start_ms: config.start_ms } : {}),
    ...(config.end_ms !== undefined ? { end_ms: config.end_ms } : {}),
    cash: config.cash,
    fee_rate: config.fee_rate,
    playback_ms: PLAYBACK_MS_BY_SPEED[config.speed],
    ws_wait_ms: 300,
    breakpoints,
    mode: "backtest",
  };
}

export function replayCandlesForCursor<T>(candles: T[], cursor?: number): T[] {
  if (cursor === undefined) return candles;
  if (cursor < 0) return [];
  return candles.slice(0, Math.min(candles.length, cursor + 1));
}

export function toDatetimeLocal(ms?: number): string {
  if (ms === undefined) return "";
  const date = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fromDatetimeLocal(value: string): number | undefined {
  if (!value) return undefined;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : undefined;
}

function clampWindowValue(value: number | undefined, market?: MarketWindow): number | undefined {
  if (value === undefined || !market) return value;
  return Math.max(market.start_ms, Math.min(market.end_ms, value));
}
