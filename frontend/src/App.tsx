import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { Activity, BarChart3, FlaskConical, History, Play, Radar, Settings, Shield, Square } from "lucide-react";
import { engineAction, getBlofinCredentialsStatus, getReplayStatus, getStatus, startReplay, updateBlofinCredentials, type BlofinCredentialsStatus, type ReplayStatus } from "./api";
import { Card, Empty, Gate, Metric, PageHeader, Pill, money, num, pct, toneFor } from "./components";
import type { Candidate, Liveness, Status, View } from "./types";

function secs(value?: number | null) {
  if (value == null) return "—";
  return value < 90 ? `${Math.round(value)}s` : `${Math.round(value / 60)}min`;
}

/** Zamrozona petla wygladala w UI jak zdrowy silnik przez 7,5 h (25.08.2026),
 *  bo /api/status serwowal ostatni znany stan bez informacji, ile ma lat. */
function LoopPill({ live }: { live?: Liveness }) {
  if (!live || live.state === "off") return null;
  if (live.state === "frozen") return <Pill tone="bad">PĘTLA STOI · {secs(live.cycle_age_s)}</Pill>;
  if (live.state === "degraded") return <Pill tone="warn">CENY SPRZED {secs(live.price_map_age_s)}</Pill>;
  if (live.state === "starting") return <Pill tone="info">START…</Pill>;
  if (live.state === "unknown") return <Pill tone="warn">CYKL: BRAK ODCZYTU</Pill>;
  return <Pill tone="good">CYKL {secs(live.cycle_age_s)}</Pill>;
}

const nav: Array<[View, string, typeof Activity]> = [
  ["desk", "Pulpit", Activity], ["scan", "Skaner", Radar], ["lab", "Analiza", FlaskConical],
  ["replay", "Replay", Play], ["history", "Historia", History], ["settings", "Ustawienia", Settings],
];

function useEngine() {
  const [data, setData] = useState<Status | null>(null);
  const [connected, setConnected] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true; let controller: AbortController | null = null;
    const tick = async () => {
      controller?.abort(); controller = new AbortController();
      try { const next = await getStatus(controller.signal); if (active) { setData(next); setConnected(true); } }
      catch (error) { if (active && (error as Error).name !== "AbortError") setConnected(false); }
    };
    tick(); const timer = window.setInterval(tick, 1000);
    return () => { active = false; controller?.abort(); clearInterval(timer); };
  }, []);
  const act = useCallback(async (action: string, confirm = false) => {
    const success: Record<string, string> = {
      start_trading: "Uruchamianie bota — postęp jest widoczny w Zdarzeniach.",
      stop: "Bot został zatrzymany.",
      close_all: "Wysłano polecenie zamknięcia wszystkich pozycji.",
    };
    try { await engineAction(action, confirm); setMessage(success[action] || "Polecenie zostało wykonane."); }
    catch { setMessage("Nie udało się wykonać polecenia. Szczegóły zapisano w logach."); }
  }, []);
  return { data, connected, message, setMessage, act };
}

export default function App() {
  const [view, setView] = useState<View>("desk");
  const [selected, setSelected] = useState("BTC");
  const { data, connected, message, setMessage, act } = useEngine();
  const engine = data?.engine;
  const start = () => act("start_trading", true);
  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span>₿</span><b>CryptoEdge</b><small>v{data?.version || "—"}</small></div>
      <Pill tone={engine?.mode === "LIVE" ? "bad" : "good"}>{engine?.mode || "PAPER"}</Pill>
      <Pill tone={engine?.analysis ? "good" : "muted"}>{engine?.loading ? "ANALIZA: ŁADOWANIE" : `ANALIZA: ${engine?.analysis ? "ON" : "OFF"}`}</Pill>
      <Pill tone={engine?.trading ? "good" : engine?.paused ? "warn" : "muted"}>HANDEL: {engine?.trading ? "ON" : engine?.paused ? "PAUZA" : "OFF"}</Pill>
      <LoopPill live={data?.liveness} />
      <div className="top-spacer" />
      <Pill tone={connected ? "good" : "bad"}>{connected ? "BLOFIN API · POŁĄCZONO" : "SILNIK · ROZŁĄCZONY"}</Pill>
      <button className="btn good" onClick={start}><Play size={14}/>Start</button>
      <button className="btn danger" onClick={() => act("stop", true)}><Square size={13}/>Stop</button>
      <Metric label="KAPITAŁ" value={money(data?.account.equity)} />
    </header>
    {!connected && <div className="connection-alert" role="alert">Brak połączenia z silnikiem Python. Ponawiam automatycznie.</div>}
    <div className="workspace">
      <aside className="sidebar">
        <nav aria-label="Główne widoki"><ul role="list">{nav.map(([id, label, Icon]) => <li key={id}><button aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}><Icon size={17}/><span>{label}</span></button></li>)}</ul></nav>
        <div className="sidebar-foot"><Shield size={16}/><span>Risk Engine<br/><b>{data?.session.kill_switch ? "KILL SWITCH" : "AKTYWNY"}</b></span></div>
      </aside>
      <main id="main" tabIndex={-1} className="main-content">
        {view === "desk" && <Desk data={data} onSymbol={(s) => { setSelected(s); setView("lab"); }} act={act}/>} 
        {view === "scan" && <Scan data={data} onSymbol={(s) => { setSelected(s); setView("lab"); }}/>} 
        {view === "lab" && <Lab data={data} symbol={selected} setSymbol={setSelected}/>} 
        {view === "replay" && <Replay/>}
        {view === "history" && <TradeHistory data={data}/>} 
        {view === "settings" && <SettingsPage data={data}/>} 
      </main>
    </div>
    {message && <div className="toast" role="status"><span>{message}</span><button onClick={() => setMessage("")} aria-label="Zamknij komunikat">×</button></div>}
  </div>;
}

function Desk({ data, onSymbol, act }: { data: Status | null; onSymbol: (s: string) => void; act: (a: string, c?: boolean) => void }) {
  const s = data?.session, a = data?.account;
  return <><PageHeader title="Pulpit operacyjny" description="Pozycje, rynek i aktywne ryzyko w jednym miejscu" context={`${s?.regime || "REŻIM —"} · ${s?.uptime || "—"}`}/>
    <div className="desk-top">
      <Card title={`Otwarte pozycje · ${s?.positions || 0}/${s?.max_positions || 0}`} className="positions" action={<button className="text-action danger-text" onClick={() => act("close_all", true)}>Zamknij wszystkie</button>}>
        <table><caption className="visually-hidden">Otwarte pozycje</caption><thead><tr><th scope="col">Para</th><th scope="col">Kierunek</th><th scope="col">Wejście</th><th scope="col">SL</th><th scope="col">Zysk przy SL</th><th scope="col">PnL</th><th scope="col">Wiek</th></tr></thead>
          <tbody>{data?.positions.length ? data.positions.map(p => <tr key={p.symbol} onDoubleClick={() => onSymbol(p.symbol)}><th scope="row">{p.symbol}</th><td className={p.side === "SHORT" ? "bad" : "good"}>{p.side}</td><td>{num(p.entry, 6)}</td><td>{p.sl_mark} {num(p.sl, 6)}</td><td className={toneFor(p.pnl_at_stop)}>{money(p.pnl_at_stop, true)}</td><td className={toneFor(p.pnl)}>{money(p.pnl, true)} <small>{pct(p.pnl_pct)}</small></td><td>{p.age || "—"}</td></tr>) : <tr><td colSpan={7}><Empty>Brak otwartych pozycji</Empty></td></tr>}</tbody>
        </table>
      </Card>
      <Card title="PnL, sesja i ryzyko" className="session-card combined-account"><div className="session-result"><strong className={`hero-number ${toneFor(s?.daily)}`}>{money(s?.daily, true)}</strong><small>{pct(s?.daily_pct)} dzisiaj</small></div><div className="risk-row"><div className="risk-ring" style={{ "--risk": `${s?.used_pct || 0}%` } as CSSProperties}><b>{Math.round(s?.used_pct || 0)}%</b></div><div><Metric label="Użyty margin" value={money(a?.margin)}/><Metric label="Wolne środki" value={money(a?.available)}/></div></div><div className="metric-list"><Metric label="Equity" value={money(s?.equity)}/><Metric label="Niezrealizowany" value={money(s?.unrealized, true)} tone={toneFor(s?.unrealized)}/><Metric label="Zamknięte dziś" value={`${s?.closed_today || 0} · WR ${s?.winrate_today || 0}%`}/><Metric label="Reżim" value={s?.regime || "—"}/><Metric label="Dzienny limit" value={`${pct(s?.daily_pct)} / -${s?.daily_limit_pct || 5}%`}/></div><Pill tone={s?.kill_switch ? "bad" : "good"}>{s?.kill_switch ? "KILL SWITCH AKTYWNY" : "ZABEZPIECZENIA AKTYWNE"}</Pill></Card>
    </div>
    <div className="desk-mid">
      <Card title="Najlepsi kandydaci"><CandidateTable rows={data?.candidates || []} onSymbol={onSymbol}/></Card>
      <Card title="Watchlista · BloFin" className="watch-card"><div className="watch-grid">{["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","AVAX"].map(sym => <button key={sym} onClick={() => onSymbol(sym)}><span>{sym}/USDT</span><strong>{num(data?.prices[sym], data?.prices[sym] && data.prices[sym] < 10 ? 5 : 2)}</strong><Sparkline values={data?.sparklines?.[sym] || []}/></button>)}</div></Card>
    </div>
    <Card title="Zdarzenia" className="events-card"><table><caption className="visually-hidden">Najnowsze zdarzenia bota</caption><thead><tr><th scope="col">Czas</th><th scope="col">Typ</th><th scope="col">Informacja</th></tr></thead><tbody>{data?.events.length ? data.events.map((e,i)=><tr key={`${e.time}-${i}`}><td>{e.time}</td><td><Pill tone="info">{e.tag}</Pill></td><td>{e.text}</td></tr>) : <tr><td colSpan={3}><Empty>Brak ważnych zdarzeń</Empty></td></tr>}</tbody></table></Card>
  </>;
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="sparkline-empty">Oczekiwanie na świece 15m</span>;
  const width = 100, height = 30;
  const low = Math.min(...values), high = Math.max(...values), spread = high - low || 1;
  const points = values.map((value, index) => {
    const x = index / (values.length - 1) * width;
    const y = height - ((value - low) / spread * (height - 4) + 2);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const rising = values[values.length - 1] >= values[0];
  return <svg className={`sparkline ${rising ? "rising" : "falling"}`} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`Zmiana z ostatnich ${values.length} świec 15-minutowych: ${rising ? "wzrost" : "spadek"}`}><polyline points={points}/></svg>;
}

function CandidateTable({ rows, onSymbol }: { rows: Candidate[]; onSymbol: (s: string) => void }) { return <table><caption className="visually-hidden">Kandydaci do wejścia</caption><thead><tr><th scope="col">Para</th><th scope="col">Status</th><th scope="col">Ocena</th><th scope="col">R:R</th></tr></thead><tbody>{rows.length ? rows.map(c=><tr key={c.sym} onDoubleClick={() => onSymbol(c.sym)}><th scope="row"><button className="symbol-link" onClick={() => onSymbol(c.sym)}>{c.sym}</button></th><td><Gate gate={c.gate}/></td><td>{num(c.score,1)}</td><td>{num(c.rr,2)}</td></tr>) : <tr><td colSpan={4}><Empty>Brak kandydatów</Empty></td></tr>}</tbody></table>; }

function Scan({ data, onSymbol }: { data: Status | null; onSymbol: (s:string)=>void }) {
  const [query,setQuery]=useState(""); const [gate,setGate]=useState("ALL");
  const rows=useMemo(()=>(data?.candidates||[]).filter(c=>(!query||c.sym.includes(query.toUpperCase()))&&(gate==="ALL"||c.gate===gate)),[data,query,gate]);
  return <><PageHeader title="Skaner rynku" description="Kandydaci z rzeczywistego lejka decyzyjnego DAYTRADING_V2" context={`${rows.length} WIDOCZNYCH`}/><div className="toolbar"><label htmlFor="pair-search">Szukaj pary</label><input id="pair-search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="np. ETH"/><div className="segmented" aria-label="Filtr statusu">{["ALL","OPEN","WAIT","BLOCK"].map(v=><button key={v} aria-pressed={gate===v} onClick={()=>setGate(v)}>{v==="ALL"?"Wszystkie":v==="OPEN"?"Gotowe":v==="WAIT"?"Czekają":"Odrzucone"}</button>)}</div></div><Card title="Lista kandydatów" className="fill-card"><CandidateTable rows={rows} onSymbol={onSymbol}/></Card></>;
}

function Lab({ data, symbol, setSymbol }: { data: Status|null; symbol:string; setSymbol:(s:string)=>void }) {
  const candidate=data?.candidates.find(c=>c.sym===symbol);
  return <><PageHeader title="Laboratorium sygnału" description="Wykres i uzasadnienie decyzji dla wybranej pary" context="DANE BLOFIN"/><div className="toolbar"><label htmlFor="lab-symbol">Para</label><select id="lab-symbol" value={symbol} onChange={e=>setSymbol(e.target.value)}>{Array.from(new Set([symbol,...(data?.candidates.map(c=>c.sym)||[]),"BTC","ETH","SOL","XRP"])).map(s=><option key={s}>{s}</option>)}</select><Pill tone={candidate?.gate==="OPEN"?"good":candidate?.gate==="BLOCK"?"bad":"warn"}>{candidate?`${candidate.side||"—"} · ${candidate.gate}`:"OBSERWACJA"}</Pill></div><div className="lab-grid"><Card title={`Wykres świecowy · ${symbol}/USDT`} className="chart-card"><div className="timeframes">{["5m","15m","1h","4h","1d"].map((tf,i)=><button className={i===1?"active":""} key={tf}>{tf}</button>)}</div><div className="chart-placeholder"><BarChart3 size={42}/><strong>{symbol}/USDT</strong><span>Endpoint świecowy zostanie podłączony do istniejącego feedu BloFin w następnym etapie.</span></div></Card><aside className="analysis-stack"><Card title="Decyzja"><Metric label="Status" value={<Gate gate={candidate?.gate}/>} /><Metric label="Kierunek" value={candidate?.side||"—"}/><Metric label="Ocena" value={num(candidate?.score,1)}/><Metric label="Oczekiwane R" value={num(candidate?.rr,2)}/></Card><Card title="Breakdown sygnału"><Empty>Pełna telemetria LAB wymaga rozszerzonego endpointu analizy.</Empty></Card><Card title="Plan transakcji"><Empty>Brak zatwierdzonego setupu wejścia.</Empty></Card></aside></div></>;
}

function Replay(){
  const [status,setStatus]=useState<ReplayStatus|null>(null), [error,setError]=useState("");
  const [days,setDays]=useState(90), [oos,setOos]=useState(30), [limit,setLimit]=useState(10), [mode,setMode]=useState("LIQUID"), [symbols,setSymbols]=useState("BTC, ETH, SOL");
  useEffect(()=>{let active=true,controller:AbortController|null=null;const poll=async()=>{controller?.abort();controller=new AbortController();try{const next=await getReplayStatus(controller.signal);if(active)setStatus(next)}catch(e){if(active&&(e as Error).name!=="AbortError")setError("Brak połączenia ze statusem replay")}};poll();const timer=window.setInterval(poll,1000);return()=>{active=false;controller?.abort();clearInterval(timer)}},[]);
  const run=async()=>{setError("");try{await startReplay({universe_mode:mode,days,oos_fraction:oos/100,liquid_limit:limit,symbols:symbols.split(",").map(s=>s.trim()).filter(Boolean)});setStatus(await getReplayStatus())}catch(e){setError((e as Error).message)}};
  const result=status?.result;
  return <><PageHeader title="Replay historyczny" description="Backtest tej samej strategii na zamkniętych świecach BloFin" context={status?.running?"TEST W TOKU":status?.phase==="complete"?"TEST ZAKOŃCZONY":"NARZĘDZIE BADAWCZE"}/>
    <Card title="Konfiguracja sesji"><div className="form-grid replay-form"><label>Uniwersum<select value={mode} disabled={status?.running} onChange={e=>setMode(e.target.value)}><option>MANUAL</option><option>LIQUID</option><option>ALL</option></select></label>{mode==="MANUAL"&&<label className="replay-manual">Monety<input value={symbols} disabled={status?.running} onChange={e=>setSymbols(e.target.value)} placeholder="BTC, ETH, SOL"/></label>}<label>Dni<input type="number" min={7} max={365} value={days} disabled={status?.running} onChange={e=>setDays(Number(e.target.value))}/></label><label>Out-of-sample %<input type="number" min={10} max={50} value={oos} disabled={status?.running} onChange={e=>setOos(Number(e.target.value))}/></label><label>Limit monet<input type="number" min={1} max={100} value={limit} disabled={status?.running||mode!=="LIQUID"} onChange={e=>setLimit(Number(e.target.value))}/></label><button className="btn good" disabled={status?.running||(mode==="MANUAL"&&!symbols.trim())} onClick={run}><Play size={14}/>{status?.running?"Replay trwa":"Uruchom replay"}</button></div>{error&&<p className="replay-error" role="alert">{error}</p>}</Card>
    {(status?.running||status?.phase==="complete"||status?.phase==="error")&&<section className={`replay-status ${status.running?"is-running":status.phase}`} aria-live="polite" aria-busy={status.running}><div className="replay-status-head"><div className="replay-spinner" aria-hidden="true"/><div><strong>{status.message}</strong><span>{status.running?`Czas: ${Math.floor(status.elapsed_s/60)}m ${status.elapsed_s%60}s · ${status.completed}/${status.total||"?"} monet`:status.error||"Wynik został zapisany"}</span></div><b>{status.progress}%</b></div><progress max="100" value={status.progress}>{status.progress}%</progress>{status.current_symbol&&status.running&&<small>Aktualnie: {status.current_symbol}</small>}</section>}
    <div className="kpi-grid"><Metric label="Transakcje OOS" value={result?.trades_oos??"—"}/><Metric label="Winrate OOS" value={result?pct(result.win_rate_oos*100):"—"}/><Metric label="Net R OOS" value={result?`${num(result.net_r_oos,2)}R`:"—"} tone={toneFor(result?.net_r_oos)}/><Metric label="Profit factor" value={result?.profit_factor_oos==null?"—":num(result.profit_factor_oos,2)}/></div>
    {status?.phase==="complete"&&status.result&&<Card title="Wynik replay" className="replay-result"><div className="replay-result-grid"><section><span>IN-SAMPLE · budowa</span><strong>{status.result.trades_is} transakcji</strong><Metric label="Win rate" value={status.result.win_rate_is==null?"—":pct(status.result.win_rate_is*100)}/><Metric label="Net R" value={status.result.net_r_is==null?"—":`${num(status.result.net_r_is,3)}R`} tone={toneFor(status.result.net_r_is)}/><Metric label="Średni wynik" value={status.result.avg_r_is==null?"—":`${num(status.result.avg_r_is,3)}R`} tone={toneFor(status.result.avg_r_is)}/><Metric label="Profit factor" value={status.result.profit_factor_is==null?"—":num(status.result.profit_factor_is,2)}/><Metric label="Max drawdown" value={status.result.max_drawdown_r_is==null?"—":`${num(status.result.max_drawdown_r_is,3)}R`}/></section><section className={status.result.trades_oos===0?"no-trades":""}><span>OUT-OF-SAMPLE · walidacja</span><strong>{status.result.trades_oos} transakcji</strong>{status.result.trades_oos===0?<p>Brak wejść w części walidacyjnej. Nie można ocenić win rate ani expectancy OOS — wynik strategii nie jest jeszcze potwierdzony.</p>:<><Metric label="Win rate" value={pct(status.result.win_rate_oos*100)}/><Metric label="Net R" value={`${num(status.result.net_r_oos,3)}R`} tone={toneFor(status.result.net_r_oos)}/><Metric label="Średni wynik" value={status.result.avg_r_oos==null?"—":`${num(status.result.avg_r_oos,3)}R`} tone={toneFor(status.result.avg_r_oos)}/><Metric label="Profit factor" value={status.result.profit_factor_oos==null?"—":num(status.result.profit_factor_oos,2)}/><Metric label="Max drawdown" value={status.result.max_drawdown_r_oos==null?"—":`${num(status.result.max_drawdown_r_oos,3)}R`}/></>}</section></div><small className="replay-report-path">Raport: {status.result.report_path||"zapisany lokalnie"}</small></Card>}
    <Card title={`Skanowane monety · ${status?.symbols.length||0}`} className="replay-symbols"><table><caption className="visually-hidden">Postęp skanowania monet w replay</caption><thead><tr><th>Moneta</th><th>Status</th><th>Etap / dane</th><th>Świece 5m</th><th>Transakcje OOS</th><th>Net R OOS</th></tr></thead><tbody>{status?.symbols.length?status.symbols.map(row=><tr key={row.symbol}><th>{row.symbol}</th><td><Pill tone={row.status==="Przeskanowana"?"good":row.status==="Pominięta"?"bad":row.status==="Dane gotowe"?"info":"warn"}>{row.status}</Pill></td><td>{row.detail}</td><td>{row.bars_5m||"—"}</td><td>{row.trades_oos??"—"}</td><td className={toneFor(row.net_r_oos)}>{row.net_r_oos==null?"—":`${num(row.net_r_oos,3)}R`}</td></tr>):<tr><td colSpan={6}><Empty>Uruchom replay, aby rozpocząć skanowanie monet</Empty></td></tr>}</tbody></table></Card>
  </>
}

function TradeHistory({data}:{data:Status|null}){ return <><PageHeader title="Historia transakcji" description="Wyniki zamkniętych pozycji i jakość egzekucji" context="DANE SESJI"/><div className="kpi-grid"><Metric label="Zamknięte dzisiaj" value={data?.session.closed_today||0}/><Metric label="Winrate" value={`${data?.session.winrate_today||0}%`}/><Metric label="PnL dnia" value={money(data?.session.daily,true)} tone={toneFor(data?.session.daily)}/><Metric label="Niezrealizowany" value={money(data?.session.unrealized,true)} tone={toneFor(data?.session.unrealized)}/></div><Card title="Zamknięte transakcje" className="fill-card"><Empty>Historia pełna wymaga endpointu zamkniętych pozycji.</Empty></Card></> }

function SettingsPage({data}:{data:Status|null}){
  const [credentials,setCredentials]=useState<BlofinCredentialsStatus|null>(null),[apiKey,setApiKey]=useState(""),[secret,setSecret]=useState(""),[passphrase,setPassphrase]=useState(""),[busy,setBusy]=useState(false),[message,setMessage]=useState("");
  useEffect(()=>{const controller=new AbortController();getBlofinCredentialsStatus(controller.signal).then(setCredentials).catch(e=>setMessage((e as Error).message));return()=>controller.abort()},[]);
  const submit=async(action:"save"|"test")=>{setBusy(true);setMessage("");try{const next=await updateBlofinCredentials({action,api_key:apiKey,api_secret:secret,passphrase});setCredentials(next);setApiKey("");setSecret("");setPassphrase("");setMessage(next.message||"Zapisano")}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}};
  const clear=async()=>{if(!window.confirm("Usunąć zapisane klucze BloFin z tego komputera?"))return;setBusy(true);try{const next=await updateBlofinCredentials({action:"clear",confirm:true});setCredentials(next);setMessage(next.message||"Usunięto klucze")}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}};
  const complete=Boolean(apiKey&&secret&&passphrase);
  return <><PageHeader title="Ustawienia i połączenie" description="Tryb konta, limity bezpieczeństwa i konfiguracja danych" context="ZAPIS LOKALNY"/><div className="settings-grid"><Card title="Tryb konta"><div className="mode-choice"><button aria-pressed={data?.engine.mode!=="LIVE"}>PAPER</button><button aria-pressed={data?.engine.mode==="LIVE"}>LIVE</button></div><p className="muted-copy">Zmiana trybu wymaga zatrzymanego silnika. LIVE podlega osobnej blokadzie egzekucji.</p></Card><Card title="Limity ryzyka"><Metric label="Dzienny limit straty" value={`-${data?.session.daily_limit_pct||5}%`}/><Metric label="Maks. pozycji" value={data?.session.max_positions||"—"}/><Metric label="Egzekucja LIVE" value={data?.engine.live_execution?"WŁĄCZONA":"ZABLOKOWANA"} tone={data?.engine.live_execution?"bad":"good"}/></Card><Card title="BloFin API" className="credentials-card"><div className="credentials-head"><Pill tone={credentials?.configured?"good":credentials?.partial?"warn":"muted"}>{credentials?.configured?`SKONFIGUROWANO · ${credentials.masked_key}`:credentials?.partial?"NIEKOMPLETNE":"BRAK KLUCZY"}</Pill><span>Tylko lokalny, szyfrowany magazyn</span></div><form onSubmit={e=>{e.preventDefault();submit("save")}} autoComplete="off"><label>API Key<input type="password" value={apiKey} onChange={e=>setApiKey(e.target.value)} autoComplete="new-password" spellCheck={false} placeholder={credentials?.configured?"Wpisz nowy, aby zastąpić zapisany":"Wpisz API Key"}/></label><label>API Secret<input type="password" value={secret} onChange={e=>setSecret(e.target.value)} autoComplete="new-password" spellCheck={false} placeholder="Wpisz API Secret"/></label><label>Passphrase<input type="password" value={passphrase} onChange={e=>setPassphrase(e.target.value)} autoComplete="new-password" spellCheck={false} placeholder="Wpisz Passphrase"/></label><div className="credentials-actions"><button className="btn" type="submit" disabled={busy||!complete}>Zapisz klucze</button><button className="btn good" type="button" disabled={busy||!complete} onClick={()=>submit("test")}>Zapisz i testuj</button><button className="btn danger" type="button" disabled={busy||!credentials?.configured} onClick={clear}>Usuń zapisane</button></div></form>{message&&<p className="credentials-message" role="status">{message}</p>}{credentials?.account&&<div className="credentials-account"><Metric label="Equity" value={`${money(credentials.account.equity)} ${credentials.account.currency}`}/><Metric label="Dostępne" value={`${money(credentials.account.available)} ${credentials.account.currency}`}/><Metric label="Otwarte pozycje" value={credentials.account.open_positions}/></div>}<p className="muted-copy">Test wykonuje wyłącznie zapytania odczytu salda i pozycji. Nie przełącza LIVE i nie składa zleceń.</p></Card><Card title="Interfejs"><Metric label="Główny UI" value="React + Tauri" tone="info"/><Metric label="Awaryjny UI" value="PySide6"/><Metric label="Wersja silnika" value={`v${data?.version||"—"}`}/></Card></div></>
}
