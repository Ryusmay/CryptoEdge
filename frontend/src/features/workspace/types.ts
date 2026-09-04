export const WORKSPACE_IDS = ["trading", "research", "risk"] as const;

export type WorkspaceId = (typeof WORKSPACE_IDS)[number];

export const WIDGET_IDS = [
  "market-chart",
  "scanner",
  "positions",
  "order-entry",
  "decision-funnel",
  "signal-history",
  "market-context",
  "risk-overview",
  "exposure",
  "equity-curve",
  "drawdown",
  "reconciliation",
  "system-events",
] as const;

export type WidgetId = (typeof WIDGET_IDS)[number];

export interface WidgetPlacement {
  id: WidgetId;
  x: number;
  y: number;
  width: number;
  height: number;
  minWidth?: number;
  minHeight?: number;
}

export interface WorkspaceLayout {
  id: WorkspaceId;
  widgets: WidgetPlacement[];
}

export type WorkspaceLayouts = Record<WorkspaceId, WorkspaceLayout>;

export interface WidgetDefinition {
  id: WidgetId;
  title: string;
  minimumSize: Readonly<{ width: number; height: number }>;
}

export const WIDGET_DEFINITIONS: Readonly<Record<WidgetId, WidgetDefinition>> = {
  "market-chart": { id: "market-chart", title: "Wykres rynku", minimumSize: { width: 4, height: 4 } },
  scanner: { id: "scanner", title: "Scanner", minimumSize: { width: 3, height: 4 } },
  positions: { id: "positions", title: "Pozycje", minimumSize: { width: 3, height: 3 } },
  "order-entry": { id: "order-entry", title: "Zlecenie", minimumSize: { width: 2, height: 3 } },
  "decision-funnel": { id: "decision-funnel", title: "Decision funnel", minimumSize: { width: 3, height: 3 } },
  "signal-history": { id: "signal-history", title: "Historia sygnałów", minimumSize: { width: 3, height: 3 } },
  "market-context": { id: "market-context", title: "Kontekst rynku", minimumSize: { width: 3, height: 3 } },
  "risk-overview": { id: "risk-overview", title: "Risk overview", minimumSize: { width: 3, height: 3 } },
  exposure: { id: "exposure", title: "Ekspozycja", minimumSize: { width: 3, height: 3 } },
  "equity-curve": { id: "equity-curve", title: "Equity", minimumSize: { width: 4, height: 3 } },
  drawdown: { id: "drawdown", title: "Drawdown", minimumSize: { width: 3, height: 3 } },
  reconciliation: { id: "reconciliation", title: "Reconciliation", minimumSize: { width: 3, height: 3 } },
  "system-events": { id: "system-events", title: "Zdarzenia systemowe", minimumSize: { width: 3, height: 3 } },
};
