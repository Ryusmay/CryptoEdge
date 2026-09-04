import { useEffect, useState } from "react";
import { Play } from "lucide-react";
import { getReplayStatus, startReplay, type ReplayStatus } from "../api";
import { Card, Empty, Metric, PageHeader, Pill, num, pct, toneFor } from "../components";

export function ReplayView() {
  const [status, setStatus] = useState<ReplayStatus | null>(null);
  const [error, setError] = useState("");
  const [days, setDays] = useState(90);
  const [oos, setOos] = useState(30);
  const [limit, setLimit] = useState(10);
  const [mode, setMode] = useState("LIQUID");
  const [symbols, setSymbols] = useState("BTC, ETH, SOL");

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;
    const poll = async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const next = await getReplayStatus(controller.signal);
        if (active) { setStatus(next); setError(""); }
      } catch (caught) {
        if (active && (caught as Error).name !== "AbortError") setError("Brak połączenia ze statusem replay");
      }
    };
    void poll();
    const timer = window.setInterval(poll, 1_000);
    return () => { active = false; controller?.abort(); window.clearInterval(timer); };
  }, []);

  const run = async () => {
    setError("");
    try {
      await startReplay({ universe_mode: mode, days, oos_fraction: oos / 100, liquid_limit: limit, symbols: symbols.split(",").map((item) => item.trim()).filter(Boolean) });
      setStatus(await getReplayStatus());
    } catch (caught) { setError((caught as Error).message); }
  };
  const result = status?.result;

  return <>
    <PageHeader title="Replay historyczny" description="Backtest tej samej strategii na zamkniętych świecach BloFin" context={status?.running ? "TEST W TOKU" : status?.phase === "complete" ? "TEST ZAKOŃCZONY" : "NARZĘDZIE BADAWCZE"} />
    <Card title="Konfiguracja sesji">
      <div className="form-grid replay-form">
        <label>Uniwersum<select value={mode} disabled={status?.running} onChange={(event) => setMode(event.target.value)}><option>MANUAL</option><option>LIQUID</option><option>ALL</option></select></label>
        {mode === "MANUAL" && <label className="replay-manual">Monety<input value={symbols} disabled={status?.running} onChange={(event) => setSymbols(event.target.value)} placeholder="BTC, ETH, SOL" /></label>}
        <label>Dni<input type="number" min={7} max={365} value={days} disabled={status?.running} onChange={(event) => setDays(Number(event.target.value))} /></label>
        <label>Out-of-sample %<input type="number" min={10} max={50} value={oos} disabled={status?.running} onChange={(event) => setOos(Number(event.target.value))} /></label>
        <label>Limit monet<input type="number" min={1} max={100} value={limit} disabled={status?.running || mode !== "LIQUID"} onChange={(event) => setLimit(Number(event.target.value))} /></label>
        <button type="button" className="btn good" disabled={status?.running || (mode === "MANUAL" && !symbols.trim())} onClick={() => void run()}><Play size={14} aria-hidden="true" />{status?.running ? "Replay trwa" : "Uruchom replay"}</button>
      </div>
      {error && <p className="replay-error" role="alert">{error}</p>}
    </Card>
    {(status?.running || status?.phase === "complete" || status?.phase === "error") && <section className={`replay-status ${status.running ? "is-running" : status.phase}`} aria-live="polite" aria-busy={status.running}><div className="replay-status-head"><div className="replay-spinner" aria-hidden="true" /><div><strong>{status.message}</strong><span>{status.running ? `Czas: ${Math.floor(status.elapsed_s / 60)}m ${status.elapsed_s % 60}s · ${status.completed}/${status.total || "?"} monet` : status.error || "Wynik został zapisany"}</span></div><b>{status.progress}%</b></div><progress max="100" value={status.progress}>{status.progress}%</progress>{status.current_symbol && status.running && <small>Aktualnie: {status.current_symbol}</small>}</section>}
    <div className="kpi-grid"><Metric label="Transakcje OOS" value={result?.trades_oos ?? "—"} /><Metric label="Winrate OOS" value={result ? pct(result.win_rate_oos * 100) : "—"} /><Metric label="Net R OOS" value={result ? `${num(result.net_r_oos, 2)}R` : "—"} tone={toneFor(result?.net_r_oos)} /><Metric label="Profit factor" value={result?.profit_factor_oos == null ? "—" : num(result.profit_factor_oos, 2)} /></div>
    {status?.phase === "complete" && result && <Card title="Wynik replay" className="replay-result"><div className="replay-result-grid"><ReplayResult title="IN-SAMPLE · budowa" trades={result.trades_is} winRate={result.win_rate_is} netR={result.net_r_is} avgR={result.avg_r_is} profitFactor={result.profit_factor_is} drawdown={result.max_drawdown_r_is} /><ReplayResult title="OUT-OF-SAMPLE · walidacja" trades={result.trades_oos} winRate={result.win_rate_oos} netR={result.net_r_oos} avgR={result.avg_r_oos} profitFactor={result.profit_factor_oos} drawdown={result.max_drawdown_r_oos} oos /></div><small className="replay-report-path">Raport: {result.report_path || "zapisany lokalnie"}</small></Card>}
    <Card title={`Skanowane monety · ${status?.symbols.length || 0}`} className="replay-symbols"><table><caption className="visually-hidden">Postęp skanowania monet w replay</caption><thead><tr><th scope="col">Moneta</th><th scope="col">Status</th><th scope="col">Etap / dane</th><th scope="col">Świece 5m</th><th scope="col">Transakcje OOS</th><th scope="col">Net R OOS</th></tr></thead><tbody>{status?.symbols.length ? status.symbols.map((row) => <tr key={row.symbol}><th scope="row">{row.symbol}</th><td><Pill tone={row.status === "Przeskanowana" ? "good" : row.status === "Pominięta" ? "bad" : row.status === "Dane gotowe" ? "info" : "warn"}>{row.status}</Pill></td><td>{row.detail}</td><td>{row.bars_5m || "—"}</td><td>{row.trades_oos ?? "—"}</td><td className={toneFor(row.net_r_oos)}>{row.net_r_oos == null ? "—" : `${num(row.net_r_oos, 3)}R`}</td></tr>) : <tr><td colSpan={6}><Empty>Uruchom replay, aby rozpocząć skanowanie monet</Empty></td></tr>}</tbody></table></Card>
  </>;
}

interface ReplayResultProps { title: string; trades: number; winRate?: number; netR?: number; avgR?: number; profitFactor?: number | null; drawdown?: number; oos?: boolean }
function ReplayResult({ title, trades, winRate, netR, avgR, profitFactor, drawdown, oos }: ReplayResultProps) {
  return <section className={oos && trades === 0 ? "no-trades" : undefined}><span>{title}</span><strong>{trades} transakcji</strong>{oos && trades === 0 ? <p>Brak wejść w części walidacyjnej. Nie można ocenić win rate ani expectancy OOS — wynik strategii nie jest jeszcze potwierdzony.</p> : <><Metric label="Win rate" value={winRate == null ? "—" : pct(winRate * 100)} /><Metric label="Net R" value={netR == null ? "—" : `${num(netR, 3)}R`} tone={toneFor(netR)} /><Metric label="Średni wynik" value={avgR == null ? "—" : `${num(avgR, 3)}R`} tone={toneFor(avgR)} /><Metric label="Profit factor" value={profitFactor == null ? "—" : num(profitFactor, 2)} /><Metric label="Max drawdown" value={drawdown == null ? "—" : `${num(drawdown, 3)}R`} /></>}</section>;
}
