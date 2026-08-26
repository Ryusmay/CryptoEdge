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
export interface Status {
  ok: boolean; ts: number; version: string; feed?: string; prices: Record<string, number>; sparklines?: Record<string, number[]>;
  engine: { analysis: boolean; trading: boolean; paused: boolean; loading: boolean; mode: string; live_execution: boolean; warmup?: { phase: string; ready: boolean; available: number; total: number; message: string } };
  account: { equity?: number; available?: number; margin?: number; daily?: number; unrealized?: number; positions?: number };
  session: { mode: string; equity?: number; daily?: number; daily_pct?: number; daily_limit_pct?: number; unrealized?: number; positions?: number; max_positions?: number; closed_today?: number; winrate_today?: number; uptime?: string; regime?: string; kill_switch?: boolean; used_pct?: number };
  liveness?: Liveness;
  positions: Position[]; candidates: Candidate[]; events: BotEvent[];
}
