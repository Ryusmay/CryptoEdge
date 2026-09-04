import { create } from "zustand";
import type { MarketCandle, MarketDataBySymbol, MarketDeltaPayload, MarketMarker, MarketStreamPayload, Status, StreamEnvelope, StreamHealth, SymbolMarketData } from "../types";

const STALE_AFTER_MS = 5_000;
export const MARKET_HISTORY_LIMIT = 1_000;

const finiteCandle = (candle: MarketCandle): boolean =>
  Number.isFinite(candle.time) && Number.isFinite(candle.open) && Number.isFinite(candle.high)
  && Number.isFinite(candle.low) && Number.isFinite(candle.close)
  && (candle.volume === undefined || Number.isFinite(candle.volume));

const mergeCandles = (current: MarketCandle[], incoming: MarketCandle[]): MarketCandle[] => {
  const byTime = new Map<number, MarketCandle>();
  for (const candle of current) if (finiteCandle(candle)) byTime.set(candle.time, candle);
  // A candle with the same timestamp is the still-open candle replacement.
  for (const candle of incoming) if (finiteCandle(candle)) byTime.set(candle.time, candle);
  return [...byTime.values()].sort((a, b) => a.time - b.time).slice(-MARKET_HISTORY_LIMIT);
};

const mergeById = <T extends { id: string }>(current: T[], incoming: T[], limit = MARKET_HISTORY_LIMIT): T[] => {
  const byId = new Map(current.filter((item) => item.id).map((item) => [item.id, item]));
  for (const item of incoming) if (item.id) byId.set(item.id, item);
  return [...byId.values()].slice(-limit);
};

const emptySymbol = (): SymbolMarketData => ({ candles: [], levels: [], markers: [] });

export const mergeMarketData = (current: MarketDataBySymbol = {}, delta: MarketDeltaPayload["market"] = {}): MarketDataBySymbol => {
  const next = { ...current };
  for (const [symbol, update] of Object.entries(delta)) {
    if (!symbol || !update || typeof update !== "object") continue;
    const previous = current[symbol] ?? emptySymbol();
    next[symbol] = {
      candles: update.candles ? mergeCandles(previous.candles, update.candles) : previous.candles,
      levels: update.levels ? mergeById(previous.levels, update.levels, 100) : previous.levels,
      markers: update.markers ? mergeById<MarketMarker>(previous.markers, update.markers) : previous.markers,
    };
  }
  return next;
};

const normalizeSnapshot = (snapshot: Status): Status => snapshot.market
  ? { ...snapshot, market: mergeMarketData({}, snapshot.market) }
  : { ...snapshot };

export const mergeMarketSnapshot = (current: Status, delta: MarketDeltaPayload): Status => ({
  ...current,
  ...delta,
  prices: delta.prices ? { ...current.prices, ...delta.prices } : current.prices,
  sparklines: delta.sparklines ? { ...current.sparklines, ...delta.sparklines } : current.sparklines,
  market: delta.market ? mergeMarketData(current.market, delta.market) : current.market,
});

interface MarketState extends StreamHealth {
  snapshot: Status | null;
  ingest: (message: StreamEnvelope<MarketStreamPayload>) => "applied" | "ignored" | "resync";
  markDisconnected: () => void;
  checkFreshness: (now?: number) => void;
  replaceSnapshot: (snapshot: Status, receivedAt?: number) => void;
}

export const useMarketStore = create<MarketState>((set, get) => ({
  state: "idle",
  sessionId: null,
  sequenceId: 0,
  lastMessageAt: null,
  gapDetected: false,
  snapshot: null,
  replaceSnapshot: (snapshot, receivedAt = Date.now()) => set((current) => {
    const normalized = normalizeSnapshot(snapshot);
    return {
      snapshot: normalized.market ? normalized : { ...normalized, market: current.snapshot?.market },
      lastMessageAt: receivedAt, state: "live", gapDetected: false,
    };
  }),
  ingest: (message) => {
    const current = get();
    const newSession = current.sessionId !== null && current.sessionId !== message.session_id;
    const expected = current.sequenceId + 1;
    if (!newSession && message.sequence_id <= current.sequenceId) return "ignored";
    if (message.kind !== "snapshot" && (newSession || message.sequence_id !== expected)) {
      set({ state: "resyncing", gapDetected: true });
      return "resync";
    }
    const snapshot = message.kind === "snapshot"
      ? normalizeSnapshot(message.payload as Status)
      : current.snapshot ? mergeMarketSnapshot(current.snapshot, message.payload as MarketDeltaPayload) : null;
    set({
      snapshot, sessionId: message.session_id, sequenceId: message.sequence_id,
      lastMessageAt: Date.now(), state: "live", gapDetected: false,
    });
    return "applied";
  },
  markDisconnected: () => set({ state: "disconnected" }),
  checkFreshness: (now = Date.now()) => {
    const last = get().lastMessageAt;
    if (last !== null && now - last > STALE_AFTER_MS) set({ state: "stale" });
  },
}));
