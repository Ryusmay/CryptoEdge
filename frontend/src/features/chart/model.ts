import type { UTCTimestamp } from "lightweight-charts";

export interface OhlcvCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export type TradeMarkerKind = "entry" | "exit";

export interface TradeMarker {
  time: number;
  kind: TradeMarkerKind;
  side?: "long" | "short";
  label?: string;
}

export interface TradingLevels {
  entry?: number;
  stopLoss?: number;
  takeProfits?: number[];
}

export interface ChartCandle {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface ChartVolume {
  time: UTCTimestamp;
  value: number;
  color: string;
}

const isFinitePositive = (value: number) => Number.isFinite(value) && value > 0;

export function normalizeOhlcv(input: readonly OhlcvCandle[]): OhlcvCandle[] {
  const byTime = new Map<number, OhlcvCandle>();
  for (const candle of input) {
    if (!Number.isFinite(candle.time) || !isFinitePositive(candle.open) ||
      !isFinitePositive(candle.high) || !isFinitePositive(candle.low) ||
      !isFinitePositive(candle.close)) continue;
    const high = Math.max(candle.high, candle.open, candle.close);
    const low = Math.min(candle.low, candle.open, candle.close);
    byTime.set(Math.floor(candle.time), {
      ...candle,
      time: Math.floor(candle.time),
      high,
      low,
      volume: Number.isFinite(candle.volume) && (candle.volume ?? 0) >= 0 ? candle.volume : 0,
    });
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

export function toChartData(input: readonly OhlcvCandle[]) {
  const candles = normalizeOhlcv(input);
  return {
    candles: candles.map(({ time, open, high, low, close }): ChartCandle => ({
      time: time as UTCTimestamp, open, high, low, close,
    })),
    volumes: candles.map(({ time, open, close, volume }): ChartVolume => ({
      time: time as UTCTimestamp,
      value: volume ?? 0,
      color: close >= open ? "rgba(51, 214, 166, .38)" : "rgba(255, 91, 113, .38)",
    })),
  };
}

export function valuesToOhlcv(values: readonly number[], nowSeconds = Math.floor(Date.now() / 1000), interval = 900): OhlcvCandle[] {
  return values.filter(isFinitePositive).map((close, index, valid) => {
    const open = index === 0 ? close : valid[index - 1];
    return {
      time: nowSeconds - (valid.length - 1 - index) * interval,
      open,
      high: Math.max(open, close),
      low: Math.min(open, close),
      close,
      volume: 0,
    };
  });
}

export function validLevels(levels?: TradingLevels): Array<{ price: number; title: string; color: string }> {
  if (!levels) return [];
  const result: Array<{ price: number; title: string; color: string }> = [];
  if (levels.entry && isFinitePositive(levels.entry)) result.push({ price: levels.entry, title: "ENTRY", color: "#5da9ff" });
  if (levels.stopLoss && isFinitePositive(levels.stopLoss)) result.push({ price: levels.stopLoss, title: "SL", color: "#ff5b71" });
  levels.takeProfits?.forEach((price, index) => {
    if (isFinitePositive(price)) result.push({ price, title: `TP${index + 1}`, color: "#33d6a6" });
  });
  return result;
}
