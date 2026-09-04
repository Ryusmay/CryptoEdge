export type Tone = "good" | "bad" | "warn" | "info" | "muted";
export type View = "desk" | "scan" | "lab" | "replay" | "history" | "settings";

export interface Position {
  symbol: string; side: string; entry?: number; mark?: number; sl?: number; tp?: number;
  size?: number; margin?: number; pnl?: number; pnl_pct?: number; pnl_at_stop?: number;
  sl_mark?: string; age?: string; engine?: string;
}
export interface Candidate { sym: string; side?: string; score?: number; gate?: string; rr?: number; }
/** Wiek ostatniego cyklu i mapy cen. Bez tego zamrozony silnik wyglada w UI
 *  identycznie jak zdrowy - dokladnie to zdarzylo sie 25.08.2026 na 7,5 h. */
export interface Liveness {
  state: "ok" | "off" | "starting" | "degraded" | "frozen" | "unknown";
  engine_enabled?: boolean; cycle?: number; cycle_age_s?: number | null;
  price_map_age_s?: number | null; prices_stale?: boolean; frozen?: boolean;
  loop_stale_after_s?: number; error?: string;
}
export interface BotEvent { time?: string; tag?: string; text?: string; }
export interface MarketCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}
export interface MarketLevel {
  id: string;
  kind: "entry" | "stop" | "target" | "support" | "resistance" | "custom";
  price: number;
  label?: string;
  color?: string;
}
export interface MarketMarker {
  id: string;
  time: number;
  kind: "entry" | "exit" | "signal" | "fill";
  side?: "long" | "short";
  price?: number;
  label?: string;
}
export interface SymbolMarketData {
  candles: MarketCandle[];
  levels: MarketLevel[];
  markers: MarketMarker[];
}
export type MarketDataBySymbol = Record<string, SymbolMarketData>;
export type SymbolMarketDelta = Partial<SymbolMarketData>;
export type MarketDataDelta = Record<string, SymbolMarketDelta>;
export interface UiHistoryRow { time: string; symbol: string; side: string; entry: number; exit: number; pnl: number; pnl_pct: number; engine: string; reason: string; }
export interface UiEquityPoint { index: number; time: string; equity: number; drawdown: number; }
export interface UiEquityModel { starting_equity: number; current_equity: number; peak_equity: number; max_drawdown: number; max_drawdown_pct: number; points: UiEquityPoint[]; }
export interface UiExposureModel { gross: number; net: number; long: number; short: number; positions: number; by_symbol: Record<string, number>; }
export interface UiReconciliationMismatch { symbol: string; kind: string; detail: string; }
export interface UiReconciliationModel { status: string; checked_at: string; mismatch_count: number; mismatches: UiReconciliationMismatch[]; }
export interface UiSignalRow { symbol: string; side: string; score: number; gate: string; engine: string; time: string; }
export interface UiSignalTelemetry { total: number; by_gate: Record<string, number>; by_engine: Record<string, number>; rows: UiSignalRow[]; }
export interface UiReadModels { history: UiHistoryRow[]; equity: UiEquityModel; exposure: UiExposureModel; reconciliation: UiReconciliationModel; signals: UiSignalTelemetry; }
export interface Status {
  ok: boolean; ts: number; version: string; feed?: string; prices: Record<string, number>; sparklines?: Record<string, number[]>;
  engine: { analysis: boolean; trading: boolean; paused: boolean; loading: boolean; mode: string; live_execution: boolean; warmup?: { phase: string; ready: boolean; available: number; total: number; message: string } };
  account: { equity?: number; available?: number; margin?: number; daily?: number; unrealized?: number; positions?: number };
  session: { mode: string; equity?: number; daily?: number; daily_pct?: number; daily_limit_pct?: number; unrealized?: number; positions?: number; max_positions?: number; closed_today?: number; winrate_today?: number; uptime?: string; regime?: string; kill_switch?: boolean; used_pct?: number };
  liveness?: Liveness;
  positions: Position[]; candidates: Candidate[]; events: BotEvent[];
  market?: MarketDataBySymbol;
  ui?: UiReadModels;
}

/** Snapshot carries a complete Status. Delta fields are shallow except for
 * prices/sparklines/market, which the store merges per symbol. */
export type MarketSnapshotPayload = Status;
export type MarketDeltaPayload = Omit<Partial<Status>, "market"> & { market?: MarketDataDelta };
export type MarketStreamPayload = MarketSnapshotPayload | MarketDeltaPayload;

export interface StreamEnvelope<T = unknown> {
  session_id: string;
  sequence_id: number;
  emitted_at_ms: number;
  kind: "snapshot" | "delta" | "heartbeat";
  payload: T;
}

export interface StreamHealth {
  state: "idle" | "connecting" | "live" | "stale" | "resyncing" | "disconnected";
  sessionId: string | null;
  sequenceId: number;
  lastMessageAt: number | null;
  gapDetected: boolean;
}
