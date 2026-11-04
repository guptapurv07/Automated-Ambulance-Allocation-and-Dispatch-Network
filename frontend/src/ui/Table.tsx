import { useVirtualizer } from "@tanstack/react-virtual";
import { clsx } from "clsx";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { Checkbox } from "./form";

export interface Column<R> {
  key: string;
  header: string;
  cell: (row: R) => ReactNode;
  /** value used for sorting; omit to make the column unsortable */
  sortValue?: (row: R) => string | number;
  width?: string;            // CSS grid track, e.g. "120px" or "1fr"
  align?: "left" | "right";
  numeric?: boolean;         // mono, tabular
}

const ROW_H = 40;

/** Sortable, virtualised table with a sticky header, optional row selection and keyboard focus.
 *  Laid out with CSS grid so the virtualised rows keep their column widths. */
export function DataTable<R>({
  rows,
  columns,
  rowKey,
  selectable,
  selected,
  onSelectedChange,
  activeKey,
  onRowClick,
  height = 360,
  empty,
  label,
}: {
  rows: R[];
  columns: Column<R>[];
  rowKey: (row: R) => string | number;
  selectable?: boolean;
  selected?: Set<string | number>;
  onSelectedChange?: (s: Set<string | number>) => void;
  activeKey?: string | number | null;
  onRowClick?: (row: R) => void;
  height?: number | string;
  empty?: ReactNode;
  label: string;
}) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    const col = sort && columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const get = col.sortValue;
    return [...rows].sort((a, b) => {
      const x = get(a), y = get(b);
      return (x < y ? -1 : x > y ? 1 : 0) * sort!.dir;
    });
  }, [rows, columns, sort]);

  const scroller = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({ count: sorted.length, getScrollElement: () => scroller.current, estimateSize: () => ROW_H, overscan: 12 });
  const template = [selectable ? "36px" : null, ...columns.map((c) => c.width ?? "1fr")].filter(Boolean).join(" ");
  const sel = selected ?? new Set();
  const allSelected = sorted.length > 0 && sorted.every((r) => sel.has(rowKey(r)));
  const someSelected = !allSelected && sorted.some((r) => sel.has(rowKey(r)));

  const toggle = (k: string | number) => {
    const next = new Set(sel);
    next.has(k) ? next.delete(k) : next.add(k);
    onSelectedChange?.(next);
  };

  return (
    <div role="table" aria-label={label} aria-rowcount={sorted.length} className="overflow-hidden border border-line bg-panel">
      <div role="row" className="grid h-10 items-center border-b border-line bg-panel-muted px-0 text-micro font-semibold uppercase tracking-[0.06em] text-muted" style={{ gridTemplateColumns: template }}>
        {selectable && (
          <div className="flex justify-center">
            <Checkbox label="Select all" showLabel={false} checked={allSelected ? true : someSelected ? "indeterminate" : false}
              onChange={(on) => onSelectedChange?.(on ? new Set(sorted.map(rowKey)) : new Set())} />
          </div>
        )}
        {columns.map((c) => {
          const active = sort?.key === c.key;
          return (
            <div key={c.key} role="columnheader" aria-sort={active ? (sort!.dir === 1 ? "ascending" : "descending") : undefined}
              className={clsx("px-3", c.align === "right" && "text-right")}>
              {c.sortValue ? (
                <button type="button" onClick={() => setSort(active ? (sort!.dir === 1 ? { key: c.key, dir: -1 } : null) : { key: c.key, dir: 1 })}
                  className={clsx("inline-flex items-center gap-1 uppercase hover:text-text", active && "text-text", c.align === "right" && "flex-row-reverse")}>
                  {c.header}
                  {active && (sort!.dir === 1 ? <ArrowUp size={12} strokeWidth={1.75} /> : <ArrowDown size={12} strokeWidth={1.75} />)}
                </button>
              ) : (
                c.header
              )}
            </div>
          );
        })}
      </div>
      <div ref={scroller} className="overflow-auto" style={{ height }}>
        {sorted.length === 0 ? (
          <div className="px-3">{empty}</div>
        ) : (
          <div className="relative" style={{ height: v.getTotalSize() }}>
            {v.getVirtualItems().map((vi) => {
              const r = sorted[vi.index]!;
              const k = rowKey(r);
              return (
                <div key={k} role="row" tabIndex={onRowClick ? 0 : -1} aria-selected={sel.has(k) || activeKey === k}
                  onClick={() => onRowClick?.(r)}
                  onKeyDown={(e) => e.key === "Enter" && onRowClick?.(r)}
                  className={clsx(
                    "absolute inset-x-0 grid items-center border-b border-line px-0 text-dense outline-none",
                    onRowClick && "cursor-default hover:bg-panel-muted focus-visible:bg-panel-muted",
                    (activeKey === k || sel.has(k)) && "bg-panel-muted hover:bg-panel-muted",
                  )}
                  style={{ top: vi.start, height: ROW_H, gridTemplateColumns: template }}>
                  {selectable && (
                    <div className="flex justify-center" onClick={(e) => e.stopPropagation()}>
                      <Checkbox label="Select row" showLabel={false} checked={sel.has(k)} onChange={() => toggle(k)} />
                    </div>
                  )}
                  {columns.map((c) => (
                    <div key={c.key} role="cell" className={clsx("truncate px-3", c.align === "right" && "text-right", c.numeric && "num font-medium")}>
                      {c.cell(r)}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
