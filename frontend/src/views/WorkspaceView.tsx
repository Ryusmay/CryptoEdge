import { lazy, Suspense, useCallback, useState } from "react";
import type { SortingState } from "@tanstack/react-table";
import { Empty, Gate, Metric, PageHeader, Pill, money, num, pct, toneFor } from "../components";
import { ScannerTable } from "../features/scanner/ScannerTable";
import { WorkspaceGrid, type WidgetId, type WorkspaceId } from "../features/workspace";
import type { Status } from "../types";

const MarketChart = lazy(() => import("../features/chart/MarketChart").then((module) => ({ default: module.MarketChart })));

interface WorkspaceViewProps {
  data: Status | null;
  workspaceId: WorkspaceId;
  symbol: string;
  onSymbol: (symbol: string) => void;
}

export function WorkspaceView({ data, workspaceId, symbol, onSymbol }: WorkspaceViewProps) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "score", desc: true }]);
  const renderWidget = useCallback((id: WidgetId) => {
    const market = data?.market?.[symbol];
    switch (id) {
      case "market-chart":
        return market?.candles.length || (data?.sparklines?.[symbol]?.length ?? 0) > 1
          ? <Suspense fallback={<Empty>Ładowanie wykresu…</Empty>}><MarketChart symbol={symbol} candles={market?.candles} values={data?.sparklines?.[symbol] ?? []}
              levels={market ? { entry: market.levels.find((level) => level.kind === "entry")?.price, stopLoss: market.levels.find((level) => level.kind === "stop")?.price, takeProfits: market.levels.filter((level) => level.kind === "target").map((level) => level.price) } : undefined}
              markers={market?.markers.filter((marker) => marker.kind === "entry" || marker.kind === "exit").map((marker) => ({ time: marker.time, kind: marker.kind as "entry" | "exit", side: marker.side, label: marker.label }))}/></Suspense>
          : <Empty>Oczekiwanie na świece {symbol}/USDT</Empty>;
      case "scanner":
        return <ScannerTable rows={data?.candidates ?? []} onSymbol={onSymbol} sorting={sorting} onSortingChange={setSorting} />;
      case "signal-history":
        return data?.ui?.signals.rows.length ? <table><thead><tr><th>Para</th><th>Strona</th><th>Gate</th><th>Silnik</th><th>Score</th></tr></thead><tbody>{data.ui.signals.rows.map((row, index) => <tr key={`${row.time}-${row.symbol}-${index}`}><th>{row.symbol}</th><td>{row.side || "—"}</td><td><Pill tone={row.gate === "ready" ? "good" : "warn"}>{row.gate}</Pill></td><td>{row.engine}</td><td>{num(row.score, 1)}</td></tr>)}</tbody></table> : <Empty>Brak telemetrii sygnałów</Empty>;
      case "positions":
        return data?.positions.length ? <table><thead><tr><th>Para</th><th>Strona</th><th>PnL</th></tr></thead><tbody>{data.positions.map((position) => <tr key={position.symbol}><th><button className="symbol-link" onClick={() => onSymbol(position.symbol)}>{position.symbol}</button></th><td>{position.side}</td><td className={toneFor(position.pnl)}>{money(position.pnl, true)}</td></tr>)}</tbody></table> : <Empty>Brak otwartych pozycji</Empty>;
      case "exposure": {
        const exposure = data?.ui?.exposure;
        return exposure ? <div className="metric-list"><Metric label="Gross" value={money(exposure.gross)}/><Metric label="Net" value={money(exposure.net, true)} tone={toneFor(exposure.net)}/><Metric label="Long" value={money(exposure.long)}/><Metric label="Short" value={money(exposure.short)}/><Metric label="Pozycje" value={exposure.positions}/></div> : <Empty>Brak modelu ekspozycji</Empty>;
      }
      case "risk-overview":
        return <div className="metric-list"><Metric label="Użyte ryzyko" value={pct(data?.session.used_pct)} tone={data?.session.kill_switch ? "bad" : "good"}/><Metric label="Limit dnia" value={`-${data?.session.daily_limit_pct ?? 5}%`}/><Metric label="Kill switch" value={data?.session.kill_switch ? "AKTYWNY" : "Gotowy"} tone={data?.session.kill_switch ? "bad" : "good"}/></div>;
      case "equity-curve":
        return <div className="metric-list"><Metric label="Equity" value={money(data?.ui?.equity.current_equity ?? data?.session.equity)}/><Metric label="Szczyt" value={money(data?.ui?.equity.peak_equity)}/><Metric label="Max drawdown" value={money(data?.ui?.equity.max_drawdown, true)} tone="bad"/><Metric label="Punkty" value={data?.ui?.equity.points.length ?? 0}/></div>;
      case "drawdown":
        return <div className="metric-list"><Metric label="Max drawdown" value={money(data?.ui?.equity.max_drawdown, true)} tone="bad"/><Metric label="Max drawdown %" value={pct(-(data?.ui?.equity.max_drawdown_pct ?? 0))} tone="bad"/><Metric label="Zmiana dnia" value={pct(data?.session.daily_pct)} tone={toneFor(data?.session.daily_pct)}/></div>;
      case "system-events":
        return data?.events.length ? <ul className="workspace-events">{data.events.slice(0, 12).map((event, index) => <li key={`${event.time}-${index}`}><Pill tone="info">{event.tag ?? "INFO"}</Pill><span>{event.text}</span></li>)}</ul> : <Empty>Brak zdarzeń systemowych</Empty>;
      case "reconciliation": {
        const reconciliation = data?.ui?.reconciliation;
        if (!reconciliation) return <Empty>Brak danych reconciliation</Empty>;
        return <><Pill tone={reconciliation.mismatch_count ? "bad" : reconciliation.status === "unknown" ? "warn" : "good"}>{reconciliation.status.toUpperCase()}</Pill><div className="metric-list"><Metric label="Rozbieżności" value={reconciliation.mismatch_count} tone={reconciliation.mismatch_count ? "bad" : "good"}/><Metric label="Ostatnia kontrola" value={reconciliation.checked_at || "—"}/></div>{reconciliation.mismatches.length > 0 && <ul className="workspace-events">{reconciliation.mismatches.map((item, index) => <li key={`${item.symbol}-${item.kind}-${index}`}><Pill tone="bad">{item.symbol}</Pill><span>{item.kind}: {item.detail}</span></li>)}</ul>}</>;
      }
      case "decision-funnel": {
        const candidate = data?.candidates.find((item) => item.sym === symbol);
        return <div className="metric-list"><Metric label="Status" value={<Gate gate={candidate?.gate}/>}/><Metric label="Ocena" value={num(candidate?.score, 1)}/><Metric label="R:R" value={num(candidate?.rr, 2)}/></div>;
      }
      case "market-context":
        return <div className="metric-list"><Metric label="Para" value={`${symbol}/USDT`}/><Metric label="Cena" value={num(data?.prices[symbol], 6)}/><Metric label="Reżim" value={data?.session.regime ?? "—"}/></div>;
      case "order-entry":
        return <Empty>Panel zleceń pozostaje zablokowany w PAPER UI do czasu osobnego audytu granicy egzekucji.</Empty>;
    }
  }, [data, onSymbol, sorting, symbol]);

  return <>
    <PageHeader title="Workspace" description="Modułowy pulpit rynku, ryzyka i badań" context={workspaceId.toUpperCase()}/>
    <WorkspaceGrid workspaceId={workspaceId} renderWidget={renderWidget}/>
  </>;
}
