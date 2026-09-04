import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, FlaskConical, History, Play, Radar, Search, Settings, Shield } from "lucide-react";
import type { Candidate, View } from "../../types";

interface PaletteCommand {
  id: string;
  label: string;
  hint: string;
  icon: typeof Activity;
  run: () => void;
}

interface CommandPaletteProps {
  candidates: Candidate[];
  onClose: () => void;
  onNavigate: (view: View) => void;
  onSymbol: (symbol: string) => void;
  onEngineAction: (action: string, confirm?: boolean) => void;
}

const views: Array<[View, string, typeof Activity]> = [
  ["desk", "Otwórz pulpit", Activity], ["scan", "Otwórz skaner", Radar],
  ["lab", "Otwórz laboratorium", FlaskConical], ["replay", "Otwórz replay", Play],
  ["history", "Otwórz historię", History], ["settings", "Otwórz ustawienia", Settings],
];

export function CommandPalette({ candidates, onClose, onNavigate, onSymbol, onEngineAction }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const commands = useMemo<PaletteCommand[]>(() => [
    ...views.map(([view, label, icon]) => ({ id: `view:${view}`, label, hint: "Widok", icon, run: () => onNavigate(view) })),
    ...candidates.slice(0, 80).map(candidate => ({
      id: `symbol:${candidate.sym}`, label: `${candidate.sym}/USDT`,
      hint: `${candidate.side || "—"} · ${candidate.gate || "WAIT"}`, icon: Search,
      run: () => onSymbol(candidate.sym),
    })),
    { id: "engine:start", label: "Uruchom handel", hint: "Wymaga potwierdzenia", icon: Play, run: () => onEngineAction("start_trading", true) },
    { id: "engine:stop", label: "Zatrzymaj handel", hint: "Bez zamykania pozycji", icon: Shield, run: () => onEngineAction("stop", true) },
  ], [candidates, onEngineAction, onNavigate, onSymbol]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("pl");
    return needle ? commands.filter(command => `${command.label} ${command.hint}`.toLocaleLowerCase("pl").includes(needle)) : commands;
  }, [commands, query]);

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => { setActive(0); }, [query]);
  const execute = (command?: PaletteCommand) => {
    if (!command) return;
    command.run();
    onClose();
  };

  return <div className="command-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="command-palette" role="dialog" aria-modal="true" aria-label="Paleta poleceń">
      <div className="command-search"><Search size={18}/><input ref={inputRef} value={query} onChange={event => setQuery(event.target.value)}
        onKeyDown={event => {
          if (event.key === "Escape") onClose();
          else if (event.key === "ArrowDown") { event.preventDefault(); setActive(value => Math.min(value + 1, filtered.length - 1)); }
          else if (event.key === "ArrowUp") { event.preventDefault(); setActive(value => Math.max(value - 1, 0)); }
          else if (event.key === "Enter") { event.preventDefault(); execute(filtered[active]); }
        }} placeholder="Widok, symbol albo polecenie…" aria-label="Szukaj polecenia"/></div>
      <div className="command-results" role="listbox">
        {filtered.length ? filtered.map((command, index) => <button key={command.id} role="option" aria-selected={index === active}
          onMouseEnter={() => setActive(index)} onClick={() => execute(command)}><command.icon size={17}/><span><b>{command.label}</b><small>{command.hint}</small></span></button>)
          : <p>Brak pasujących poleceń</p>}
      </div>
      <footer><span>↑↓ wybór</span><span>Enter uruchom</span><span>Esc zamknij</span></footer>
    </section>
  </div>;
}
