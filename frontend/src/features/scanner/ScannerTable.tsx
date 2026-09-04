import { useMemo, useRef } from "react";
import { createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel, useReactTable, type OnChangeFn, type SortingState } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { Candidate } from "../../types";
import { Gate, num } from "../../components";

const column = createColumnHelper<Candidate>();

interface ScannerTableProps {
  rows: Candidate[];
  onSymbol: (symbol: string) => void;
  sorting: SortingState;
  onSortingChange: OnChangeFn<SortingState>;
}

export function ScannerTable({ rows, onSymbol, sorting, onSortingChange }: ScannerTableProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const columns = useMemo(() => [
    column.accessor("sym", {
      header: "Para",
      cell: ({ getValue }) => <button className="symbol-link" onClick={() => onSymbol(getValue())}>{getValue()}</button>,
    }),
    column.accessor("gate", { header: "Status", cell: ({ getValue }) => <Gate gate={getValue()} /> }),
    column.accessor("side", { header: "Kierunek", cell: ({ getValue }) => getValue() || "—" }),
    column.accessor("score", { header: "Ocena", cell: ({ getValue }) => num(getValue(), 1) }),
    column.accessor("rr", { header: "R:R", cell: ({ getValue }) => num(getValue(), 2) }),
  ], [onSymbol]);
  const table = useReactTable({
    data: rows, columns, state: { sorting }, onSortingChange,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  });
  const visibleRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: visibleRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 43,
    overscan: 12,
  });

  return <div className="virtual-table" role="table" aria-label="Kandydaci do wejścia">
    <div className="virtual-table-head" role="rowgroup">
      <div className="virtual-table-row" role="row">
        {table.getHeaderGroups()[0]?.headers.map(header => <button
          key={header.id} role="columnheader" className="virtual-table-cell sortable"
          onClick={header.column.getToggleSortingHandler()}
          aria-sort={header.column.getIsSorted() === "asc" ? "ascending" : header.column.getIsSorted() === "desc" ? "descending" : "none"}
        >{flexRender(header.column.columnDef.header, header.getContext())}<span>{header.column.getIsSorted() === "asc" ? " ↑" : header.column.getIsSorted() === "desc" ? " ↓" : ""}</span></button>)}
      </div>
    </div>
    <div ref={scrollRef} className="virtual-table-scroll" role="rowgroup">
      {visibleRows.length === 0 ? <div className="virtual-table-empty">Brak kandydatów</div> : <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map(item => {
          const row = visibleRows[item.index];
          return <div key={row.id} className="virtual-table-row virtual-table-data-row" role="row"
            onDoubleClick={() => onSymbol(row.original.sym)}
            style={{ position: "absolute", transform: `translateY(${item.start}px)`, width: "100%" }}>
            {row.getVisibleCells().map(cell => <div key={cell.id} className="virtual-table-cell" role="cell">{flexRender(cell.column.columnDef.cell, cell.getContext())}</div>)}
          </div>;
        })}
      </div>}
    </div>
  </div>;
}
