import { useCallback, useMemo, useState, type ReactNode } from "react";
import { loadWorkspaceLayouts, resetWorkspaceLayouts, saveWorkspaceLayouts, type StorageAdapter } from "./persistence";
import { moveWidget, resizeWidget, type LayoutDirection, type ResizeDirection } from "./layout";
import { WIDGET_DEFINITIONS, type WidgetId, type WorkspaceId, type WorkspaceLayouts } from "./types";
import "./workspace.css";

export interface WorkspaceGridProps {
  workspaceId: WorkspaceId;
  renderWidget: (id: WidgetId) => ReactNode;
  storage?: StorageAdapter;
}

const browserStorage = (): StorageAdapter | undefined =>
  typeof window === "undefined" ? undefined : window.localStorage;

export function WorkspaceGrid({ workspaceId, renderWidget, storage = browserStorage() }: WorkspaceGridProps) {
  const [layouts, setLayouts] = useState<WorkspaceLayouts>(() =>
    storage ? loadWorkspaceLayouts(storage).layouts : loadWorkspaceLayouts(memoryFallback).layouts,
  );
  const layout = layouts[workspaceId];

  const commit = useCallback(
    (next: WorkspaceLayouts) => {
      setLayouts(next);
      if (storage) saveWorkspaceLayouts(storage, next);
    },
    [storage],
  );

  const change = useCallback(
    (id: WidgetId, operation: LayoutDirection | ResizeDirection) => {
      const nextLayout = ["left", "right", "up", "down"].includes(operation)
        ? moveWidget(layout, id, operation as LayoutDirection)
        : resizeWidget(layout, id, operation as ResizeDirection);
      if (nextLayout !== layout) commit({ ...layouts, [workspaceId]: nextLayout });
    },
    [commit, layout, layouts, workspaceId],
  );

  const reset = useCallback(() => {
    const next = storage ? resetWorkspaceLayouts(storage) : loadWorkspaceLayouts(memoryFallback).layouts;
    setLayouts(next);
  }, [storage]);

  const orderedWidgets = useMemo(
    () => [...layout.widgets].sort((a, b) => a.y - b.y || a.x - b.x),
    [layout.widgets],
  );

  return (
    <section className="workspace-shell" aria-label={`Workspace ${workspaceId}`}>
      <div className="workspace-toolbar">
        <span aria-live="polite">Układ: {workspaceId}</span>
        <button type="button" onClick={reset}>Przywróć układ</button>
      </div>
      <div className="workspace-grid">
        {orderedWidgets.map((widget, index) => {
          const definition = WIDGET_DEFINITIONS[widget.id];
          return (
            <section
              className={`workspace-widget${index === 0 ? " workspace-widget--critical" : ""}`}
              key={widget.id}
              aria-labelledby={`workspace-title-${widget.id}`}
              style={{
                gridColumn: `${widget.x + 1} / span ${widget.width}`,
                gridRow: `${widget.y + 1} / span ${widget.height}`,
              }}
            >
              <header className="workspace-widget__header">
                <h2 id={`workspace-title-${widget.id}`}>{definition.title}</h2>
                <div className="workspace-widget__controls" aria-label={`Sterowanie: ${definition.title}`}>
                  <button type="button" onClick={() => change(widget.id, "left")} aria-label="Przesuń w lewo">←</button>
                  <button type="button" onClick={() => change(widget.id, "right")} aria-label="Przesuń w prawo">→</button>
                  <button type="button" onClick={() => change(widget.id, "up")} aria-label="Przesuń w górę">↑</button>
                  <button type="button" onClick={() => change(widget.id, "down")} aria-label="Przesuń w dół">↓</button>
                  <button type="button" onClick={() => change(widget.id, "narrower")} aria-label="Zmniejsz szerokość">−↔</button>
                  <button type="button" onClick={() => change(widget.id, "wider")} aria-label="Zwiększ szerokość">+↔</button>
                  <button type="button" onClick={() => change(widget.id, "shorter")} aria-label="Zmniejsz wysokość">−↕</button>
                  <button type="button" onClick={() => change(widget.id, "taller")} aria-label="Zwiększ wysokość">+↕</button>
                </div>
              </header>
              <div className="workspace-widget__body">{renderWidget(widget.id)}</div>
            </section>
          );
        })}
      </div>
    </section>
  );
}

const memoryFallback: StorageAdapter = {
  getItem: () => null,
  setItem: () => undefined,
  removeItem: () => undefined,
};

