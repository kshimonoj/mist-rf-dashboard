/**
 * 明細テーブルの列ソート・列フィルタ（純関数）。
 *
 * UI（DataTable コンポーネント）から切り出してある。ここに手を入れたら
 * `tests/tableSort.test.mjs` で検証できる（jsdom 無しで動く）。
 *
 * ダウンロード（xlsx / csv）の内容には一切関与しない。ソート・フィルタは
 * 表示専用で、ダウンロードは常にサーバ側の元データ・元順序を返す。
 */

export type ColumnKind = "text" | "number" | "enum" | "time" | "bool";
export type SortDir = "asc" | "desc";

export interface SortSpec {
  key: string;
  dir: SortDir;
}

function isNullish(v: unknown): boolean {
  return v === null || v === undefined || v === "";
}

/**
 * 2 値を列の型に応じて比較する。null/undefined/空文字は「値が無い」として
 * 呼び出し側（sortByColumn）が常に末尾に回す前提で、ここでは非 null 値同士だけを比べる。
 */
export function compareForSort(a: unknown, b: unknown, kind: ColumnKind): number {
  switch (kind) {
    case "number": {
      const an = typeof a === "number" ? a : Number(a);
      const bn = typeof b === "number" ? b : Number(b);
      if (Number.isNaN(an) && Number.isNaN(bn)) return 0;
      if (Number.isNaN(an)) return 1;
      if (Number.isNaN(bn)) return -1;
      return an - bn;
    }
    case "time": {
      const at = new Date(String(a)).getTime();
      const bt = new Date(String(b)).getTime();
      if (Number.isNaN(at) && Number.isNaN(bt)) return 0;
      if (Number.isNaN(at)) return 1;
      if (Number.isNaN(bt)) return -1;
      return at - bt;
    }
    case "bool": {
      const ab = Boolean(a);
      const bb = Boolean(b);
      return ab === bb ? 0 : ab ? 1 : -1;
    }
    case "text":
    case "enum":
    default:
      return String(a).localeCompare(String(b), "ja");
  }
}

/**
 * `rows` を `key` 列でソートする。値の無い行は方向に関わらず常に末尾に置く
 * （「昇順なら先頭、降順なら末尾」だと同じ行でも位置が飛んで読みづらいため）。
 * 同値・値無し同士は元の順序を保つ（安定ソート）。
 */
export function sortByColumn<T>(
  rows: readonly T[],
  getValue: (row: T) => unknown,
  kind: ColumnKind,
  dir: SortDir
): T[] {
  const decorated = rows.map((row, index) => ({ row, value: getValue(row), index }));
  decorated.sort((x, y) => {
    const xNull = isNullish(x.value);
    const yNull = isNullish(y.value);
    if (xNull && yNull) return x.index - y.index;
    if (xNull) return 1;
    if (yNull) return -1;
    const cmp = compareForSort(x.value, y.value, kind);
    if (cmp !== 0) return dir === "asc" ? cmp : -cmp;
    return x.index - y.index;
  });
  return decorated.map((d) => d.row);
}

export type FilterValue =
  | { kind: "text"; value: string }
  | { kind: "number"; min: number | null; max: number | null }
  | { kind: "bool"; value: "all" | "true" | "false" }
  | { kind: "enum"; excluded: Set<string> };

export function isFilterActive(filter: FilterValue | undefined): boolean {
  if (!filter) return false;
  switch (filter.kind) {
    case "text":
      return filter.value.trim() !== "";
    case "number":
      return filter.min !== null || filter.max !== null;
    case "bool":
      return filter.value !== "all";
    case "enum":
      return filter.excluded.size > 0;
  }
}

/** 1 セルの値がフィルタ条件に一致するか。値が無い行は数値レンジ以外は通す。 */
export function matchesFilter(value: unknown, filter: FilterValue): boolean {
  switch (filter.kind) {
    case "text": {
      const needle = filter.value.trim().toLowerCase();
      if (!needle) return true;
      return String(value ?? "").toLowerCase().includes(needle);
    }
    case "number": {
      if (filter.min === null && filter.max === null) return true;
      const n = typeof value === "number" ? value : Number(value);
      if (Number.isNaN(n)) return false;
      if (filter.min !== null && n < filter.min) return false;
      if (filter.max !== null && n > filter.max) return false;
      return true;
    }
    case "bool": {
      if (filter.value === "all") return true;
      const b = Boolean(value);
      return filter.value === "true" ? b : !b;
    }
    case "enum": {
      if (filter.excluded.size === 0) return true;
      return !filter.excluded.has(String(value));
    }
  }
}

/**
 * 列フィルタをすべて AND で適用する。`filters` に無い列・非アクティブな条件は無視する。
 * ダウンロード内容には使わないこと（表示専用）。
 */
export function applyColumnFilters<T>(
  rows: readonly T[],
  getValue: (row: T, key: string) => unknown,
  filters: Record<string, FilterValue>
): T[] {
  const active = Object.entries(filters).filter(([, f]) => isFilterActive(f));
  if (active.length === 0) return rows.slice();
  return rows.filter((row) => active.every(([key, filter]) => matchesFilter(getValue(row, key), filter)));
}
