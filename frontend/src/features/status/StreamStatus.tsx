import { useEffect, useState } from "react";
import type { StreamHealth } from "../../types";
import { useMarketStore } from "../../state/marketStore";
import "./streamStatus.css";

export type StreamStatusProps = Pick<
  StreamHealth,
  "state" | "sequenceId" | "sessionId" | "lastMessageAt" | "gapDetected"
>;

const STATUS_LABELS: Record<StreamHealth["state"], string> = {
  idle: "Bezczynny",
  connecting: "Łączenie",
  live: "Na żywo",
  stale: "Nieaktualne dane",
  resyncing: "Synchronizacja",
  disconnected: "Rozłączono",
};

export function dataAgeMs(lastMessageAt: number | null, now = Date.now()): number | null {
  return lastMessageAt === null ? null : Math.max(0, now - lastMessageAt);
}

export function formatDataAge(ageMs: number | null): string {
  if (ageMs === null) return "brak danych";
  if (ageMs < 1_000) return `${ageMs} ms`;
  if (ageMs < 60_000) return `${(ageMs / 1_000).toFixed(ageMs < 10_000 ? 1 : 0)} s`;
  const minutes = Math.floor(ageMs / 60_000);
  const seconds = Math.floor((ageMs % 60_000) / 1_000);
  return `${minutes} min ${seconds} s`;
}

/** Compact, presentation-only status suitable for a header or diagnostics panel. */
export function StreamStatus({
  state,
  sequenceId,
  sessionId,
  lastMessageAt,
  gapDetected,
}: StreamStatusProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    setNow(Date.now());
    if (lastMessageAt === null) return;
    const interval = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [lastMessageAt]);

  const age = formatDataAge(dataAgeMs(lastMessageAt, now));
  const label = STATUS_LABELS[state];
  const announcement = `${label}. Wiek danych: ${age}. Sekwencja: ${sequenceId}.`;

  return (
    <section className={`stream-status stream-status--${state}`} aria-label="Stan połączenia ze strumieniem">
      <div className="stream-status__headline">
        <span className="stream-status__indicator" aria-hidden="true" />
        <strong>{label}</strong>
        {gapDetected && <span className="stream-status__warning">wykryto lukę</span>}
      </div>
      <dl className="stream-status__details">
        <div><dt>Wiek danych</dt><dd>{age}</dd></div>
        <div><dt>Sekwencja</dt><dd>{sequenceId.toLocaleString("pl-PL")}</dd></div>
        <div><dt>Sesja</dt><dd title={sessionId ?? undefined}>{sessionId ?? "—"}</dd></div>
      </dl>
      <span className="stream-status__announcement" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </span>
    </section>
  );
}

/** Store-connected variant; keeps stream subscriptions local to this widget. */
export function ConnectionStatus() {
  const state = useMarketStore((store) => store.state);
  const sequenceId = useMarketStore((store) => store.sequenceId);
  const sessionId = useMarketStore((store) => store.sessionId);
  const lastMessageAt = useMarketStore((store) => store.lastMessageAt);
  const gapDetected = useMarketStore((store) => store.gapDetected);

  return <StreamStatus {...{ state, sequenceId, sessionId, lastMessageAt, gapDetected }} />;
}
