"use client";

import {
  AlertTriangle, Archive, ChevronDown, Clock, Download, Eye, Play, RadioTower,
  RefreshCw, ShieldCheck, Trash2, X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import {
  deleteRrmSavedResult, downloadPseudonymized, fetchRrmJob, fetchRrmLogSites,
  fetchRrmResult, fetchRrmSavedResults, fetchRrmSavedRows, fetchSiteAps,
  getPseudonymizedResultUrl, getRrmDownloadUrl, startRrmAnalysis,
  HangapLogSite, HangapLogSites, PSEUDONYMIZE_NOTICE, RrmAnalyzeBody, RrmJob,
  RrmMeta, RrmPhase, RrmResult, RrmRow, RrmSavedResult, rrmClassColor,
} from "@/lib/api";
import DataTable, { DataTableColumn } from "@/app/components/DataTable";
import DownloadLink from "@/app/components/DownloadLink";
import MaskToggle from "@/app/components/MaskToggle";
import RrmCharts from "@/app/components/RrmCharts";
import TabNav from "@/app/components/TabNav";
import ThemeToggle from "@/app/components/ThemeToggle";
import { ColumnKind } from "@/lib/tableSort";
import { DOWNLOAD_DISABLED_TITLE, prefetchForMask } from "@/lib/mask";
import { toLocalString } from "@/lib/time";
import { useMask, useTimezone } from "@/app/providers";

const PHASE_LABELS: Record<RrmPhase, string> = {
  loading: "読み込み中",
  events: "イベント分類中",
  metrics: "前後メトリクス突合中",
  aggregate: "集計中",
  writing: "書き出し中",
};

const POLL_INTERVAL_MS = 2000;
/** ポーリングを続ける上限。これを過ぎたら止めて「状態を再取得」を出す */
const POLL_LIMIT_MS = 15 * 60 * 1000;
/** 画面を離れて戻ってきたときに実行中のジョブを拾うためのキー */
const JOB_STORAGE_KEY = "rrm:job_id";

/** 明細テーブルに描く行数の上限。**超えたら必ず画面に書く**（黙って切らない） */
const MAX_TABLE_ROWS = 500;

/** 期間のプリセット（値を入れるだけで自動実行はしない） */
const PRESETS = [
  { label: "直近6時間", hours: 6 },
  { label: "直近24時間", hours: 24 },
  { label: "直近3日", hours: 72 },
  { label: "直近7日", hours: 168 },
] as const;

const cardStyle = { borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" };

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * Date を datetime-local の値（``YYYY-MM-DDTHH:mm``）にする。
 * **ローカルの日時要素をそのまま並べる。** ログの時刻は naive なので、
 * UTC への変換（toISOString など）を挟むと窓がずれる。
 */
function toLocalInput(d: Date): string {
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

function formatDataRange(period: (string | null)[] | null | undefined): string | null {
  if (!period) return null;
  const [rawFirst, rawLast] = period;
  if (!rawFirst || !rawLast) return null;
  const first = rawFirst.slice(0, 16);
  const last = rawLast.slice(0, 16);
  const sameDay = first.slice(0, 10) === last.slice(0, 10);
  return `${first} 〜 ${sameDay ? last.slice(11) : last}`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const siteLabel = (site: HangapLogSite) => site.site_name || site.site_id;

function siteDetail(site: HangapLogSite): string {
  const range = formatDataRange([site.first, site.last]);
  return `AP ${site.ap_count} 台` + (range ? ` / ${range}` : "");
}

/**
 * 対象サイトの選択。**複数選択可・既定は全サイト。**
 * floorpeak と違い「サイト全体のピーク」のような単一サイト前提の定義が無く、
 * サイト別の比較を出すので複数のほうが有用。
 */
function SiteSelect({
  sites, loading, value, onChange, onRefresh,
}: {
  sites: HangapLogSite[] | undefined;
  loading: boolean;
  value: string[];
  onChange: (next: string[]) => void;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const list = sites ?? [];
  const summary =
    value.length === 0
      ? `すべてのサイト（${list.length || "-"}）`
      : value
          .map((id) => list.find((s) => s.site_id === id))
          .map((s, i) => (s ? siteLabel(s) : value[i]))
          .join(", ");

  const toggle = (siteId: string) => {
    onChange(
      value.includes(siteId) ? value.filter((v) => v !== siteId) : [...value, siteId]
    );
  };

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div className="relative" ref={containerRef}>
      <button
        ref={triggerRef}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 mt-1 px-2 py-1.5 rounded border text-sm w-72 text-left"
        style={{
          borderColor: "var(--chart-grid)",
          backgroundColor: "var(--bg-primary)",
          color: "var(--text-primary)",
        }}
      >
        <span className="truncate">{loading && !sites ? "読み込み中..." : summary}</span>
        <ChevronDown className="w-4 h-4 ml-auto shrink-0" />
      </button>

      {open && (
          <div
            className="absolute z-50 mt-1 w-96 max-h-80 overflow-y-auto p-2 border rounded-lg shadow-lg"
            style={cardStyle}
          >
            <div className="flex items-center gap-2 px-1 pb-2">
              <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                収集済みログに含まれるサイト（複数選べます）
              </span>
              <button
                onClick={onRefresh}
                className="ml-auto flex items-center gap-1 px-1.5 py-0.5 border rounded text-[10px]"
                style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
              >
                <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
                再取得
              </button>
            </div>

            <button
              onClick={() => onChange([])}
              className="w-full text-left px-2 py-1.5 mb-1 rounded border text-xs"
              style={
                value.length === 0
                  ? { borderColor: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
                  : { borderColor: "var(--chart-grid)" }
              }
            >
              <span style={{ color: "var(--text-primary)" }}>すべてのサイト（既定）</span>
            </button>

            {list.length === 0 && (
              <p className="px-2 py-3 text-xs" style={{ color: "var(--text-muted)" }}>
                {loading ? "読み込み中..." : "ログにサイトが見つかりません"}
              </p>
            )}

            {list.map((site) => {
              const active = value.includes(site.site_id);
              return (
                <button
                  key={site.site_id}
                  onClick={() => toggle(site.site_id)}
                  className="w-full text-left px-2 py-1.5 mb-1 rounded border text-xs flex items-start gap-2"
                  style={
                    active
                      ? { borderColor: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
                      : { borderColor: "var(--chart-grid)" }
                  }
                >
                  <input type="checkbox" readOnly checked={active} className="mt-0.5" />
                  <span className="min-w-0">
                    <span className="block truncate" style={{ color: "var(--text-primary)" }}>
                      {siteLabel(site)}
                    </span>
                    <span className="block text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                      {siteDetail(site)}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
      )}
    </div>
  );
}

/** 警告。**結果より上に出す**（前提を知らずにグラフだけ見ると誤読する） */
function Warnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <section
      className="border rounded-lg p-4 mb-6"
      style={{ borderColor: "var(--yellow)", backgroundColor: "rgba(255,215,0,0.08)" }}
    >
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className="w-5 h-5" style={{ color: "var(--yellow)" }} />
        <p className="text-sm font-semibold" style={{ color: "var(--yellow)" }}>
          警告 {warnings.length} 件（結果を共有する前に必ず読むこと）
        </p>
      </div>
      <ul className="space-y-1.5">
        {warnings.map((w) => (
          <li key={w} className="text-sm flex gap-2" style={{ color: "var(--text-primary)" }}>
            <span style={{ color: "var(--yellow)" }}>⚠</span>
            <span>{w}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 分析条件。**常時表示する**（折りたたまない） */
function ConditionPanel({ meta }: { meta: RrmMeta }) {
  const sites = meta.site_labels?.length ? meta.site_labels.join(", ") : "すべて";
  const rows: [string, string][] = [
    ["対象サイト", meta.requested_sites?.length ? sites : `すべて（${sites}）`],
    [
      "指定期間",
      `${meta.window_start ?? "(指定なし)"} 〜 ${meta.window_end ?? "(指定なし)"}（半開区間 [開始, 終了)）`,
    ],
    ["バケット幅", `${Math.round((meta.bucket_seconds ?? 3600) / 60)} 分（1 時間）`],
    [
      "サンプリング間隔",
      `${meta.interval_seconds ?? "-"} 秒`
        + (meta.interval_estimated === false ? "（推定できず既定値）" : "（ログから推定）")
        + ` / 照合しきい値 ${meta.gap_factor ?? "-"} 倍`,
    ],
    ["レーダー突合の時間差", `±${meta.radar_match_seconds ?? "-"} 秒`],
    [
      "使用したログ",
      `走査 ${meta.files_scanned ?? "-"} ファイル / ap_metrics ${meta.metrics_files ?? "-"} ファイル`
        + ` / ap_events ${meta.events_files ?? "-"} ファイル`,
    ],
    [
      "イベント行数",
      `期間内 ${meta.events_rows_in_window ?? "-"} 行 / 全期間 ${meta.events_rows_all ?? "-"} 行`,
    ],
  ];
  return (
    <div className="border rounded-lg p-4" style={cardStyle}>
      <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>分析条件</p>
      <dl className="grid grid-cols-1 gap-y-1.5 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex gap-3">
            <dt className="shrink-0 w-40" style={{ color: "var(--text-muted)" }}>{label}</dt>
            <dd className="font-mono text-xs pt-0.5 break-all" style={{ color: "var(--text-primary)" }}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Stat({ label, value, note, color }: {
  label: string; value: string | number; note?: string; color?: string;
}) {
  return (
    <div className="border rounded-lg px-3 py-2" style={cardStyle}>
      <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="font-mono text-lg" style={{ color: color ?? "var(--text-primary)" }}>{value}</p>
      {note && <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>{note}</p>}
    </div>
  );
}

/** サマリ。**no-op / 照合不可 / 汚染 / レーダーの内訳を必ず出す。** */
function SummaryPanel({ meta }: { meta: RrmMeta }) {
  const classes = meta.classifications ?? ["RADAR", "POST_RADAR", "RRM"];
  const breakdown = (table: Partial<Record<string, number>>) =>
    classes.map((c) => `${c}=${table?.[c] ?? 0}`).join(" ");
  const matches = meta.match_status_counts ?? {};

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-4 gap-3">
      <Stat
        label="対象イベント（AP_RRM_ACTION）"
        value={meta.event_count}
        note={`期間内の AP_RRM_ACTION の行数`}
      />
      <Stat
        label="チャネル変更（pre ≠ post）"
        value={meta.change_count}
        note={breakdown(meta.changes_by_class)}
        color="var(--cyan)"
      />
      <Stat
        label="評価のみ no-op（pre = post）"
        value={meta.noop_count}
        note={`${breakdown(meta.noop_by_class)} ／ 異常ではありません`}
      />
      <Stat
        label="インパクト合計"
        value={`${meta.impact_total} 台`}
        note="変更直前に接続していた端末数の合計"
        color="var(--cyan)"
      />
      <Stat
        label="照合不可"
        value={meta.unmatched_count}
        note={`ok=${matches.ok ?? 0} no_before=${matches.no_before ?? 0} no_after=${matches.no_after ?? 0} too_far=${matches.too_far ?? 0} no_ap=${matches.no_ap ?? 0}`}
        color={meta.unmatched_count > 0 ? "var(--yellow)" : undefined}
      />
      <Stat
        label="汚染（前後区間に別の変更）"
        value={meta.contaminated_count}
        note="除外していません。行に印が付いています"
        color={meta.contaminated_count > 0 ? "var(--yellow)" : undefined}
      />
      <Stat
        label="AP_RADAR_DETECTED"
        value={meta.radar_detected}
        note={`うちチャネル変更あり ${meta.radar_with_change}`}
      />
      <Stat
        label="ACTION 未記録のレーダー"
        value={meta.radar_without_action}
        note="AP_RRM_ACTION だけを数えると取りこぼす分"
        color={meta.radar_without_action > 0 ? "var(--yellow)" : undefined}
      />
    </div>
  );
}

/** 分類別・サイト別の集計表 */
function SummaryTables({ meta }: { meta: RrmMeta }) {
  const head = ["変更", "no-op", "インパクト計", "インパクト平均", "Δ端末", "Δ2.4G", "Δ5G", "Δ6G", "汚染", "照合不可"];
  const cell = (v: number | null | undefined) => (v === null || v === undefined ? "-" : v);

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      {([
        ["分類別", "classification", meta.by_classification ?? []],
        ["サイト別", "site_name", meta.by_site ?? []],
      ] as const).map(([title, key, list]) => (
        <div key={title} className="border rounded-lg p-4 overflow-x-auto" style={cardStyle}>
          <p className="text-xs mb-2" style={{ color: "var(--cyan)" }}>{title}</p>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left" style={{ borderColor: "var(--chart-grid)" }}>
                <th className="py-1.5 px-2 font-normal" style={{ color: "var(--text-muted)" }}>
                  {title === "分類別" ? "分類" : "サイト"}
                </th>
                {head.map((h) => (
                  <th key={h} className="py-1.5 px-2 font-normal whitespace-nowrap"
                      style={{ color: "var(--text-muted)" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {list.map((item) => {
                const label = String((item as unknown as Record<string, unknown>)[key] ?? "");
                return (
                  <tr key={label} className="border-b" style={{ borderColor: "var(--chart-grid)" }}>
                    <td className="py-1.5 px-2 whitespace-nowrap" style={{ color: "var(--text-primary)" }}>
                      {title === "分類別" && (
                        <span
                          className="inline-block w-2.5 h-2.5 rounded-sm mr-1.5 align-middle"
                          style={{ backgroundColor: rrmClassColor(label, meta) }}
                        />
                      )}
                      {label}
                    </td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--cyan)" }}>{item.changes}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{item.noop}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{item.impact_total}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{cell(item.impact_avg)}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{cell(item.delta_clients_avg)}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{cell(item.delta_util_24_avg)}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{cell(item.delta_util_5_avg)}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{cell(item.delta_util_6_avg)}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{item.contaminated}</td>
                    <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{item.unmatched}</td>
                  </tr>
                );
              })}
              {list.length === 0 && (
                <tr>
                  <td colSpan={head.length + 1} className="py-6 text-center"
                      style={{ color: "var(--text-muted)" }}>
                    対象がありません
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

const HIGHLIGHT_CONTAMINATED = "rgba(255,215,0,0.10)";
const HIGHLIGHT_UNMATCHED = "rgba(255,68,68,0.08)";

/** 27 列それぞれの型。DataTable のソート・フィルタに使う（列自体は API の columns をそのまま使う） */
const RRM_COLUMN_KIND: Record<string, ColumnKind> = {
  event_timestamp: "time",
  classification: "enum",
  reason: "text",
  site_name: "text",
  ap_name: "text",
  ap_mac: "text",
  band: "enum",
  pre_channel: "number",
  post_channel: "number",
  channel_changed: "bool",
  before_timestamp: "time",
  after_timestamp: "time",
  match_status: "enum",
  contaminated: "bool",
  clients_before: "number",
  clients_after: "number",
  clients_delta: "number",
  util_24_before: "number",
  util_24_after: "number",
  util_24_delta: "number",
  util_5_before: "number",
  util_5_after: "number",
  util_5_delta: "number",
  util_6_before: "number",
  util_6_after: "number",
  util_6_delta: "number",
  impact_clients: "number",
};

/** match_status の意味（rrm/metrics.py の判定基準と一致させること） */
const MATCH_STATUS_EXPLANATIONS: [string, string][] = [
  ["ok", "前後とも直近サンプルが見つかり、推定間隔の範囲内だった（差分を信頼してよい）"],
  ["no_before", "変更前の直近サンプルが見つからなかった（そのAPの記録が変更時刻より後にしかない）"],
  ["no_after", "変更後の直近サンプルが見つからなかった（そのAPの記録が変更時刻より前にしかない）"],
  ["too_far", "前後どちらかのサンプルは見つかったが、推定間隔の3倍以上離れていた（間が空きすぎて直近とは言えない）"],
  ["no_ap", "そのAPのサンプルが ap_metrics に1件も無い（前後どころかAP自体の記録が無い）"],
];

function renderCell(column: string, row: RrmRow, meta: RrmMeta) {
  const value = (row as unknown as Record<string, unknown>)[column];
  if (column === "classification") {
    return (
      <span className="whitespace-nowrap">
        <span
          className="inline-block w-2.5 h-2.5 rounded-sm mr-1.5 align-middle"
          style={{ backgroundColor: rrmClassColor(String(value), meta) }}
        />
        {String(value)}
      </span>
    );
  }
  if (column === "channel_changed") {
    return value ? "変更あり" : "no-op";
  }
  if (column === "contaminated") {
    return value ? "⚠ 汚染" : "-";
  }
  if (column === "match_status") {
    return String(value);
  }
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

/**
 * 「汚染」「照合不可（match_status）」の意味の説明。常時表示ではなく折りたたみ
 * （details/summary）にする。読んだ人が「なぜこの行に印がついているか」
 * 「なぜあの行にはついていないか」を判断できることを基準にした文言。
 */
function ContaminationExplanation() {
  return (
    <details className="text-xs mb-3 border rounded-lg" style={cardStyle}>
      <summary
        className="px-3 py-2 cursor-pointer select-none"
        style={{ color: "var(--cyan)" }}
      >
        「汚染」「照合不可（match_status）」の判定基準を見る
      </summary>
      <div className="px-3 pb-3 pt-1 space-y-3" style={{ color: "var(--text-secondary)" }}>
        <div>
          <p className="font-semibold mb-1" style={{ color: "var(--text-primary)" }}>汚染（contaminated）とは</p>
          <p>
            チャネル変更の前後で取得した接続端末数・利用率は、直前サンプルと直後サンプルの
            2点だけを比較しています。この区間内に同じAPで別のチャネル変更（同一バンド）が
            起きていた場合、比較結果にその影響が混ざっている可能性があるため「汚染」と印を付けます。
          </p>
          <p className="mt-1">
            <span style={{ color: "var(--text-muted)" }}>汚染に含めないもの: </span>
            同じ瞬間（{RADAR_MATCH_HINT}秒以内）に複数バンド（2.4/5/6GHz）で同時に変更が起きた場合は、
            1回のRRM動作とみなし汚染にしません。前後どちらかのサンプルが無い行（match_status が ok
            以外）は区間そのものが確定できないため、汚染の判定自体を行いません。
          </p>
        </div>
        <div>
          <p className="font-semibold mb-1" style={{ color: "var(--text-primary)" }}>match_status（照合不可の理由）</p>
          <table className="text-xs">
            <tbody>
              {MATCH_STATUS_EXPLANATIONS.map(([status, text]) => (
                <tr key={status}>
                  <td className="pr-3 py-0.5 font-mono align-top whitespace-nowrap" style={{ color: "var(--cyan)" }}>
                    {status}
                  </td>
                  <td className="py-0.5">{text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  );
}

/** 汚染判定で「同一 RRM アクショングループ」とみなす時間窓（秒）。backend/rrm/metrics.py の CONTAMINATION_GROUP_SECONDS と同じ */
const RADAR_MATCH_HINT = 5;

/**
 * 明細テーブル。列は **API が返した columns をそのまま** 使う
 * （フロントで列を選び直すと csv / xlsx と食い違う）。
 * 汚染・照合不可の行は背景色と印で一目で分かるようにする。
 * 列ヘッダーのソート・列ごとのフィルタ（DataTable）は表示専用で、
 * ダウンロード（xlsx / csv）の内容には影響しない。
 */
function DetailTable({ result }: { result: RrmResult }) {
  const meta = result.meta;
  const [onlySuspect, setOnlySuspect] = useState(false);
  const columns = result.columns;

  const rows = useMemo(
    () =>
      onlySuspect
        ? result.rows.filter((r) => r.contaminated || r.match_status !== "ok")
        : result.rows,
    [result.rows, onlySuspect]
  );

  const tableColumns: DataTableColumn<RrmRow>[] = useMemo(
    () =>
      columns.map((c) => ({
        key: c,
        label: c,
        kind: RRM_COLUMN_KIND[c] ?? "text",
        getValue: (row: RrmRow) => (row as unknown as Record<string, unknown>)[c],
        render: (row: RrmRow) => renderCell(c, row, meta),
      })),
    [columns, meta]
  );

  return (
    <section className="border rounded-lg p-4" style={cardStyle}>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <p className="text-xs" style={{ color: "var(--cyan)" }}>
          明細（AP_RRM_ACTION の全行 / {result.rows.length} 件）
        </p>
        <label className="text-xs flex items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
          <input
            type="checkbox"
            checked={onlySuspect}
            onChange={(e) => setOnlySuspect(e.target.checked)}
          />
          汚染・照合不可の行だけ表示（{result.rows.filter((r) => r.contaminated || r.match_status !== "ok").length} 件）
        </label>
        <span className="text-[10px] flex items-center gap-3 ml-auto" style={{ color: "var(--text-muted)" }}>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: HIGHLIGHT_CONTAMINATED }} />
            汚染（前後区間に別のチャネル変更）
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: HIGHLIGHT_UNMATCHED }} />
            照合不可（差分なし）
          </span>
        </span>
      </div>

      <ContaminationExplanation />

      <DataTable
        columns={tableColumns}
        rows={rows}
        rowKey={(row, i) => `${row.event_timestamp}/${row.ap_mac}/${row.band}/${i}`}
        rowStyle={(row) =>
          row.match_status !== "ok"
            ? { backgroundColor: HIGHLIGHT_UNMATCHED }
            : row.contaminated ? { backgroundColor: HIGHLIGHT_CONTAMINATED } : undefined
        }
        maxRows={MAX_TABLE_ROWS}
      />
    </section>
  );
}

/**
 * 結果の表示。**分析直後と保存済み結果で同じコンポーネントを使う**
 * （別実装を作らないこと）。
 */
function ResultView({
  result, source,
}: {
  result: RrmResult;
  source: { kind: "job"; jobId: string } | { kind: "saved"; name: string };
}) {
  const meta = result.meta;
  const linkStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };

  return (
    <div className="flex flex-col gap-4 mb-6">
      <SummaryPanel meta={meta} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ConditionPanel meta={meta} />
        <div className="lg:col-span-2 border rounded-lg p-4" style={cardStyle}>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              参考: AP_CONFIG_CHANGED_BY_RRM {meta.config_changed_by_rrm_count} 件
              <span className="ml-1">（reason を持たないため本分析では未使用）</span>
            </p>
            <div className="ml-auto flex items-center gap-2">
              {(["xlsx", "csv"] as const).map((format) => (
                <DownloadLink
                  key={format}
                  href={getRrmDownloadUrl(source, format)}
                  className="flex items-center gap-1 px-2 py-1 border rounded text-xs"
                  style={linkStyle}
                >
                  <Download className="w-3.5 h-3.5" />
                  {format}
                </DownloadLink>
              ))}
            </div>
          </div>
          <p className="text-xs mt-3 whitespace-pre-wrap font-mono break-all"
             style={{ color: "var(--text-secondary)" }}>
            {meta.condition_text}
          </p>
          {meta.chart_buckets_total !== undefined
            && meta.chart_buckets_shown !== undefined
            && meta.chart_buckets_total > meta.chart_buckets_shown && (
            <p className="text-[10px] mt-2" style={{ color: "var(--text-muted)" }}>
              xlsx のグラフは直近 {meta.chart_buckets_shown} バケットのみ
              （全 {meta.chart_buckets_total} バケットは summary シートと csv にあります）。
              画面のグラフは全バケットを表示しています。
            </p>
          )}
        </div>
      </div>

      <RrmCharts meta={meta} />
      <SummaryTables meta={meta} />
      <DetailTable result={result} />
    </div>
  );
}

/** 保存済み結果の表示（過去の結果であることが分かる帯を必ず上に出す） */
function SavedResultView({ row, onClose }: { row: RrmSavedResult; onClose: () => void }) {
  const { timezone } = useTimezone();
  const savedAt = row.saved_at ? toLocalString(row.saved_at, timezone) : row.name;
  const { data, error, isLoading } = useSWR<RrmResult>(
    ["rrm-saved-rows", row.name],
    () => fetchRrmSavedRows(row.name)
  );

  return (
    <section className="mb-8">
      <div
        className="border rounded-lg p-4 mb-4 flex flex-wrap items-center gap-3"
        style={{ borderColor: "var(--purple)", backgroundColor: "rgba(124,58,237,0.10)" }}
      >
        <Clock className="w-5 h-5 shrink-0" style={{ color: "var(--purple)" }} />
        <div>
          <p className="text-sm font-semibold" style={{ color: "var(--purple)" }}>
            保存済みの分析結果を表示しています（保存日時 {savedAt}）
          </p>
          <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
            これは過去の結果です。いま実行した分析の結果ではありません（{row.name}）。
          </p>
        </div>
        <button
          onClick={onClose}
          className="ml-auto flex items-center gap-1.5 px-3 py-2 border rounded-lg text-sm"
          style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
        >
          <X className="w-4 h-4" />
          表示をやめる
        </button>
      </div>

      {error && (
        <p className="text-sm mb-3" style={{ color: "var(--red)" }}>
          {error instanceof Error ? error.message : String(error)}
        </p>
      )}
      {isLoading && (
        <p className="text-sm mb-3" style={{ color: "var(--text-muted)" }}>読み込み中...</p>
      )}
      {data && (
        <>
          <Warnings warnings={data.warnings ?? []} />
          <ResultView result={data} source={{ kind: "saved", name: row.name }} />
        </>
      )}
    </section>
  );
}

function SavedResults({
  doneJobId, viewingName, onView,
}: {
  doneJobId: string | null;
  viewingName: string | null;
  onView: (row: RrmSavedResult | null) => void;
}) {
  const { timezone } = useTimezone();
  const { masked } = useMask();
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pseudonymizing, setPseudonymizing] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR<RrmSavedResult[]>(
    "rrm-saved-results",
    fetchRrmSavedResults
  );

  useEffect(() => {
    if (doneJobId) mutate();
  }, [doneJobId, mutate]);

  // 仮名化ダウンロードは csv のみ。xlsx は自由記述にサイト名・時刻が入るため対象外
  // （hangap/page.tsx の同名ハンドラと同じ設計。新しい設計をしない）。
  const handlePseudonymize = async (row: RrmSavedResult) => {
    setPseudonymizing(row.name);
    setError(null);
    try {
      await downloadPseudonymized(getPseudonymizedResultUrl(row.name, "rrm"), `${row.name}.csv`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPseudonymizing(null);
    }
  };

  const handleDelete = async (row: RrmSavedResult) => {
    const when = row.saved_at ? toLocalString(row.saved_at, timezone) : row.name;
    if (!window.confirm(`${when} の分析結果を削除します。\nxlsx / csv / json をまとめて削除し、元に戻せません。よろしいですか？`)) {
      return;
    }
    setDeleting(row.name);
    setError(null);
    try {
      await deleteRrmSavedResult(row.name);
      if (viewingName === row.name) onView(null);
      await mutate();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(null);
    }
  };

  const linkStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };

  return (
    <section className="mt-8">
      <div className="flex items-center gap-2 mb-3">
        <Archive className="w-4 h-4" style={{ color: "var(--cyan)" }} />
        <h2 className="text-sm font-display font-semibold tracking-wider" style={{ color: "var(--cyan)" }}>
          保存済みの分析結果
        </h2>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          分析が完了すると自動で保存されます。行をクリックすると画面上に表示します
        </span>
        <button
          onClick={() => mutate()}
          className="ml-auto flex items-center gap-1.5 px-2.5 py-1 border rounded text-xs"
          style={linkStyle}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
          再取得
        </button>
      </div>

      {/* 仮名化 ≠ 匿名化。落とす人がREADMEを読むとは限らないので導線の隣に置く */}
      <p className="text-xs mb-3 leading-relaxed" style={{ color: "var(--text-muted)" }}>
        「仮名化 csv」は AP名・サイト名・時刻を置き換えた csv をその場で作って返します
        （xlsx はタイトル・分析条件の自由記述が仮名化できないため対象外です）。{PSEUDONYMIZE_NOTICE}
      </p>

      {error && (
        <p className="text-sm mb-3 whitespace-pre-wrap" style={{ color: "var(--red)" }}>{error}</p>
      )}

      {!data || data.length === 0 ? (
        <div
          className="border rounded-lg py-10 text-center text-sm"
          style={{ ...cardStyle, color: "var(--text-muted)" }}
        >
          {isLoading ? "読み込み中..." : "保存済みの分析結果はありません"}
        </div>
      ) : (
        <div className="border rounded-lg overflow-x-auto" style={cardStyle}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left" style={{ borderColor: "var(--chart-grid)" }}>
                {["保存日時", "対象サイト", "変更", "no-op", "レーダー", "照合不可", "汚染", "警告", "サイズ", ""].map((h) => (
                  <th key={h} className="py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr
                  key={row.name}
                  className="border-b cursor-pointer"
                  style={{
                    borderColor: "var(--chart-grid)",
                    backgroundColor: viewingName === row.name ? "rgba(0,212,255,0.08)" : undefined,
                  }}
                  onClick={() => onView(row)}
                  title="クリックするとこの結果を画面上に表示します"
                >
                  <td className="py-2.5 px-3 whitespace-nowrap font-mono text-xs" style={{ color: "var(--text-primary)" }}>
                    {row.saved_at ? toLocalString(row.saved_at, timezone) : row.name}
                  </td>
                  <td className="py-2.5 px-3 text-xs" style={{ color: "var(--text-secondary)" }}>
                    {row.site_names?.length ? row.site_names.join(", ") : "すべて"}
                  </td>
                  <td className="py-2.5 px-3 font-mono text-xs" style={{ color: "var(--cyan)" }}>{row.change_count}</td>
                  <td className="py-2.5 px-3 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>{row.noop_count}</td>
                  <td className="py-2.5 px-3 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>
                    {row.radar_detected}
                    {row.radar_without_action > 0 && (
                      <span style={{ color: "var(--yellow)" }}>（未記録 {row.radar_without_action}）</span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>{row.unmatched_count}</td>
                  <td className="py-2.5 px-3 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>{row.contaminated_count}</td>
                  <td
                    className="py-2.5 px-3 font-mono text-xs"
                    style={{ color: row.warning_count > 0 ? "var(--yellow)" : "var(--text-muted)" }}
                  >
                    {row.warning_count}
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap font-mono text-xs" style={{ color: "var(--text-muted)" }}>
                    {formatSize(row.total_bytes)}
                  </td>
                  <td className="py-2.5 px-3">
                    <div className="flex items-center justify-end gap-2" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => onView(viewingName === row.name ? null : row)}
                        className="flex items-center gap-1 px-2 py-1 border rounded text-xs"
                        style={
                          viewingName === row.name
                            ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
                            : linkStyle
                        }
                      >
                        <Eye className="w-3.5 h-3.5" />
                        {viewingName === row.name ? "表示中" : "表示"}
                      </button>
                      {(["xlsx", "csv"] as const).map((format) => (
                        <DownloadLink
                          key={format}
                          href={getRrmDownloadUrl({ kind: "saved", name: row.name }, format)}
                          className="flex items-center gap-1 px-2 py-1 border rounded text-xs"
                          style={linkStyle}
                          disabled={row.files[format] === undefined}
                        >
                          <Download className="w-3.5 h-3.5" />
                          {format}
                        </DownloadLink>
                      ))}
                      {/* 通常のダウンロードとは別の導線。csv だけに出す */}
                      <button
                        onClick={() => handlePseudonymize(row)}
                        disabled={masked || pseudonymizing === row.name || row.files.csv === undefined}
                        title={masked ? DOWNLOAD_DISABLED_TITLE : PSEUDONYMIZE_NOTICE}
                        className="flex items-center gap-1 px-2 py-1 border rounded text-xs disabled:opacity-40"
                        style={{ borderColor: "var(--green)", color: "var(--green)" }}
                      >
                        <ShieldCheck className={`w-3.5 h-3.5 ${pseudonymizing === row.name ? "animate-pulse" : ""}`} />
                        {pseudonymizing === row.name ? "仮名化中..." : "仮名化 csv"}
                      </button>
                      <button
                        onClick={() => handleDelete(row)}
                        disabled={deleting === row.name}
                        className="flex items-center gap-1 px-2 py-1 border rounded text-xs disabled:opacity-40"
                        style={{ borderColor: "var(--red)", color: "var(--red)" }}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        削除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function RrmPage() {
  const { timezone } = useTimezone();
  const { masked } = useMask();

  const [sites, setSites] = useState<string[]>([]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [pollStopped, setPollStopped] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const [viewingSaved, setViewingSaved] = useState<RrmSavedResult | null>(null);

  const {
    data: logSites, isLoading: sitesLoading, error: sitesError, mutate: mutateSites,
  } = useSWR<HangapLogSites>("rrm-log-sites", () => fetchRrmLogSites());

  // マスク ON 中のみ: 対象サイトの AP 一覧を先行取得し、警告文（自由文）に
  // 出てくる AP 名をあらかじめ採番しておく。取りこぼし（一覧に無い AP 名）は残る。
  useEffect(() => {
    if (!masked || !logSites) return;
    const targets = sites.length > 0 ? sites : logSites.sites.map((s) => s.site_id);
    targets.forEach((siteId) => prefetchForMask(() => fetchSiteAps(siteId)));
  }, [masked, sites, logSites]);

  useEffect(() => {
    const saved = window.localStorage.getItem(JOB_STORAGE_KEY);
    if (saved) setJobId(saved);
  }, []);

  /**
   * ジョブの監視。**SWR の refreshInterval は使わない**
   * （毎秒再レンダーする画面ではポーリング用タイマーが張り直され続けて発火しない）。
   */
  const [job, setJob] = useState<RrmJob | null | undefined>(undefined);
  const [jobLoading, setJobLoading] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const reloadJob = () => setReloadToken((v) => v + 1);

  useEffect(() => {
    if (!jobId) {
      setJob(undefined);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      setJobLoading(true);
      try {
        const state = await fetchRrmJob(jobId);
        if (cancelled) return;
        setJob(state);
        if (state?.status === "running" && !pollStopped) {
          timer = window.setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch {
        // 一時的な通信エラーでは諦めない（サーバ側でジョブは走り続けている）
        if (!cancelled) timer = window.setTimeout(tick, POLL_INTERVAL_MS);
      } finally {
        if (!cancelled) setJobLoading(false);
      }
    };
    tick();

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId, pollStopped, reloadToken]);

  // 破棄済み／TTL 切れ（404）のジョブは覚えておかない
  useEffect(() => {
    if (jobId && job === null) {
      window.localStorage.removeItem(JOB_STORAGE_KEY);
      setJobId(null);
    }
  }, [job, jobId]);

  useEffect(() => {
    if (job?.status !== "running" || pollStopped) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [job?.status, pollStopped]);

  const startedAtMs = job?.started_at ? Date.parse(job.started_at) : null;
  useEffect(() => {
    if (job?.status !== "running" || startedAtMs === null) return;
    if (now - startedAtMs > POLL_LIMIT_MS) setPollStopped(true);
  }, [now, job?.status, startedAtMs]);

  const elapsedSec =
    startedAtMs === null ? null : Math.max(0, Math.floor((now - startedAtMs) / 1000));

  const doneJobId = job?.status === "done" ? job.job_id : null;
  const { data: result } = useSWR<RrmResult>(
    doneJobId ? ["rrm-result", doneJobId] : null,
    () => fetchRrmResult(doneJobId as string)
  );

  const handleRun = async () => {
    setStarting(true);
    setStartError(null);
    setConflict(null);
    setViewingSaved(null);  // 新しい分析を始めたら保存済み結果の表示はやめる
    try {
      const body: RrmAnalyzeBody = {};
      if (sites.length > 0) body.sites = sites;
      // datetime-local の値（"2026-08-15T20:00"）は API がそのまま受け付ける
      // ISO8601（TZ なし）。**変換を挟まないこと。** ログの時刻は naive なので、
      // UTC などへ直すと窓がずれる。
      if (from.trim()) body.from = from.trim();
      if (to.trim()) body.to = to.trim();
      const started = await startRrmAnalysis(body);
      if (started.conflict) setConflict(started.message ?? "別の分析が実行中です。");
      if (started.job_id) {
        window.localStorage.setItem(JOB_STORAGE_KEY, started.job_id);
        setJobId(started.job_id);
        setPollStopped(false);
        reloadJob();
      }
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const running = job?.status === "running";
  const btnStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };
  const inputStyle = {
    borderColor: "var(--chart-grid)",
    backgroundColor: "var(--bg-primary)",
    color: "var(--text-primary)",
  };

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2">
          <RadioTower className="w-5 h-5" style={{ color: "var(--cyan)" }} />
          <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
            RRM
          </h1>
        </div>
        <div className="flex items-center gap-2 flex-nowrap">
          <p className="text-xs mr-2 whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
            RRM / RADAR によるチャネル変更を分析（実行したときだけ走ります）
          </p>
          <button
            onClick={reloadJob}
            disabled={!jobId}
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all disabled:opacity-40 whitespace-nowrap"
            style={btnStyle}
          >
            <RefreshCw className={`w-4 h-4 ${jobLoading ? "animate-spin" : ""}`} />
            状態を再取得
          </button>
          <MaskToggle />
          <ThemeToggle />
        </div>
      </header>

      <TabNav />

      <section className="border rounded-lg p-4 mb-6" style={cardStyle}>
        <h2 className="text-sm font-display font-semibold mb-3 tracking-wider" style={{ color: "var(--cyan)" }}>
          分析条件
        </h2>

        <div className="flex flex-wrap items-end gap-4">
          <label className="text-xs" style={{ color: "var(--text-muted)" }}>
            サイト（複数選択可・既定は全サイト）
            <SiteSelect
              sites={logSites?.sites}
              loading={sitesLoading}
              value={sites}
              onChange={setSites}
              onRefresh={() => mutateSites(fetchRrmLogSites(true))}
            />
          </label>

          <label className="text-xs" style={{ color: "var(--text-muted)" }}>
            期間（開始）
            <input
              type="datetime-local" step={60} value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="block mt-1 px-2 py-1.5 rounded border text-sm w-56 font-mono"
              style={inputStyle}
            />
          </label>
          <label className="text-xs" style={{ color: "var(--text-muted)" }}>
            期間（終了）
            <input
              type="datetime-local" step={60} value={to}
              onChange={(e) => setTo(e.target.value)}
              className="block mt-1 px-2 py-1.5 rounded border text-sm w-56 font-mono"
              style={inputStyle}
            />
          </label>

          <button
            onClick={handleRun}
            disabled={running || starting}
            className="flex items-center gap-2 px-4 py-2 border rounded-lg text-sm transition-all disabled:opacity-40"
            style={btnStyle}
          >
            <Play className="w-4 h-4" />
            {running ? "実行中..." : "分析を実行"}
          </button>
        </div>

        {sitesError && (
          <p className="text-xs mt-2" style={{ color: "var(--red)" }}>
            サイト一覧を取得できませんでした（
            {sitesError instanceof Error ? sitesError.message : String(sitesError)}）
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2 mt-3">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>プリセット:</span>
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => {
                const current = new Date();
                setTo(toLocalInput(current));
                setFrom(toLocalInput(new Date(current.getTime() - p.hours * 3600_000)));
              }}
              className="px-3 py-1 rounded border text-xs transition-all"
              style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
            >
              {p.label}
            </button>
          ))}
          <button
            onClick={() => { setFrom(""); setTo(""); }}
            className="px-3 py-1 rounded border text-xs transition-all"
            style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
          >
            クリア
          </button>
        </div>

        <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
          期間はログの時刻表記（タイムゾーンなし）で指定します。半開区間 [開始, 終了) で、
          終了時刻ちょうどのイベントは含みません。両方とも省略すると全データが対象です。
          集計は 1 時間バケットの実時系列で、時刻（0〜23 時）で丸めた平均は出しません。
        </p>
      </section>

      {startError && (
        <div
          className="border rounded-lg p-4 mb-6 flex gap-3"
          style={{ borderColor: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }}
        >
          <AlertTriangle className="w-5 h-5 shrink-0" style={{ color: "var(--red)" }} />
          <div>
            <p className="text-sm font-semibold" style={{ color: "var(--red)" }}>分析を開始できませんでした</p>
            <p className="text-sm mt-1" style={{ color: "var(--text-primary)" }}>{startError}</p>
          </div>
        </div>
      )}

      {conflict && (
        <div
          className="border rounded-lg p-4 mb-6 flex gap-3"
          style={{ borderColor: "var(--yellow)", backgroundColor: "rgba(255,215,0,0.08)" }}
        >
          <AlertTriangle className="w-5 h-5 shrink-0" style={{ color: "var(--yellow)" }} />
          <div>
            <p className="text-sm font-semibold" style={{ color: "var(--yellow)" }}>別の分析が実行中です</p>
            <p className="text-sm mt-1" style={{ color: "var(--text-primary)" }}>
              {conflict} 下に実行中のジョブの進捗を表示しています。
            </p>
          </div>
        </div>
      )}

      {running && job && (
        <section className="border rounded-lg p-4 mb-6" style={cardStyle}>
          <div className="flex items-center gap-3">
            <RefreshCw className="w-4 h-4 animate-spin" style={{ color: "var(--cyan)" }} />
            <span className="text-sm" style={{ color: "var(--text-primary)" }}>
              {PHASE_LABELS[job.phase] ?? job.phase}
            </span>
            <span className="text-sm font-mono" style={{ color: "var(--text-secondary)" }}>
              経過 {elapsedSec ?? 0} 秒
            </span>
            {job.started_at && (
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                開始 {toLocalString(job.started_at, timezone)}
              </span>
            )}
          </div>
          {pollStopped && (
            <p className="text-xs mt-2" style={{ color: "var(--yellow)" }}>
              自動更新を停止しました（{Math.floor(POLL_LIMIT_MS / 60000)} 分経過）。
              「状態を再取得」で最新の状態を確認してください。
            </p>
          )}
        </section>
      )}

      {job?.status === "failed" && (
        <section
          className="border rounded-lg p-4 mb-6 flex gap-3"
          style={{ borderColor: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }}
        >
          <AlertTriangle className="w-6 h-6 shrink-0" style={{ color: "var(--red)" }} />
          <div>
            <p className="text-base font-semibold" style={{ color: "var(--red)" }}>
              分析は完了していません（チャネル変更が無かったのではありません）
            </p>
            <p className="text-sm mt-2 whitespace-pre-wrap" style={{ color: "var(--text-primary)" }}>
              {job.error ?? "原因が記録されていません。"}
            </p>
            <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
              指定したサイトがログに無い場合や、ap_events が 1 件も無い場合もここに来ます。
            </p>
          </div>
        </section>
      )}

      {/* 警告は結果より上に出す */}
      {job && !viewingSaved && <Warnings warnings={job.warnings} />}

      {viewingSaved && (
        <SavedResultView row={viewingSaved} onClose={() => setViewingSaved(null)} />
      )}

      {!viewingSaved && result && job?.status === "done" && (
        <>
          <ResultView result={result} source={{ kind: "job", jobId: job.job_id }} />
          {job.finished_at && (
            <p className="text-xs font-mono text-right mb-4" style={{ color: "var(--text-muted)" }}>
              完了 {toLocalString(job.finished_at, timezone)}
            </p>
          )}
        </>
      )}

      {!jobId && !startError && !viewingSaved && (
        <div
          className="border rounded-lg py-16 text-center text-sm"
          style={{ ...cardStyle, color: "var(--text-muted)" }}
        >
          サイトと期間を選んで「分析を実行」を押すと、RRM / RADAR によるチャネル変更と
          その前後のメトリクスを表示します。過去の結果は下の「保存済みの分析結果」から表示できます。
        </div>
      )}

      <SavedResults
        doneJobId={doneJobId}
        viewingName={viewingSaved?.name ?? null}
        onView={setViewingSaved}
      />
    </main>
  );
}
