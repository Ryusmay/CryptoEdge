import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketStreamPayload, StreamEnvelope } from "../types";
import { WebSocketTransport, type StreamSink } from "./webSocketTransport";

class MockWebSocket extends EventTarget {
  static instances: MockWebSocket[] = [];
  readonly url: string;
  close = vi.fn(() => this.dispatchEvent(new Event("close")));

  constructor(url: string | URL) {
    super();
    this.url = String(url);
    MockWebSocket.instances.push(this);
  }

  open(): void { this.dispatchEvent(new Event("open")); }
  message(value: unknown): void {
    this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) }));
  }
  disconnect(): void { this.dispatchEvent(new Event("close")); }
}

const envelope = (sequenceId: number): StreamEnvelope<MarketStreamPayload> => ({
  session_id: "session-a",
  sequence_id: sequenceId,
  emitted_at_ms: sequenceId,
  kind: sequenceId === 1 ? "snapshot" : "delta",
  payload: {},
});

describe("WebSocketTransport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("batches a burst and applies it in sequence order", () => {
    const received: number[] = [];
    const sink: StreamSink = {
      ingest: vi.fn((message) => { received.push(message.sequence_id); return "applied" as const; }),
      disconnected: vi.fn(),
      resync: vi.fn(async () => undefined),
    };
    const transport = new WebSocketTransport({ url: "ws://local/stream", sink, flushEveryMs: 75 });
    transport.start();
    const socket = MockWebSocket.instances[0];

    socket.message(envelope(3));
    socket.message(envelope(1));
    socket.message(envelope(2));
    expect(sink.ingest).not.toHaveBeenCalled();
    vi.advanceTimersByTime(74);
    expect(sink.ingest).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);

    expect(received).toEqual([1, 2, 3]);
    transport.stop();
  });

  it("stops a batch and fetches a canonical snapshot after resync", async () => {
    const sink: StreamSink = {
      ingest: vi.fn((message) => message.sequence_id === 2 ? "resync" as const : "applied" as const),
      disconnected: vi.fn(),
      resync: vi.fn(async () => undefined),
    };
    const transport = new WebSocketTransport({ url: "ws://local/stream", sink, flushEveryMs: 10 });
    transport.start();
    const socket = MockWebSocket.instances[0];
    socket.message(envelope(1));
    socket.message(envelope(2));
    socket.message(envelope(3));
    await vi.advanceTimersByTimeAsync(10);

    expect(sink.ingest).toHaveBeenCalledTimes(2);
    expect(sink.resync).toHaveBeenCalledTimes(1);
    expect(socket.close).toHaveBeenCalledWith(1012, "sequence resync");
    transport.stop();
  });

  it("ignores malformed frames", () => {
    const sink: StreamSink = {
      ingest: vi.fn(() => "applied" as const),
      disconnected: vi.fn(),
      resync: vi.fn(async () => undefined),
    };
    const transport = new WebSocketTransport({ url: "ws://local/stream", sink, flushEveryMs: 10 });
    transport.start();
    MockWebSocket.instances[0].message({ sequence_id: 1, payload: {} });
    vi.advanceTimersByTime(20);
    expect(sink.ingest).not.toHaveBeenCalled();
    transport.stop();
  });

  it("reconnects with exponential backoff and resets it after open", () => {
    const sink: StreamSink = {
      ingest: vi.fn(() => "applied" as const),
      disconnected: vi.fn(),
      resync: vi.fn(async () => undefined),
    };
    const transport = new WebSocketTransport({
      url: "ws://local/stream",
      sink,
      reconnectMinMs: 100,
      reconnectMaxMs: 1_000,
    });
    transport.start();
    MockWebSocket.instances[0].disconnect();
    expect(sink.disconnected).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(99);
    expect(MockWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(2);

    MockWebSocket.instances[1].disconnect();
    vi.advanceTimersByTime(199);
    expect(MockWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(3);

    MockWebSocket.instances[2].open();
    MockWebSocket.instances[2].disconnect();
    vi.advanceTimersByTime(100);
    expect(MockWebSocket.instances).toHaveLength(4);
    transport.stop();
  });

  it("cancels pending flush and reconnect work on stop", () => {
    const sink: StreamSink = {
      ingest: vi.fn(() => "applied" as const),
      disconnected: vi.fn(),
      resync: vi.fn(async () => undefined),
    };
    const transport = new WebSocketTransport({ url: "ws://local/stream", sink, flushEveryMs: 10 });
    transport.start();
    MockWebSocket.instances[0].message(envelope(1));
    transport.stop();
    vi.runAllTimers();
    expect(sink.ingest).not.toHaveBeenCalled();
    expect(MockWebSocket.instances).toHaveLength(1);
  });
});
