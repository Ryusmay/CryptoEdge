import { lazy, Suspense } from "react";
import { BarChart3 } from "lucide-react";
import { Card, Empty, Gate, Metric, PageHeader, Pill, num } from "../components";
import type { Status } from "../types";

const MarketChart = lazy(() => import("../features/chart/MarketChart").then((module) => ({ default: module.MarketChart })));

export interface LabViewProps {
  data: Status | null;
  symbol: string;
  setSymbol: (symbol: string) => void;
}

export function LabView({ data, symbol, setSymbol }: LabViewProps) {
  const candidate = data?.candidates.find((item) => item.sym === symbol);
  const chartValues = data?.sparklines?.[symbol] || [];
  const market = data?.market?.[symbol];
  const chartLevels = market ? {
    entry: market.levels.find((level) => level.kind === "entry")?.price,
    stopLoss: market.levels.find((level) => level.kind === "stop")?.price,
    takeProfits: market.levels.filter((level) => level.kind === "target").map((level) => level.price),
  } : undefined;
  const chartMarkers = market?.markers
    .filter((marker) => marker.kind === "entry" || marker.kind === "exit")
    .map((marker) => ({ time: marker.time, kind: marker.kind as "entry" | "exit", side: marker.side, label: marker.label }));
  const symbols = Array.from(new Set([symbol, ...(data?.candidates.map((item) => item.sym) || []), "BTC", "ETH", "SOL", "XRP"]));

  return <>
    <PageHeader title="Laboratorium sygnału" description="Wykres i uzasadnienie decyzji dla wybranej pary" context="DANE BLOFIN" />
    <div className="toolbar">
      <label htmlFor="lab-symbol">Para</label>
      <select id="lab-symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)}>
        {symbols.map((item) => <option key={item}>{item}</option>)}
      </select>
      <Pill tone={candidate?.gate === "OPEN" ? "good" : candidate?.gate === "BLOCK" ? "bad" : "warn"}>
        {candidate ? `${candidate.side || "—"} · ${candidate.gate}` : "OBSERWACJA"}
      </Pill>
    </div>
    <div className="lab-grid">
      <Card title={`Wykres rynkowy · ${symbol}/USDT`} className="chart-card">
        <div className="timeframes" aria-label="Interwał wykresu">
          <Pill tone="info">15m · AKTYWNY</Pill>
          <span className="visually-hidden">Inne interwały nie są jeszcze podłączone do strumienia rynku.</span>
        </div>
        {(market?.candles.length || chartValues.length > 1)
          ? <Suspense fallback={<div className="chart-placeholder" role="status">Ładowanie silnika wykresu…</div>}>
              <MarketChart symbol={symbol} values={chartValues} candles={market?.candles} levels={chartLevels} markers={chartMarkers} />
            </Suspense>
          : <div className="chart-placeholder" role="status">
              <BarChart3 size={42} aria-hidden="true" />
              <strong>{symbol}/USDT</strong>
              <span>Oczekiwanie na dane świecowe z feedu BloFin.</span>
            </div>}
      </Card>
      <aside className="analysis-stack" aria-label="Analiza sygnału">
        <Card title="Decyzja">
          <Metric label="Status" value={<Gate gate={candidate?.gate} />} />
          <Metric label="Kierunek" value={candidate?.side || "—"} />
          <Metric label="Ocena" value={num(candidate?.score, 1)} />
          <Metric label="Oczekiwane R" value={num(candidate?.rr, 2)} />
        </Card>
        <Card title="Breakdown sygnału"><Empty>Pełna telemetria LAB wymaga rozszerzonego endpointu analizy.</Empty></Card>
        <Card title="Plan transakcji"><Empty>Brak zatwierdzonego setupu wejścia.</Empty></Card>
      </aside>
    </div>
  </>;
}
