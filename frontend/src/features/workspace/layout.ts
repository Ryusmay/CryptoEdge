import { WIDGET_DEFINITIONS, type WidgetId, type WidgetPlacement, type WorkspaceLayout } from "./types";

export type LayoutDirection = "left" | "right" | "up" | "down";
export type ResizeDirection = "narrower" | "wider" | "shorter" | "taller";

const overlaps = (a: WidgetPlacement, b: WidgetPlacement) =>
  a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;

export const canPlaceWidget = (layout: WorkspaceLayout, candidate: WidgetPlacement) =>
  candidate.x >= 0 &&
  candidate.y >= 0 &&
  candidate.x + candidate.width <= 12 &&
  layout.widgets.every((widget) => widget.id === candidate.id || !overlaps(widget, candidate));

const updateWidget = (
  layout: WorkspaceLayout,
  id: WidgetId,
  update: (widget: WidgetPlacement) => WidgetPlacement,
): WorkspaceLayout => {
  const current = layout.widgets.find((widget) => widget.id === id);
  if (!current) return layout;
  const candidate = update(current);
  if (candidate.x === current.x && candidate.y === current.y && candidate.width === current.width && candidate.height === current.height) return layout;
  if (!canPlaceWidget(layout, candidate)) return layout;
  return { ...layout, widgets: layout.widgets.map((widget) => (widget.id === id ? candidate : widget)) };
};

export const moveWidget = (layout: WorkspaceLayout, id: WidgetId, direction: LayoutDirection) => {
  const offsets: Record<LayoutDirection, readonly [number, number]> = {
    left: [-1, 0],
    right: [1, 0],
    up: [0, -1],
    down: [0, 1],
  };
  const [dx, dy] = offsets[direction];
  return updateWidget(layout, id, (widget) => ({ ...widget, x: widget.x + dx, y: widget.y + dy }));
};

export const resizeWidget = (layout: WorkspaceLayout, id: WidgetId, direction: ResizeDirection) =>
  updateWidget(layout, id, (widget) => {
    const minimum = WIDGET_DEFINITIONS[id].minimumSize;
    const minWidth = Math.max(widget.minWidth ?? 1, minimum.width);
    const minHeight = Math.max(widget.minHeight ?? 1, minimum.height);
    const widthDelta = direction === "wider" ? 1 : direction === "narrower" ? -1 : 0;
    const heightDelta = direction === "taller" ? 1 : direction === "shorter" ? -1 : 0;
    return {
      ...widget,
      width: Math.max(minWidth, widget.width + widthDelta),
      height: Math.max(minHeight, widget.height + heightDelta),
    };
  });
