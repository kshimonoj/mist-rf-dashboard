"use client";

import {
  ArrowUpDown, Download, Filter as FilterIcon, GripVertical, RotateCcw, X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  fetchHangapResult, fetchHangapSavedRows, getHangapDownloadUrl, getHangapSavedDownloadUrl,
  hangapFilterSpecs, isHangapFilterActive,
  HangapCell, HangapColumnKind, HangapFilter, HangapFilters, HangapResultPage,
} from "@/lib/api";

/**
 * 結果テーブル。**分析直後の結果と保存済み結果で同じこのコンポーネントを使う。**
 * どちらの API も同じ形（HangapResultPage）を返すので、表示側に分岐は無い。
 * 別実装を作ると、ページング・ソート・フィルタ・列順が片方だけで壊れる。
 */
export type HangapResultSource =
  | { kind: "job"; jobId: string }
  | { kind: "saved"; name: string };

/**
 * 既定で表示する列。結果は 30 列あり、全部横に並べると読めない。
 * ここは「出すかどうか」だけを決める。並び順は API が返す columns
 * （= detector.RESULT_COLUMNS）の順で、利用者が並べ替えた場合はその順。
 * **ダウンロードは常に全 30 列・全行**（画面の列順・フィルタの影響を受けない）。
 */
const DEFAULT_COLUMNS = new Set([
  "ap_name",
  "区間番号",
  "ゼロ直前時刻",
  "直前clients",
  "ゼロ開始",
  "ゼロ終了",
  "連続ゼロ回数",
  "回復状況",
  "直後clients（回復時）",
  "AP最大clients",
  "周辺AP判定",
  "周辺AP端末数合計",
]);

const PAGE_SIZE = 100;

/** 並べ替えた列順の保存先。再読み込み後も維持する（既定に戻すボタンで消す） */
const COLUMN_ORDER_KEY = "hangap:column_order";

const cardStyle = { borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" };
const btnStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };
const inputStyle = {
  borderColor: "var(--chart-grid)",
  backgroundColor: "var(--bg-primary)",
  color: "var(--text-primary)",
};

function fmtCell(value: HangapCell): string {
  if (value === null || value === undefined) return "-";
  return String(value);
}

/** 列の種類ごとの空の条件（「指定なし」の状態） */
function emptyFilter(kind: HangapColumnKind): HangapFilter {
  switch (kind) {
    case "enum": return { kind: "enum", values: [] };
    case "number": return { kind: "number", min: "", max: "" };
    case "time": return { kind: "time", from: "", to: "" };
    case "bool": return { kind: "bool", value: null };
    default: return { kind: "text", text: "" };
  }
}

/** 条件を「AP-01 を含む」のような短い説明にする（何で絞っているかを画面に出すため） */
function describeFilter(f: HangapFilter): string {
  switch (f.kind) {
    case "text": return `「${f.text}」を含む`;
    case "enum": return f.values.join(" / ");
    case "number": return `${f.min || "下限なし"} 〜 ${f.max || "上限なし"}`;
    case "time": return `${f.from || "開始なし"} 〜 ${f.to || "終了なし"}`;
    case "bool": return f.value ? "True" : "False";
  }
}

// ---------------------------------------------------------------------------
// 列ごとのフィルタ入力
// ---------------------------------------------------------------------------

/** 開いているフィルタ（列と、画面上の表示位置）。表の overflow に切られないよう固定配置する */
type OpenFilter = { column: string; top: number; left: number };

const PANEL_WIDTH = 256;

function FilterPanel({
  column, kind, choices, value, anchor, onChange, onClose,
}: {
  column: string;
  kind: HangapColumnKind;
  choices: string[];
  value: HangapFilter;
  anchor: OpenFilter;
  onChange: (next: HangapFilter) => void;
  onClose: () => void;
}) {
  const label = (text: string) => (
    <span className="block text-[10px] mb-0.5" style={{ color: "var(--text-muted)" }}>{text}</span>
  );

  return (
    <>
      {/* 外側のクリックで閉じる。表は overflow-x: auto なので、パネル自体は固定配置にする
          （表の内側に絶対配置すると切られる／余計なスクロールが出る） */}
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        className="fixed z-50 p-3 border rounded-lg shadow-lg font-normal"
        style={{
          ...cardStyle,
          backgroundColor: "var(--bg-card)",
          top: anchor.top,
          left: anchor.left,
          width: PANEL_WIDTH,
        }}
      >
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs truncate" style={{ color: "var(--text-primary)" }}>{column}</span>
        <button onClick={onClose} className="ml-auto" style={{ color: "var(--text-muted)" }}>
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {value.kind === "text" && (
        <input
          autoFocus
          value={value.text}
          onChange={(e) => onChange({ kind: "text", text: e.target.value })}
          placeholder="部分一致（大小文字は区別しない）"
          className="w-full px-2 py-1 rounded border text-xs"
          style={inputStyle}
        />
      )}

      {value.kind === "enum" && (
        <ul className="space-y-1">
          {choices.map((choice) => {
            const checked = value.values.includes(choice);
            return (
              <li key={choice}>
                <label className="flex items-center gap-1.5 text-xs" style={{ color: "var(--text-secondary)" }}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      onChange({
                        kind: "enum",
                        values: checked
                          ? value.values.filter((v) => v !== choice)
                          : [...value.values, choice],
                      })
                    }
                  />
                  {choice}
                </label>
              </li>
            );
          })}
        </ul>
      )}

      {value.kind === "number" && (
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs">
            {label("下限")}
            <input
              autoFocus
              value={value.min}
              inputMode="decimal"
              onChange={(e) => onChange({ ...value, min: e.target.value })}
              className="w-full px-2 py-1 rounded border text-xs font-mono"
              style={inputStyle}
            />
          </label>
          <label className="text-xs">
            {label("上限")}
            <input
              value={value.max}
              inputMode="decimal"
              onChange={(e) => onChange({ ...value, max: e.target.value })}
              className="w-full px-2 py-1 rounded border text-xs font-mono"
              style={inputStyle}
            />
          </label>
        </div>
      )}

      {value.kind === "time" && (
        <div className="space-y-2">
          <label className="text-xs block">
            {label("開始")}
            <input
              type="datetime-local"
              step={1}
              value={value.from}
              onChange={(e) => onChange({ ...value, from: e.target.value })}
              className="w-full px-2 py-1 rounded border text-xs font-mono"
              style={inputStyle}
            />
          </label>
          <label className="text-xs block">
            {label("終了")}
            <input
              type="datetime-local"
              step={1}
              value={value.to}
              onChange={(e) => onChange({ ...value, to: e.target.value })}
              className="w-full px-2 py-1 rounded border text-xs font-mono"
              style={inputStyle}
            />
          </label>
          <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            ログの時刻表記（タイムゾーンなし）で比較します。
          </p>
        </div>
      )}

      {value.kind === "bool" && (
        <div className="flex flex-wrap gap-1.5">
          {([
            [null, "指定なし"],
            [true, "True"],
            [false, "False"],
          ] as const).map(([v, text]) => (
            <button
              key={text}
              onClick={() => onChange({ kind: "bool", value: v })}
              className="px-2 py-1 rounded border text-xs"
              style={
                value.value === v
                  ? { borderColor: "var(--cyan)", color: "var(--cyan)" }
                  : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
              }
            >
              {text}
            </button>
          ))}
        </div>
      )}

      <button
        onClick={() => onChange(emptyFilter(kind))}
        className="mt-2 text-[10px]"
        style={{ color: "var(--cyan)" }}
      >
        この列の条件を消す
      </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// 結果テーブル
// ---------------------------------------------------------------------------

export default function HangapResultTable({ source }: { source: HangapResultSource }) {
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<string | null>(null);
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [filters, setFilters] = useState<HangapFilters>({});
  const [openFilter, setOpenFilter] = useState<OpenFilter | null>(null);
  const [showAllColumns, setShowAllColumns] = useState(false);
  const [columnOrder, setColumnOrder] = useState<string[] | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);

  // 並べ替えた列順は再読み込み後も維持する
  useEffect(() => {
    const raw = window.localStorage.getItem(COLUMN_ORDER_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.every((v) => typeof v === "string")) {
        setColumnOrder(parsed as string[]);
      }
    } catch {
      /* 壊れていれば既定の順序を使う */
    }
  }, []);

  const specs = hangapFilterSpecs(filters);
  const sourceKey = source.kind === "job" ? source.jobId : source.name;
  const { data: page, isLoading } = useSWR<HangapResultPage>(
    ["hangap-rows", source.kind, sourceKey, offset, sort, order, specs.join("")],
    () => {
      const query = {
        offset, limit: PAGE_SIZE, sort: sort ?? undefined, order, filters,
      };
      return source.kind === "job"
        ? fetchHangapResult(source.jobId, query)
        : fetchHangapSavedRows(source.name, query);
    },
    { keepPreviousData: true }
  );

  /** API が返す既定の順序（= detector.RESULT_COLUMNS）に、保存された並べ替えを重ねる */
  const orderedColumns = useMemo(() => {
    const all = page?.columns ?? [];
    if (!columnOrder) return all;
    const kept = columnOrder.filter((c) => all.includes(c));
    const out = [...kept];
    // 保存後に増えた列は既定の位置に挿し込む（末尾に溜めない）
    for (const col of all.filter((c) => !kept.includes(c))) {
      out.splice(Math.min(all.indexOf(col), out.length), 0, col);
    }
    return out;
  }, [page?.columns, columnOrder]);

  const columns = useMemo(
    () => (showAllColumns ? orderedColumns : orderedColumns.filter((c) => DEFAULT_COLUMNS.has(c))),
    [orderedColumns, showAllColumns]
  );

  const activeColumns = Object.entries(filters)
    .filter(([, f]) => isHangapFilterActive(f))
    .map(([column, f]) => ({ column, f }));

  const toggleSort = (column: string) => {
    if (sort === column) {
      setOrder((v) => (v === "asc" ? "desc" : "asc"));
    } else {
      setSort(column);
      setOrder("asc");
    }
    setOffset(0);
  };

  const updateFilter = (column: string, next: HangapFilter) => {
    setFilters((prev) => ({ ...prev, [column]: next }));
    setOffset(0);
  };

  const moveColumn = (from: string, to: string) => {
    if (from === to) return;
    const next = orderedColumns.filter((c) => c !== from);
    next.splice(Math.max(0, next.indexOf(to)), 0, from);
    setColumnOrder(next);
    window.localStorage.setItem(COLUMN_ORDER_KEY, JSON.stringify(next));
  };

  const resetColumnOrder = () => {
    setColumnOrder(null);
    window.localStorage.removeItem(COLUMN_ORDER_KEY);
  };

  const downloads =
    source.kind === "job"
      ? {
          xlsx: getHangapDownloadUrl(source.jobId, "xlsx"),
          csv: getHangapDownloadUrl(source.jobId, "csv"),
        }
      : {
          xlsx: getHangapSavedDownloadUrl(source.name, "xlsx"),
          csv: getHangapSavedDownloadUrl(source.name, "csv"),
        };

  return (
    <>
      {/* ダウンロード（画面の絞り込み・列順とは無関係に、常に全行・全列） */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        {(["xlsx", "csv"] as const).map((format) => (
          <a
            key={format}
            href={downloads[format]}
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm"
            style={btnStyle}
          >
            <Download className="w-4 h-4" />
            {format} ダウンロード
          </a>
        ))}
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          ダウンロードは常に全 {page?.columns.length ?? 30} 列・全行（画面のフィルタ・列順の影響を受けません）
        </span>
      </div>

      {/* 回復状況のショートカット。列フィルタ（複数選択）と同じ状態を書き換えるだけで、
          別のフィルタは持たない（2 つの絞り込みが食い違う状態を作らない） */}
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>回復状況:</span>
        {[null, ...(page?.enum_choices["回復状況"] ?? [])].map((choice) => {
          const current = filters["回復状況"];
          const selected =
            choice === null
              ? !isHangapFilterActive(current)
              : current?.kind === "enum" &&
                current.values.length === 1 &&
                current.values[0] === choice;
          return (
            <button
              key={choice ?? "all"}
              onClick={() =>
                updateFilter("回復状況", { kind: "enum", values: choice === null ? [] : [choice] })
              }
              className="px-3 py-1 rounded border text-xs transition-all"
              style={
                selected
                  ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
                  : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
              }
            >
              {choice ?? "すべて"}
            </button>
          );
        })}
      </div>

      {/* 表示の切り替えとページング */}
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <label className="flex items-center gap-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
          <input
            type="checkbox"
            checked={showAllColumns}
            onChange={(e) => setShowAllColumns(e.target.checked)}
          />
          全列を表示
        </label>
        <button
          onClick={resetColumnOrder}
          disabled={columnOrder === null}
          className="flex items-center gap-1 px-2 py-1 rounded border text-xs disabled:opacity-40"
          style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
          title="列の並び順を RESULT_COLUMNS の既定に戻す"
        >
          <RotateCcw className="w-3 h-3" />
          列順を既定に戻す
        </button>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          列は見出しの <GripVertical className="inline w-3 h-3" /> をドラッグして並べ替えられます
        </span>
        <span className="ml-auto text-xs" style={{ color: "var(--text-muted)" }}>
          {page
            ? `${page.total} 件中 ${page.total === 0 ? 0 : offset + 1}–${Math.min(offset + PAGE_SIZE, page.total)} 件`
            : ""}
        </span>
        <button
          onClick={() => setOffset((v) => Math.max(0, v - PAGE_SIZE))}
          disabled={offset === 0}
          className="px-2 py-1 rounded border text-xs disabled:opacity-40"
          style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
        >
          前へ
        </button>
        <button
          onClick={() => setOffset((v) => v + PAGE_SIZE)}
          disabled={!page || offset + PAGE_SIZE >= page.total}
          className="px-2 py-1 rounded border text-xs disabled:opacity-40"
          style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
        >
          次へ
        </button>
      </div>

      {/* 掛かっているフィルタ（絞り込み後の件数と一括解除） */}
      {activeColumns.length > 0 && (
        <div
          className="flex flex-wrap items-center gap-2 mb-3 p-2 border rounded-lg"
          style={{ borderColor: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.06)" }}
        >
          <FilterIcon className="w-3.5 h-3.5" style={{ color: "var(--cyan)" }} />
          <span className="text-xs" style={{ color: "var(--cyan)" }}>
            フィルタ {activeColumns.length} 列 → {page?.total ?? 0} 件
          </span>
          {activeColumns.map(({ column, f }) => (
            <span
              key={column}
              className="flex items-center gap-1 px-2 py-0.5 rounded border text-xs"
              style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
            >
              {column}: {describeFilter(f)}
              <button
                onClick={() => updateFilter(column, emptyFilter(f.kind))}
                style={{ color: "var(--text-muted)" }}
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
          <button
            onClick={() => { setFilters({}); setOffset(0); }}
            className="ml-auto px-2 py-1 rounded border text-xs"
            style={btnStyle}
          >
            フィルタを一括解除
          </button>
        </div>
      )}

      {isLoading && !page ? (
        <div className="flex justify-center py-16">
          <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading result...</div>
        </div>
      ) : !page || page.total === 0 ? (
        <div
          className="border rounded-lg py-16 text-center text-sm"
          style={{ ...cardStyle, color: "var(--text-secondary)" }}
        >
          {activeColumns.length > 0
            ? "フィルタに一致する区間はありません（フィルタを解除すると全件表示に戻ります）"
            : "検出された区間はありません（0 件）"}
        </div>
      ) : (
        <div className="border rounded-lg overflow-x-auto" style={cardStyle}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left" style={{ borderColor: "var(--chart-grid)" }}>
                {columns.map((col) => {
                  const kind = page.column_kinds[col] ?? "text";
                  const value = filters[col] ?? emptyFilter(kind);
                  const active = isHangapFilterActive(filters[col]);
                  return (
                    <th
                      key={col}
                      className={`py-3 px-3 font-normal select-none whitespace-nowrap ${dragging === col ? "opacity-40" : ""}`}
                      style={{ color: "var(--text-muted)" }}
                      onDragOver={(e) => {
                        if (!dragging) return;
                        e.preventDefault();  // これが無いと drop が発火しない
                        e.dataTransfer.dropEffect = "move";
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        const from = dragging ?? e.dataTransfer.getData("text/plain");
                        if (from) moveColumn(from, col);
                        setDragging(null);
                      }}
                    >
                      <span className="inline-flex items-center gap-1">
                        <span
                          draggable
                          onDragStart={(e) => {
                            // Firefox はデータが無いとドラッグを開始しない
                            e.dataTransfer.setData("text/plain", col);
                            e.dataTransfer.effectAllowed = "move";
                            setDragging(col);
                          }}
                          onDragEnd={() => setDragging(null)}
                          className="cursor-grab"
                          title="ドラッグして列を並べ替え"
                        >
                          <GripVertical className="w-3 h-3" />
                        </span>
                        <button
                          className="inline-flex items-center gap-1"
                          onClick={() => toggleSort(col)}
                          title="クリックで並べ替え"
                        >
                          {col}
                          <ArrowUpDown
                            className="w-3 h-3"
                            style={{ color: sort === col ? "var(--cyan)" : "var(--text-muted)" }}
                          />
                        </button>
                        <button
                          onClick={(e) => {
                            const rect = e.currentTarget.getBoundingClientRect();
                            setOpenFilter((v) =>
                              v?.column === col
                                ? null
                                : {
                                    column: col,
                                    top: rect.bottom + 4,
                                    // 画面右端からはみ出さない位置に寄せる
                                    left: Math.max(
                                      8,
                                      Math.min(rect.left, window.innerWidth - PANEL_WIDTH - 8)
                                    ),
                                  }
                            );
                          }}
                          title="この列で絞り込む"
                        >
                          <FilterIcon
                            className="w-3 h-3"
                            style={{ color: active ? "var(--cyan)" : "var(--text-muted)" }}
                          />
                        </button>
                      </span>
                      {openFilter?.column === col && (
                        <FilterPanel
                          column={col}
                          kind={kind}
                          choices={page.enum_choices[col] ?? []}
                          value={value}
                          anchor={openFilter}
                          onChange={(next) => updateFilter(col, next)}
                          onClose={() => setOpenFilter(null)}
                        />
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {page.rows.map((row, i) => (
                <tr
                  key={`${row["ap_name"]}-${row["区間番号"]}-${offset + i}`}
                  className="border-b"
                  style={{ borderColor: "var(--chart-grid)" }}
                >
                  {columns.map((col) => (
                    <td
                      key={col}
                      className="py-2.5 px-3 whitespace-nowrap font-mono text-xs"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {fmtCell(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
