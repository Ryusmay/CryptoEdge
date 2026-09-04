import { describe, expect, it } from "vitest";
import { normalizeOhlcv, toChartData, validLevels, valuesToOhlcv } from "./model";

describe("chart data helpers", () => {
  it("sorts, deduplicates and repairs candle bounds", () => {
    const result = normalizeOhlcv([
      { time: 2, open: 10, high: 9, low: 11, close: 12, volume: 5 },
      { time: 1, open: 8, high: 9, low: 7, close: 8 },
      { time: 2, open: 11, high: 13, low: 10, close: 12, volume: 6 },
      { time: 3, open: Number.NaN, high: 1, low: 1, close: 1 },
    ]);
    expect(result).toHaveLength(2);
    expect(result.map((item) => item.time)).toEqual([1, 2]);
    expect(result[1]).toMatchObject({ open: 11, high: 13, low: 10, close: 12, volume: 6 });
  });

  it("creates directional volume colors", () => {
    const result = toChartData([
      { time: 1, open: 10, high: 11, low: 9, close: 11, volume: 4 },
      { time: 2, open: 11, high: 12, low: 9, close: 10, volume: 7 },
    ]);
    expect(result.volumes.map((item) => item.value)).toEqual([4, 7]);
    expect(result.volumes[0].color).toContain("51, 214, 166");
    expect(result.volumes[1].color).toContain("255, 91, 113");
  });

  it("keeps the legacy values input as synthetic OHLCV", () => {
    expect(valuesToOhlcv([100, 102], 1_000, 60)).toEqual([
      { time: 940, open: 100, high: 100, low: 100, close: 100, volume: 0 },
      { time: 1000, open: 100, high: 102, low: 100, close: 102, volume: 0 },
    ]);
  });

  it("drops invalid trading levels", () => {
    expect(validLevels({ entry: 100, stopLoss: Number.NaN, takeProfits: [110, -1] })).toEqual([
      { price: 100, title: "ENTRY", color: "#5da9ff" },
      { price: 110, title: "TP1", color: "#33d6a6" },
    ]);
  });
});
