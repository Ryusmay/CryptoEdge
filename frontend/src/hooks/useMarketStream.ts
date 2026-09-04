import { useEffect } from "react";
import { getStatus } from "../api";
import { useMarketStore } from "../state/marketStore";
import { WebSocketTransport, type StreamSink } from "../transport/webSocketTransport";
import type { MarketStreamPayload, StreamEnvelope } from "../types";

const DEFAULT_STREAM_PATH = "/api/stream";
const DEFAULT_FRESHNESS_CHECK_MS = 1_000;

interface LocationLike { protocol: string; host: string }

export function marketStreamUrl(_location: LocationLike = window.location, path = DEFAULT_STREAM_PATH): string {
  const token = import.meta.env.VITE_ENGINE_TOKEN;
  const suffix = token ? `?token=${encodeURIComponent(token)}` : "";
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const configured = import.meta.env.VITE_MARKET_STREAM_URL?.replace(/\/$/, "");
  return `${configured ?? "ws://127.0.0.1:47822"}${normalizedPath}${suffix}`;
}

export interface UseMarketStreamOptions {
  enabled?: boolean;
  url?: string;
  freshnessCheckMs?: number;
}

/** Owns the browser-side market stream lifecycle. REST remains the canonical
 * recovery path whenever the transport/store detects a sequence gap. */
export function useMarketStream(options: UseMarketStreamOptions = {}): void {
  const { enabled = true, url, freshnessCheckMs = DEFAULT_FRESHNESS_CHECK_MS } = options;

  useEffect(() => {
    if (!enabled) return;
    useMarketStore.setState({ state: "connecting" });
    const store = useMarketStore.getState();
    const sink: StreamSink = {
      ingest: (message) => useMarketStore.getState().ingest(message as StreamEnvelope<MarketStreamPayload>),
      disconnected: () => useMarketStore.getState().markDisconnected(),
      resync: async () => {
        const snapshot = await getStatus();
        useMarketStore.getState().replaceSnapshot(snapshot);
      },
    };
    const transport = new WebSocketTransport({ url: url ?? marketStreamUrl(), sink });
    transport.start();
    const freshnessTimer = window.setInterval(() => useMarketStore.getState().checkFreshness(), freshnessCheckMs);

    return () => {
      window.clearInterval(freshnessTimer);
      transport.stop();
      store.markDisconnected();
    };
  }, [enabled, freshnessCheckMs, url]);
}
