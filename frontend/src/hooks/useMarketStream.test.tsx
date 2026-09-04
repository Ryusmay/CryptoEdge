import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMarketStore } from "../state/marketStore";
import { WebSocketTransport } from "../transport/webSocketTransport";
import { marketStreamUrl, useMarketStream } from "./useMarketStream";

function Harness({ enabled = true }: { enabled?: boolean }) {
  useMarketStream({ enabled, url: "ws://127.0.0.1:47822/api/stream", freshnessCheckMs: 250 });
  return null;
}

describe("useMarketStream", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    useMarketStore.setState({ state: "idle", lastMessageAt: null, gapDetected: false });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.useRealTimers();
  });

  it("keeps the local market stream on its dedicated loopback port", () => {
    expect(marketStreamUrl({ protocol: "http:", host: "127.0.0.1:47821" })).toBe("ws://127.0.0.1:47822/api/stream");
    expect(marketStreamUrl({ protocol: "https:", host: "terminal.local" }, "stream")).toBe("ws://127.0.0.1:47822/stream");
    expect(marketStreamUrl({ protocol: "tauri:", host: "localhost" })).toBe("ws://127.0.0.1:47822/api/stream");
  });

  it("starts once, checks freshness and stops on unmount", () => {
    const start = vi.spyOn(WebSocketTransport.prototype, "start").mockImplementation(() => undefined);
    const stop = vi.spyOn(WebSocketTransport.prototype, "stop").mockImplementation(() => undefined);
    const freshness = vi.spyOn(useMarketStore.getState(), "checkFreshness");

    act(() => root.render(<Harness />));
    expect(start).toHaveBeenCalledTimes(1);
    expect(useMarketStore.getState().state).toBe("connecting");
    act(() => vi.advanceTimersByTime(500));
    expect(freshness).toHaveBeenCalledTimes(2);
    act(() => root.unmount());
    expect(stop).toHaveBeenCalledTimes(1);
    expect(useMarketStore.getState().state).toBe("disconnected");
    root = createRoot(host);
  });

  it("does not create transport work when disabled", () => {
    const start = vi.spyOn(WebSocketTransport.prototype, "start").mockImplementation(() => undefined);
    act(() => root.render(<Harness enabled={false} />));
    expect(start).not.toHaveBeenCalled();
    expect(useMarketStore.getState().state).toBe("idle");
  });
});
