import { describe, expect, it } from "vitest";
import { getWorkspacePreset } from "./presets";
import { canPlaceWidget, moveWidget, resizeWidget } from "./layout";

describe("workspace layout helpers", () => {
  it("moves into free space but rejects boundaries and collisions", () => {
    const layout = getWorkspacePreset("trading");
    expect(moveWidget(layout, "market-chart", "left")).toBe(layout);
    expect(moveWidget(layout, "market-chart", "right")).toBe(layout);
    const moved = moveWidget(layout, "positions", "down");
    expect(moved).not.toBe(layout);
    expect(moved.widgets.find((widget) => widget.id === "positions")?.y).toBe(7);
  });

  it("respects widget minimums and twelve-column boundary", () => {
    const layout = getWorkspacePreset("trading");
    let narrowed = layout;
    for (let step = 0; step < 4; step += 1) narrowed = resizeWidget(narrowed, "market-chart", "narrower");
    expect(narrowed.widgets.find((widget) => widget.id === "market-chart")?.width).toBe(4);
    expect(resizeWidget(narrowed, "market-chart", "narrower")).toBe(narrowed);
    expect(resizeWidget(layout, "scanner", "wider")).toBe(layout);
    const taller = resizeWidget(layout, "positions", "taller");
    expect(taller.widgets.find((widget) => widget.id === "positions")?.height).toBe(5);
  });

  it("recognizes a valid free placement", () => {
    const layout = getWorkspacePreset("trading");
    expect(canPlaceWidget(layout, { id: "positions", x: 0, y: 10, width: 7, height: 4 })).toBe(true);
  });
});
