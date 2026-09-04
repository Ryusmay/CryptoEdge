import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketStreamPayload, Status, StreamEnvelope } from "../types";
import { MARKET_HISTORY_LIMIT, useMarketStore } from "./marketStore";

const status = (price = 100): Status => ({
  ok: true,
  ts: 1,
  version: "test",
  prices: { BTCUSDT: price },
  engine: { analysis: true, trading: false, paused: false, loading: false, mode: "paper", live_execution: false },
  account: {},
  session: { mode: "paper" },
  positions: [],
  candidates: [],
  events: [],
});

const envelope = (
  sequenceId: number,
  kind: StreamEnvelope["kind"],
  payload: MarketStreamPayload,
  sessionId = "session-a",
): StreamEnvelope<MarketStreamPayload> => ({
  session_id: sessionId,
  sequence_id: sequenceId,
  emitted_at_ms: 1,
  kind,
  payload,
});

describe("marketStore stream reconciliation", () => {
  beforeEach(() => {
    useMarketStore.setState({
      state: "idle",
      sessionId: null,
      sequenceId: 0,
      lastMessageAt: null,
      gapDetected: false,
      snapshot: null,
    });
    vi.useRealTimers();
  });

  it("applies a snapshot and merges the next delta", () => {
    expect(useMarketStore.getState().ingest(envelope(10, "snapshot", status()))).toBe("applied");
    expect(useMarketStore.getState().ingest(envelope(11, "delta", { prices: { BTCUSDT: 101 } }))).toBe("applied");

    const state = useMarketStore.getState();
    expect(state.snapshot?.prices.BTCUSDT).toBe(101);
    expect(state.sequenceId).toBe(11);
    expect(state.state).toBe("live");
  });

  it("requests resync on a sequence gap without mutating the snapshot", () => {
    useMarketStore.getState().ingest(envelope(4, "snapshot", status()));

    expect(useMarketStore.getState().ingest(envelope(6, "delta", { prices: { BTCUSDT: 999 } }))).toBe("resync");
    expect(useMarketStore.getState()).toMatchObject({
      state: "resyncing",
      gapDetected: true,
      sequenceId: 4,
    });
    expect(useMarketStore.getState().snapshot?.prices.BTCUSDT).toBe(100);
  });

  it("ignores a duplicate or older message", () => {
    useMarketStore.getState().ingest(envelope(7, "snapshot", status()));

    expect(useMarketStore.getState().ingest(envelope(7, "delta", { prices: { BTCUSDT: 200 } }))).toBe("ignored");
    expect(useMarketStore.getState().snapshot?.prices.BTCUSDT).toBe(100);
  });

  it("requires resync for a delta from a new session and accepts its snapshot", () => {
    useMarketStore.getState().ingest(envelope(20, "snapshot", status()));

    expect(useMarketStore.getState().ingest(envelope(1, "delta", { prices: { BTCUSDT: 200 } }, "session-b"))).toBe("resync");
    expect(useMarketStore.getState().ingest(envelope(1, "snapshot", status(300), "session-b"))).toBe("applied");
    expect(useMarketStore.getState()).toMatchObject({
      sessionId: "session-b",
      sequenceId: 1,
      gapDetected: false,
      state: "live",
    });
  });

  it("marks old data stale and a closed stream disconnected", () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    useMarketStore.getState().ingest(envelope(1, "snapshot", status()));

    useMarketStore.getState().checkFreshness(15_001);
    expect(useMarketStore.getState().state).toBe("stale");
    useMarketStore.getState().markDisconnected();
    expect(useMarketStore.getState().state).toBe("disconnected");
  });

  it("replaces an open candle, orders deltas and preserves other symbols", () => {
    const initial = status();
    initial.market = {
      BTC: { candles: [{ time: 10, open: 100, high: 101, low: 99, close: 100 }], levels: [], markers: [] },
      ETH: { candles: [{ time: 10, open: 20, high: 21, low: 19, close: 20 }], levels: [], markers: [] },
    };
    useMarketStore.getState().ingest(envelope(1, "snapshot", initial));
    useMarketStore.getState().ingest(envelope(2, "delta", { market: { BTC: { candles: [
      { time: 20, open: 102, high: 104, low: 101, close: 103 },
      { time: 10, open: 100, high: 103, low: 99, close: 102 },
    ] } } }));

    expect(useMarketStore.getState().snapshot?.market?.BTC.candles.map((candle) => [candle.time, candle.close])).toEqual([[10, 102], [20, 103]]);
    expect(useMarketStore.getState().snapshot?.market?.ETH.candles).toHaveLength(1);
  });

  it("preserves streamed market history when REST refresh has no market payload", () => {
    const initial = status();
    initial.market = { BTC: { candles: [{ time: 10, open: 1, high: 2, low: 1, close: 2 }], levels: [], markers: [] } };
    useMarketStore.getState().replaceSnapshot(initial);
    useMarketStore.getState().replaceSnapshot(status(120));
    expect(useMarketStore.getState().snapshot?.prices.BTCUSDT).toBe(120);
    expect(useMarketStore.getState().snapshot?.market?.BTC.candles).toHaveLength(1);
  });

  it("bounds candle history and keeps the newest timestamps", () => {
    const initial = status();
    initial.market = { BTC: { candles: [], levels: [], markers: [] } };
    useMarketStore.getState().ingest(envelope(1, "snapshot", initial));
    const candles = Array.from({ length: MARKET_HISTORY_LIMIT + 20 }, (_, time) => ({ time, open: 1, high: 2, low: 1, close: 2 }));
    useMarketStore.getState().ingest(envelope(2, "delta", { market: { BTC: { candles } } }));
    const stored = useMarketStore.getState().snapshot?.market?.BTC.candles ?? [];
    expect(stored).toHaveLength(MARKET_HISTORY_LIMIT);
    expect(stored[0].time).toBe(20);
  });

  it("merges price maps per symbol", () => {
    const initial = status();
    initial.prices.ETHUSDT = 50;
    useMarketStore.getState().ingest(envelope(1, "snapshot", initial));
    useMarketStore.getState().ingest(envelope(2, "delta", { prices: { BTCUSDT: 110 } }));
    expect(useMarketStore.getState().snapshot?.prices).toEqual({ BTCUSDT: 110, ETHUSDT: 50 });
  });
});
