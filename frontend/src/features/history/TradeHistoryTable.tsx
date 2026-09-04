import { useRef } from "react";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Empty, money, pct, toneFor } from "../../components";
import type { UiHistoryRow } from "../../types";

const column = createColumnHelper<UiHistoryRow>();
const columns = [
  column.accessor("time", { header: "Czas" }), column.accessor("symbol", { header: "Para" }),
  column.accessor("side", { header: "Strona" }), column.accessor("entry", { header: "Wejście" }),
  column.accessor("exit", { header: "Wyjście" }),
  column.accessor("pnl", { header: "PnL", cell: ({ getValue }) => <span className={toneFor(getValue())}>{money(getValue(), true)}</span> }),
  column.accessor("pnl_pct", { header: "PnL %", cell: ({ getValue }) => <span className={toneFor(getValue())}>{pct(getValue())}</span> }),
  column.accessor("engine", { header: "Silnik" }), column.accessor("reason", { header: "Powód" }),
];

export function TradeHistoryTable({ rows }: { rows: UiHistoryRow[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });
  const rendered = table.getRowModel().rows;
  const virtualizer = useVirtualizer({ count: rendered.length, getScrollElement: () => scrollRef.current, estimateSize: () => 38, overscan: 8 });
  if (!rows.length) return <Empty>Brak zamkniętych transakcji.</Empty>;
  return <div ref={scrollRef} style={{ maxHeight: 520, overflow: "auto" }}>
    <table style={{ display: "grid" }}><thead style={{ display: "grid", position: "sticky", top: 0, zIndex: 1 }}>
      {table.getHeaderGroups().map((group) => <tr key={group.id} style={{ display: "grid", gridTemplateColumns: "repeat(9,minmax(90px,1fr))" }}>{group.headers.map((header) => <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}
    </thead><tbody style={{ display: "grid", height: virtualizer.getTotalSize(), position: "relative" }}>
      {virtualizer.getVirtualItems().map((item) => { const row = rendered[item.index]; return <tr key={row.id} style={{ display: "grid", gridTemplateColumns: "repeat(9,minmax(90px,1fr))", position: "absolute", transform: `translateY(${item.start}px)`, width: "100%" }}>{row.getVisibleCells().map((cell) => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>; })}
    </tbody></table>
  </div>;
}
