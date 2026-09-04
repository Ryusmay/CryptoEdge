import type { MarketStreamPayload, StreamEnvelope } from "../types";

export interface StreamSink {
  ingest: (message: StreamEnvelope<MarketStreamPayload>) => "applied" | "ignored" | "resync";
  disconnected: () => void;
  resync: () => Promise<void>;
}

export interface WebSocketTransportOptions {
  url: string;
  sink: StreamSink;
  flushEveryMs?: number;
  reconnectMinMs?: number;
  reconnectMaxMs?: number;
}

/**
 * Transport is deliberately independent from React and Tauri IPC. Incoming
 * deltas are coalesced before touching Zustand, so a burst of ticks produces
 * at most one store update per flush window.
 */
export class WebSocketTransport {
  private socket: WebSocket | null = null;
  private queue: StreamEnvelope<MarketStreamPayload>[] = [];
  private flushTimer: number | null = null;
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private stopped = true;

  constructor(private readonly options: WebSocketTransportOptions) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.flushTimer !== null) window.clearTimeout(this.flushTimer);
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.flushTimer = null;
    this.reconnectTimer = null;
    this.queue = [];
    this.socket?.close(1000, "client shutdown");
    this.socket = null;
  }

  private connect(): void {
    if (this.stopped) return;
    const socket = new WebSocket(this.options.url);
    this.socket = socket;
    socket.addEventListener("open", () => { this.reconnectAttempt = 0; });
    socket.addEventListener("message", (event) => this.enqueue(event.data));
    socket.addEventListener("close", () => this.reconnect());
    socket.addEventListener("error", () => socket.close());
  }

  private enqueue(raw: unknown): void {
    try {
      const message = JSON.parse(String(raw)) as StreamEnvelope<MarketStreamPayload>;
      if (!message.session_id || !Number.isSafeInteger(message.sequence_id)
        || !["snapshot", "delta", "heartbeat"].includes(message.kind)
        || typeof message.payload !== "object" || message.payload === null) return;
      this.queue.push(message);
      if (this.flushTimer === null) {
        this.flushTimer = window.setTimeout(() => this.flush(), this.options.flushEveryMs ?? 75);
      }
    } catch {
      // A malformed network frame is ignored; the next sequence gap forces a
      // canonical REST snapshot instead of applying an uncertain delta.
    }
  }

  private flush(): void {
    this.flushTimer = null;
    const batch = this.queue.splice(0).sort((a, b) => a.sequence_id - b.sequence_id);
    for (const message of batch) {
      if (this.options.sink.ingest(message) === "resync") {
        this.queue = [];
        void this.options.sink.resync().finally(() => this.socket?.close(1012, "sequence resync"));
        break;
      }
    }
  }

  private reconnect(): void {
    this.socket = null;
    this.options.sink.disconnected();
    if (this.stopped) return;
    const min = this.options.reconnectMinMs ?? 500;
    const max = this.options.reconnectMaxMs ?? 10_000;
    const delay = Math.min(max, min * 2 ** this.reconnectAttempt++);
    this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
  }
}
