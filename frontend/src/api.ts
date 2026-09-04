import type { Status } from "./types";

const base = import.meta.env.VITE_ENGINE_API || (import.meta.env.DEV ? "" : "http://127.0.0.1:47821");
const token = import.meta.env.VITE_ENGINE_TOKEN || "";

export async function getStatus(signal?: AbortSignal): Promise<Status> {
  const response = await fetch(`${base}/api/status`, {
    cache: "no-store", signal, headers: token ? { "X-CryptoEdge-Token": token } : {},
  });
  if (!response.ok) throw new Error(`API ${response.status}`);
  return response.json();
}

export async function engineAction(action: string, confirm = false): Promise<string> {
  const response = await fetch(`${base}/api/engine/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { "X-CryptoEdge-Token": token } : {}) },
    body: JSON.stringify({ confirm }),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || result.message || "Nie udało się wykonać akcji");
  return result.message || "Gotowe";
}

export interface ReplayStatus {
  ok: boolean; running: boolean; phase: string; message: string; progress: number;
  elapsed_s: number; current_symbol?: string; completed: number; total: number;
  symbols: Array<{ symbol: string; status: string; detail: string; bars_5m: number; trades_oos?: number; net_r_oos?: number }>;
  result?: {
    trades_oos: number; win_rate_oos: number; net_r_oos: number; avg_r_oos?: number;
    profit_factor_oos: number|null; max_drawdown_r_oos?: number;
    trades_is: number; win_rate_is?: number; net_r_is?: number; avg_r_is?: number;
    profit_factor_is?: number|null; max_drawdown_r_is?: number; report_path?: string;
  };
  error?: string;
}

export async function getReplayStatus(signal?: AbortSignal): Promise<ReplayStatus> {
  const response = await fetch(`${base}/api/replay/status`, {
    cache: "no-store", signal, headers: token ? { "X-CryptoEdge-Token": token } : {},
  });
  if (!response.ok) throw new Error(`API ${response.status}`);
  return response.json();
}

export async function startReplay(request: { universe_mode: string; days: number; oos_fraction: number; liquid_limit: number; symbols?: string[] }): Promise<void> {
  const response = await fetch(`${base}/api/replay/start`, {
    method: "POST", headers: { "Content-Type": "application/json", ...(token ? { "X-CryptoEdge-Token": token } : {}) },
    body: JSON.stringify(request),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.message || result.error || "Nie udało się uruchomić replay");
}

export interface BlofinCredentialsStatus {
  ok: boolean; configured: boolean; partial: boolean; masked_key: string; message?: string;
  account?: { equity?: number; available?: number; currency: string; open_positions: number };
}

export async function getBlofinCredentialsStatus(signal?: AbortSignal): Promise<BlofinCredentialsStatus> {
  const response = await fetch(`${base}/api/settings/blofin`, {
    cache: "no-store", signal, headers: token ? { "X-CryptoEdge-Token": token } : {},
  });
  if (!response.ok) throw new Error("Nie udało się odczytać statusu kluczy");
  return response.json();
}

export async function updateBlofinCredentials(payload: { action: "save"|"test"|"clear"; api_key?: string; api_secret?: string; passphrase?: string; confirm?: boolean }): Promise<BlofinCredentialsStatus> {
  const response = await fetch(`${base}/api/settings/blofin`, {
    method: "POST", headers: { "Content-Type": "application/json", ...(token ? { "X-CryptoEdge-Token": token } : {}) },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.message || "Nie udało się zapisać kluczy");
  return result;
}
