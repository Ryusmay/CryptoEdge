import { useEffect } from "react";
import { Activity, Command, FlaskConical, History, Play, Radar, Settings, Shield, Square } from "lucide-react";
import { Metric, Pill, money } from "./components";
import type { Liveness, View } from "./types";
import { useUiStore } from "./state/uiStore";
import { CommandPalette } from "./features/commands/CommandPalette";
import { ConnectionStatus } from "./features/status";
import { useEngine } from "./hooks/useEngine";
import { useMarketStream } from "./hooks/useMarketStream";
import { SettingsView } from "./views/SettingsView";
import { TradeHistoryView } from "./views/TradeHistoryView";
import { ScannerView } from "./views/ScannerView";
import { LabView } from "./views/LabView";
import { ReplayView } from "./views/ReplayView";
import { WorkspaceView } from "./views/WorkspaceView";

function secs(value?: number | null) {
  if (value == null) return "—";
  return value < 90 ? `${Math.round(value)}s` : `${Math.round(value / 60)}min`;
}

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
const viewTitles: Record<View, string> = { desk: "Pulpit", scan: "Skaner", lab: "Analiza", replay: "Replay", history: "Historia", settings: "Ustawienia" };

export default function App() {
  const view = useUiStore((state) => state.view);
  const selected = useUiStore((state) => state.selectedSymbol);
  const setView = useUiStore((state) => state.setView);
  const setSelected = useUiStore((state) => state.selectSymbol);
  const workspace = useUiStore((state) => state.workspace);
  const setWorkspace = useUiStore((state) => state.setWorkspace);
  const commandPaletteOpen = useUiStore((state) => state.commandPaletteOpen);
  const setCommandPaletteOpen = useUiStore((state) => state.setCommandPaletteOpen);
  const { data, connected, message, setMessage, act } = useEngine();
  useMarketStream();
  const engine = data?.engine;

  useEffect(() => { document.title = `${viewTitles[view]} — CryptoEdge`; }, [view]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandPaletteOpen(!commandPaletteOpen);
      } else if (event.key === "Escape" && commandPaletteOpen) setCommandPaletteOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [commandPaletteOpen, setCommandPaletteOpen]);

  const openSymbol = (symbol: string) => { setSelected(symbol); setView("lab"); };
  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span>₿</span><b>CryptoEdge</b><small>v{data?.version || "—"}</small></div>
      <Pill tone={engine?.mode === "LIVE" ? "bad" : "good"}>{engine?.mode || "PAPER"}</Pill>
      <Pill tone={engine?.analysis ? "good" : "muted"}>{engine?.loading ? "ANALIZA: ŁADOWANIE" : `ANALIZA: ${engine?.analysis ? "ON" : "OFF"}`}</Pill>
      <Pill tone={engine?.trading ? "good" : engine?.paused ? "warn" : "muted"}>HANDEL: {engine?.trading ? "ON" : engine?.paused ? "PAUZA" : "OFF"}</Pill>
      <LoopPill live={data?.liveness}/>
      <label className="workspace-select"><span>Workspace</span><select value={workspace} onChange={(event) => setWorkspace(event.target.value as typeof workspace)}><option value="trading">Trading</option><option value="research">Research</option><option value="risk">Risk</option></select></label>
      <div className="top-spacer"/>
      <button className="btn command-trigger" onClick={() => setCommandPaletteOpen(true)}><Command size={14}/>Polecenia <kbd>Ctrl K</kbd></button>
      <Pill tone={connected ? "good" : "bad"}>{connected ? "BLOFIN API · POŁĄCZONO" : "SILNIK · ROZŁĄCZONY"}</Pill>
      <button className="btn good" onClick={() => void act("start_trading", true)}><Play size={14}/>Start</button>
      <button className="btn danger" onClick={() => void act("stop", true)}><Square size={13}/>Stop</button>
      <Metric label="KAPITAŁ" value={money(data?.account.equity)}/>
    </header>
    {!connected && <div className="connection-alert" role="alert">Brak połączenia z silnikiem Python. Ponawiam automatycznie.</div>}
    <div className="workspace">
      <aside className="sidebar">
        <nav aria-label="Główne widoki"><ul role="list">{nav.map(([id, label, Icon]) => <li key={id}><button aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}><Icon size={17}/><span>{label}</span></button></li>)}</ul></nav>
        <ConnectionStatus/>
        <div className="sidebar-foot"><Shield size={16}/><span>Risk Engine<br/><b>{data?.session.kill_switch ? "KILL SWITCH" : "AKTYWNY"}</b></span></div>
      </aside>
      <main id="main" tabIndex={-1} className="main-content">
        {view === "desk" && <WorkspaceView data={data} workspaceId={workspace} symbol={selected} onSymbol={openSymbol}/>}
        {view === "scan" && <ScannerView data={data} onSymbol={openSymbol}/>}
        {view === "lab" && <LabView data={data} symbol={selected} setSymbol={setSelected}/>}
        {view === "replay" && <ReplayView/>}
        {view === "history" && <TradeHistoryView data={data}/>}
        {view === "settings" && <SettingsView data={data}/>}
      </main>
    </div>
    {message && <div className="toast" role="status"><span>{message}</span><button onClick={() => setMessage("")} aria-label="Zamknij komunikat">×</button></div>}
    {commandPaletteOpen && <CommandPalette candidates={data?.candidates || []} onClose={() => setCommandPaletteOpen(false)} onNavigate={setView} onSymbol={openSymbol} onEngineAction={act}/>}
  </div>;
}
