import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, HistogramSeries, createChart, createSeriesMarkers,
  type SeriesMarker, type Time,
} from "lightweight-charts";
import { toChartData, validLevels, valuesToOhlcv, type OhlcvCandle, type TradeMarker, type TradingLevels } from "./model";

interface MarketChartProps {
  symbol: string;
  values?: number[];
  candles?: OhlcvCandle[];
  levels?: TradingLevels;
  markers?: TradeMarker[];
}

export function MarketChart({ symbol, values = [], candles, levels, markers = [] }: MarketChartProps) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const chart = createChart(element, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#0d131d" }, textColor: "#8e9bad" },
      grid: { vertLines: { color: "#17202c" }, horzLines: { color: "#17202c" } },
      rightPriceScale: { borderColor: "#263243" },
      timeScale: { borderColor: "#263243", timeVisible: true },
      crosshair: { vertLine: { color: "#60718a" }, horzLine: { color: "#60718a" } },
    });
    const series = chart.addSeries(CandlestickSeries, {
      title: symbol,
      upColor: "#33d6a6", downColor: "#ff5b71",
      wickUpColor: "#33d6a6", wickDownColor: "#ff5b71",
      borderVisible: false,
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" }, priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    const data = toChartData(candles?.length ? candles : valuesToOhlcv(values));
    series.setData(data.candles);
    volumeSeries.setData(data.volumes);
    validLevels(levels).forEach(({ price, title, color }) => series.createPriceLine({
      price, title, color, lineWidth: 1, axisLabelVisible: true,
    }));
    const visibleTimes = new Set(data.candles.map((item) => Number(item.time)));
    const chartMarkers: SeriesMarker<Time>[] = markers
      .filter((marker) => visibleTimes.has(Math.floor(marker.time)))
      .sort((a, b) => a.time - b.time)
      .map((marker) => ({
        time: Math.floor(marker.time) as Time,
        position: marker.kind === "entry" ? "belowBar" : "aboveBar",
        shape: marker.kind === "entry" ? "arrowUp" : "arrowDown",
        color: marker.kind === "entry" ? "#5da9ff" : "#f2c94c",
        text: marker.label ?? (marker.kind === "entry" ? "ENTRY" : "EXIT"),
      }));
    if (chartMarkers.length) createSeriesMarkers(series, chartMarkers);
    const resize = new ResizeObserver(() => chart.timeScale().fitContent());
    resize.observe(element);
    chart.timeScale().fitContent();
    return () => { resize.disconnect(); chart.remove(); };
  }, [symbol, values, candles, levels, markers]);

  return <div className="market-chart" ref={host} role="img" aria-label={`Wykres ceny ${symbol}/USDT`} />;
}
