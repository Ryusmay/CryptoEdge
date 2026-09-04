import { useMemo, useState } from "react";
import type { SortingState } from "@tanstack/react-table";
import { Card, PageHeader } from "../components";
import { ScannerTable } from "../features/scanner/ScannerTable";
import type { Status } from "../types";

export interface ScannerViewProps {
  data: Status | null;
  onSymbol: (symbol: string) => void;
}

export function ScannerView({ data, onSymbol }: ScannerViewProps) {
  const [query, setQuery] = useState("");
  const [gate, setGate] = useState("ALL");
  const [sorting, setSorting] = useState<SortingState>([{ id: "score", desc: true }]);
  const rows = useMemo(() => (data?.candidates || []).filter(candidate =>
    (!query || candidate.sym.includes(query.toUpperCase())) && (gate === "ALL" || candidate.gate === gate)
  ), [data, query, gate]);

  return <>
    <PageHeader title="Skaner rynku" description="Kandydaci z rzeczywistego lejka decyzyjnego DAYTRADING_V2" context={`${rows.length} WIDOCZNYCH`}/>
    <div className="toolbar"><label htmlFor="pair-search">Szukaj pary</label><input id="pair-search" value={query} onChange={event => setQuery(event.target.value)} placeholder="np. ETH"/><div className="segmented" aria-label="Filtr statusu">{["ALL", "OPEN", "WAIT", "BLOCK"].map(value => <button key={value} aria-pressed={gate === value} onClick={() => setGate(value)}>{value === "ALL" ? "Wszystkie" : value === "OPEN" ? "Gotowe" : value === "WAIT" ? "Czekają" : "Odrzucone"}</button>)}</div></div>
    <Card title="Lista kandydatów" className="fill-card"><ScannerTable rows={rows} onSymbol={onSymbol} sorting={sorting} onSortingChange={setSorting}/></Card>
  </>;
}
