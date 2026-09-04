import type { CSSProperties } from "react";
import { Card, Empty, Gate, Metric, PageHeader, Pill, money, num, pct, toneFor } from "../components";
import type { Candidate, Status } from "../types";

export interface DeskViewProps {
  data: Status | null;
  onSymbol: (symbol: string) => void;
  act: (action: string, confirm?: boolean) => void;
}

export function DeskView({ data, onSymbol, act }: DeskViewProps) {
  const session = data?.session;
  const account = data?.account;

  return <>
    <PageHeader title="Pulpit operacyjny" description="Pozycje, rynek i aktywne ryzyko w jednym miejscu" context={`${session?.regime || "REŻIM —"} · ${session?.uptime || "—"}`}/>
    <div className="desk-top">
      <Card title={`Otwarte pozycje · ${session?.positions || 0}/${session?.max_positions || 0}`} className="positions" action={<button className="text-action danger-text" onClick={() => act("close_all", true)}>Zamknij wszystkie</button>}>
        <table><caption className="visually-hidden">Otwarte pozycje</caption><thead><tr><th scope="col">Para</th><th scope="col">Kierunek</th><th scope="col">Wejście</th><th scope="col">SL</th><th scope="col">Zysk przy SL</th><th scope="col">PnL</th><th scope="col">Wiek</th></tr></thead>
          <tbody>{data?.positions.length ? data.positions.map(position => <tr key={position.symbol} onDoubleClick={() => onSymbol(position.symbol)}><th scope="row">{position.symbol}</th><td className={position.side === "SHORT" ? "bad" : "good"}>{position.side}</td><td>{num(position.entry, 6)}</td><td>{position.sl_mark} {num(position.sl, 6)}</td><td className={toneFor(position.pnl_at_stop)}>{money(position.pnl_at_stop, true)}</td><td className={toneFor(position.pnl)}>{money(position.pnl, true)} <small>{pct(position.pnl_pct)}</small></td><td>{position.age || "—"}</td></tr>) : <tr><td colSpan={7}><Empty>Brak otwartych pozycji</Empty></td></tr>}</tbody>
        </table>
      </Card>
      <Card title="PnL, sesja i ryzyko" className="session-card combined-account"><div className="session-result"><strong className={`hero-number ${toneFor(session?.daily)}`}>{money(session?.daily, true)}</strong><small>{pct(session?.daily_pct)} dzisiaj</small></div><div className="risk-row"><div className="risk-ring" style={{ "--risk": `${session?.used_pct || 0}%` } as CSSProperties}><b>{Math.round(session?.used_pct || 0)}%</b></div><div><Metric label="Użyty margin" value={money(account?.margin)}/><Metric label="Wolne środki" value={money(account?.available)}/></div></div><div className="metric-list"><Metric label="Equity" value={money(session?.equity)}/><Metric label="Niezrealizowany" value={money(session?.unrealized, true)} tone={toneFor(session?.unrealized)}/><Metric label="Zamknięte dziś" value={`${session?.closed_today || 0} · WR ${session?.winrate_today || 0}%`}/><Metric label="Reżim" value={session?.regime || "—"}/><Metric label="Dzienny limit" value={`${pct(session?.daily_pct)} / -${session?.daily_limit_pct || 5}%`}/></div><Pill tone={session?.kill_switch ? "bad" : "good"}>{session?.kill_switch ? "KILL SWITCH AKTYWNY" : "ZABEZPIECZENIA AKTYWNE"}</Pill></Card>
    </div>
    <div className="desk-mid">
      <Card title="Najlepsi kandydaci"><CandidateTable rows={data?.candidates || []} onSymbol={onSymbol}/></Card>
      <Card title="Watchlista · BloFin" className="watch-card"><div className="watch-grid">{["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","AVAX"].map(symbol => <button key={symbol} onClick={() => onSymbol(symbol)}><span>{symbol}/USDT</span><strong>{num(data?.prices[symbol], data?.prices[symbol] && data.prices[symbol] < 10 ? 5 : 2)}</strong><Sparkline values={data?.sparklines?.[symbol] || []}/></button>)}</div></Card>
    </div>
    <Card title="Zdarzenia" className="events-card"><table><caption className="visually-hidden">Najnowsze zdarzenia bota</caption><thead><tr><th scope="col">Czas</th><th scope="col">Typ</th><th scope="col">Informacja</th></tr></thead><tbody>{data?.events.length ? data.events.map((event, index) => <tr key={`${event.time}-${index}`}><td>{event.time}</td><td><Pill tone="info">{event.tag}</Pill></td><td>{event.text}</td></tr>) : <tr><td colSpan={3}><Empty>Brak ważnych zdarzeń</Empty></td></tr>}</tbody></table></Card>
  </>;
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="sparkline-empty">Oczekiwanie na świece 15m</span>;
  const width = 100;
  const height = 30;
  const low = Math.min(...values);
  const high = Math.max(...values);
  const spread = high - low || 1;
  const points = values.map((value, index) => {
    const x = index / (values.length - 1) * width;
    const y = height - ((value - low) / spread * (height - 4) + 2);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const rising = values[values.length - 1] >= values[0];
  return <svg className={`sparkline ${rising ? "rising" : "falling"}`} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`Zmiana z ostatnich ${values.length} świec 15-minutowych: ${rising ? "wzrost" : "spadek"}`}><polyline points={points}/></svg>;
}

function CandidateTable({ rows, onSymbol }: { rows: Candidate[]; onSymbol: (symbol: string) => void }) {
  return <table><caption className="visually-hidden">Kandydaci do wejścia</caption><thead><tr><th scope="col">Para</th><th scope="col">Status</th><th scope="col">Ocena</th><th scope="col">R:R</th></tr></thead><tbody>{rows.length ? rows.map(candidate => <tr key={candidate.sym} onDoubleClick={() => onSymbol(candidate.sym)}><th scope="row"><button className="symbol-link" onClick={() => onSymbol(candidate.sym)}>{candidate.sym}</button></th><td><Gate gate={candidate.gate}/></td><td>{num(candidate.score, 1)}</td><td>{num(candidate.rr, 2)}</td></tr>) : <tr><td colSpan={4}><Empty>Brak kandydatów</Empty></td></tr>}</tbody></table>;
}
