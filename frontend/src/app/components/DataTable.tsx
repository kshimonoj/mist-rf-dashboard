"use client";

/**
 * 明細テーブル共通コンポーネント（列ソート + 列フィルタ）。
 *
 * RRM の明細と Floor Peak の結果テーブルで共有する。**Hang AP の結果テーブル
 * （HangapResultTable.tsx）はサーバ側ページングの別実装なのでここには含めない**
 * （33番の調査で確認済み。3画面共通化すると Hang AP のテスト済み実装を壊す）。
 *
 * フィルタパネルは表の `overflow-x-auto` の外（ツールバー）に置く。表の内側に
 * 絶対配置すると `overflow-x: auto` の影響で縦方向も切られるため
 * （HangapResultTable.tsx の FilterPanel コメント参照）。開閉は 32 で確立した
 * containerRef + document リスナー方式（SiteSelect と同じ）。
 *
 * ソート・フィルタは表示専用。ダウンロード（xlsx / csv）の内容には一切影響しない。
 */

import { ChevronDown, ChevronUp, ChevronsUpDown, Filter as FilterIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  applyColumnFilters, isFilterActive, sortByColumn,
  ColumnKind, FilterValue, SortDir,
} from "@/lib/tableSort";

export interface DataTableColumn<T> {
  key: string;
  label: string;
  kind: ColumnKind;
  getValue: (row: T) => unknown;
  /** 既定は値をそのまま文字列化。バッジ等が要る列だけ渡す */
  render?: (row: T) => React.ReactNode;
  /** 既定: time 以外は true（time は表示上の文字列比較になり実用性が低いので既定 false） */
  filterable?: boolean;
}

const cardStyle = { borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" };
const inputStyle = {
  borderColor: "var(--chart-grid)",
  backgroundColor: "var(--bg-primary)",
  color: "var(--text-primary)",
};

function fmtCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

// ---------------------------------------------------------------------------
// フィルタボタン（列ごと。開閉は containerRef + document リスナー方式）
// ---------------------------------------------------------------------------

function FilterButton<T>({
  column, value, onChange, enumOptions,
}: {
  column: DataTableColumn<T>;
  value: FilterValue | undefined;
  onChange: (next: FilterValue | undefined) => void;
  enumOptions: string[];
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const active = isFilterActive(value);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const textValue = value?.kind === "text" ? value.value : "";
  const numberValue = value?.kind === "number" ? value : { min: null, max: null };
  const boolValue = value?.kind === "bool" ? value.value : "all";
  const excluded = value?.kind === "enum" ? value.excluded : new Set<string>();

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 px-2 py-1 border rounded text-[11px] whitespace-nowrap"
        style={{
          borderColor: active ? "var(--cyan)" : "var(--chart-grid)",
          color: active ? "var(--cyan)" : "var(--text-muted)",
          backgroundColor: active ? "rgba(0,212,255,0.08)" : "transparent",
        }}
      >
        <FilterIcon className="w-3 h-3" />
        {column.label}
        {active && <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: "var(--cyan)" }} />}
      </button>

      {open && (
        <div
          className="absolute z-50 mt-1 p-2.5 border rounded-lg shadow-lg text-xs"
          style={{ ...cardStyle, width: 220 }}
        >
          {column.kind === "text" && (
            <input
              autoFocus
              type="text"
              value={textValue}
              onChange={(e) => onChange(e.target.value ? { kind: "text", value: e.target.value } : undefined)}
              placeholder={`${column.label} を含む`}
              className="w-full px-2 py-1 rounded border text-xs"
              style={inputStyle}
            />
          )}

          {column.kind === "number" && (
            <div className="flex items-center gap-1.5">
              <input
                type="number"
                value={numberValue.min ?? ""}
                onChange={(e) => {
                  const min = e.target.value === "" ? null : Number(e.target.value);
                  const max = numberValue.max ?? null;
                  onChange(min === null && max === null ? undefined : { kind: "number", min, max });
                }}
                placeholder="下限"
                className="w-full px-2 py-1 rounded border text-xs"
                style={inputStyle}
              />
              <span style={{ color: "var(--text-muted)" }}>〜</span>
              <input
                type="number"
                value={numberValue.max ?? ""}
                onChange={(e) => {
                  const max = e.target.value === "" ? null : Number(e.target.value);
                  const min = numberValue.min ?? null;
                  onChange(min === null && max === null ? undefined : { kind: "number", min, max });
                }}
                placeholder="上限"
                className="w-full px-2 py-1 rounded border text-xs"
                style={inputStyle}
              />
            </div>
          )}

          {column.kind === "bool" && (
            <div className="flex gap-1">
              {(["all", "true", "false"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => onChange(v === "all" ? undefined : { kind: "bool", value: v })}
                  className="flex-1 px-2 py-1 rounded border text-[11px]"
                  style={{
                    borderColor: boolValue === v ? "var(--cyan)" : "var(--chart-grid)",
                    color: boolValue === v ? "var(--cyan)" : "var(--text-muted)",
                    backgroundColor: boolValue === v ? "rgba(0,212,255,0.08)" : "transparent",
                  }}
                >
                  {v === "all" ? "すべて" : v === "true" ? "true" : "false"}
                </button>
              ))}
            </div>
          )}

          {column.kind === "enum" && (
            <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
              {enumOptions.length === 0 && (
                <span style={{ color: "var(--text-muted)" }}>値がありません</span>
              )}
              {enumOptions.map((opt) => {
                const checked = !excluded.has(opt);
                return (
                  <label key={opt} className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        const next = new Set(excluded);
                        if (e.target.checked) next.delete(opt);
                        else next.add(opt);
                        onChange(next.size === 0 ? undefined : { kind: "enum", excluded: next });
                      }}
                    />
                    <span style={{ color: "var(--text-secondary)" }}>{opt}</span>
                  </label>
                );
              })}
            </div>
          )}

          {active && (
            <button
              onClick={() => onChange(undefined)}
              className="mt-2 text-[11px] underline"
              style={{ color: "var(--text-muted)" }}
            >
              条件をクリア
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 本体
// ---------------------------------------------------------------------------

export default function DataTable<T>({
  columns, rows, rowKey, rowStyle, maxRows = 500, emptyMessage = "表示する行がありません",
  initialSort,
}: {
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  rowStyle?: (row: T) => React.CSSProperties | undefined;
  maxRows?: number;
  emptyMessage?: string;
  initialSort?: { key: string; dir: SortDir };
}) {
  const [sort, setSort] = useState<{ key: string; dir: SortDir } | null>(initialSort ?? null);
  const [filters, setFilters] = useState<Record<string, FilterValue>>({});

  const columnByKey = useMemo(() => new Map(columns.map((c) => [c.key, c])), [columns]);

  const enumOptionsByKey = useMemo(() => {
    const out = new Map<string, string[]>();
    for (const col of columns) {
      if (col.kind !== "enum") continue;
      const set = new Set<string>();
      for (const row of rows) {
        const v = col.getValue(row);
        if (v !== null && v !== undefined && v !== "") set.add(String(v));
      }
      out.set(col.key, Array.from(set).sort((a, b) => a.localeCompare(b, "ja")));
    }
    return out;
  }, [columns, rows]);

  const filtered = useMemo(() => {
    const getValue = (row: T, key: string) => columnByKey.get(key)?.getValue(row);
    return applyColumnFilters(rows, getValue, filters);
  }, [rows, filters, columnByKey]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columnByKey.get(sort.key);
    if (!col) return filtered;
    return sortByColumn(filtered, col.getValue, col.kind, sort.dir);
  }, [filtered, sort, columnByKey]);

  const shown = sorted.slice(0, maxRows);

  const handleHeaderClick = (col: DataTableColumn<T>) => {
    setSort((prev) => {
      if (!prev || prev.key !== col.key) return { key: col.key, dir: "asc" };
      if (prev.dir === "asc") return { key: col.key, dir: "desc" };
      return null;
    });
  };

  const setFilter = (key: string, value: FilterValue | undefined) => {
    setFilters((prev) => {
      const next = { ...prev };
      if (value === undefined) delete next[key];
      else next[key] = value;
      return next;
    });
  };

  const filterableColumns = columns.filter((c) => c.filterable ?? c.kind !== "time");

  return (
    <div>
      {filterableColumns.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mb-2">
          <span className="text-[11px] mr-1" style={{ color: "var(--text-muted)" }}>フィルタ:</span>
          {filterableColumns.map((col) => (
            <FilterButton
              key={col.key}
              column={col}
              value={filters[col.key]}
              onChange={(v) => setFilter(col.key, v)}
              enumOptions={enumOptionsByKey.get(col.key) ?? []}
            />
          ))}
          {Object.keys(filters).length > 0 && (
            <button
              onClick={() => setFilters({})}
              className="text-[11px] underline ml-1"
              style={{ color: "var(--text-muted)" }}
            >
              すべてクリア
            </button>
          )}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left" style={{ borderColor: "var(--chart-grid)" }}>
              {columns.map((col) => {
                const active = sort?.key === col.key;
                return (
                  <th
                    key={col.key}
                    onClick={() => handleHeaderClick(col)}
                    className="py-2 px-2 font-normal whitespace-nowrap font-mono cursor-pointer select-none hover:opacity-80"
                    style={{ color: active ? "var(--cyan)" : "var(--text-muted)" }}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {active ? (
                        sort!.dir === "asc" ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />
                      ) : (
                        <ChevronsUpDown className="w-3 h-3 opacity-40" />
                      )}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr
                key={rowKey(row, i)}
                className="border-b"
                style={{ borderColor: "var(--chart-grid)", ...rowStyle?.(row) }}
              >
                {columns.map((col) => (
                  <td key={col.key} className="py-1.5 px-2 whitespace-nowrap font-mono" style={{ color: "var(--text-primary)" }}>
                    {col.render ? col.render(row) : fmtCell(col.getValue(row))}
                  </td>
                ))}
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="py-8 text-center" style={{ color: "var(--text-muted)" }}>
                  {emptyMessage}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {sorted.length > shown.length && (
        <p className="text-xs mt-2" style={{ color: "var(--yellow)" }}>
          {sorted.length} 件のうち先頭 {shown.length} 件だけを表示しています。全件は csv / xlsx をダウンロードしてください。
        </p>
      )}
    </div>
  );
}
