import { Card, Empty, Metric, PageHeader, money, toneFor } from "../components";
import type { Status } from "../types";
import { TradeHistoryTable } from "../features/history/TradeHistoryTable";

export interface TradeHistoryViewProps {
  data: Status | null;
}

export function TradeHistoryView({ data }: TradeHistoryViewProps) {
  const session = data?.session;

  return (
    <>
      <PageHeader
        title="Historia transakcji"
        description="Wyniki zamkniętych pozycji i jakość egzekucji"
        context="DANE SESJI"
      />
      <div className="kpi-grid">
        <Metric label="Zamknięte dzisiaj" value={session?.closed_today ?? 0} />
        <Metric label="Winrate" value={`${session?.winrate_today ?? 0}%`} />
        <Metric
          label="PnL dnia"
          value={money(session?.daily, true)}
          tone={toneFor(session?.daily)}
        />
        <Metric
          label="Niezrealizowany"
          value={money(session?.unrealized, true)}
          tone={toneFor(session?.unrealized)}
        />
      </div>
      <Card title="Zamknięte transakcje" className="fill-card">
        {data?.ui ? <TradeHistoryTable rows={data.ui.history} /> : <Empty>Oczekiwanie na model historii…</Empty>}
      </Card>
    </>
  );
}
