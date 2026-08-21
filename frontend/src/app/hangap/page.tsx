"use client";

import {
  AlertTriangle, Archive, ChevronDown, ChevronRight, Clock,
  Download, Eye, Play, RefreshCw, ShieldCheck, Trash2, WifiOff, X,
} from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import {
  deleteHangapSavedResult, fetchHangapJob, fetchHangapLogSites, fetchHangapSavedResults,
  getHangapSavedDownloadUrl, startHangapAnalysis, fetchSiteAps,
  downloadPseudonymized, getPseudonymizedResultUrl, PSEUDONYMIZE_NOTICE,
  HangapAnalyzeBody, HangapJob, HangapLogSite, HangapLogSites, HangapPhase,
  HangapSavedResult, HangapSummary,
} from "@/lib/api";
import HangapResultTable from "@/app/components/HangapResultTable";
import MaskToggle from "@/app/components/MaskToggle";
import ThemeToggle from "@/app/components/ThemeToggle";
import TabNav from "@/app/components/TabNav";
import DownloadLink from "@/app/components/DownloadLink";
import { toLocalString } from "@/lib/time";
import { useTimezone, useMask } from "@/app/providers";
import { DOWNLOAD_DISABLED_TITLE, prefetchForMask } from "@/lib/mask";

const PHASE_LABELS: Record<HangapPhase, string> = {
  loading: "読み込み中",
  neighbors: "近傍判定中",
  detecting: "検出中",
  writing: "書き出し中",
};

/**
 * 詳細設定。placeholder は既定値の目安で、**空欄なら送らない**（バックエンドの
 * 既定値がそのまま効く）。実際に適用された条件は結果の「分析条件」に出る。
 */
const ADVANCED_FIELDS = [
  { key: "min_zero_samples", label: "連続ゼロ数", placeholder: "5" },
  { key: "event_window_minutes", label: "イベント窓(分)", placeholder: "30" },
  { key: "exodus_threshold", label: "退場判定しきい値", placeholder: "-0.5" },
  { key: "gap_factor", label: "gap factor", placeholder: "1.5" },
  { key: "neighbor_count", label: "近傍AP台数", placeholder: "4" },
  { key: "max_distance_m", label: "距離上限(m)", placeholder: "25" },
  { key: "neighbor_client_threshold", label: "周辺端末しきい値", placeholder: "1.0" },
  { key: "truncated_warn_ratio", label: "打ち切り警告比率", placeholder: "0.3" },
] as const;

type AdvancedKey = (typeof ADVANCED_FIELDS)[number]["key"];

const EMPTY_ADVANCED = Object.fromEntries(
  ADVANCED_FIELDS.map((f) => [f.key, ""])
) as Record<AdvancedKey, string>;

const POLL_INTERVAL_MS = 2000;
/** ポーリングを続ける上限。これを過ぎたら止めて「状態を再取得」を出す */
const POLL_LIMIT_MS = 15 * 60 * 1000;
/** 画面を離れて戻ってきたときに実行中のジョブを拾うためのキー */
const JOB_STORAGE_KEY = "hangap:job_id";

/** 期間のプリセット（値を入れるだけで自動実行はしない） */
const PRESETS = [
  { label: "直近1時間", hours: 1 },
  { label: "直近6時間", hours: 6 },
  { label: "直近24時間", hours: 24 },
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

/** ローダが報告した metrics の実期間を「2026-08-15 20:06 〜 21:56」の形にする */
function formatDataRange(period: (string | null)[] | null | undefined): string | null {
  if (!period) return null;
  const [rawFirst, rawLast] = period;
  if (!rawFirst || !rawLast) return null;
  const first = rawFirst.slice(0, 16); // 秒は落とす（サンプリング間隔より細かい桁は不要）
  const last = rawLast.slice(0, 16);
  const sameDay = first.slice(0, 10) === last.slice(0, 10);
  return `${first} 〜 ${sameDay ? last.slice(11) : last}`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 内訳を「回復 3 / 継続中 1」の形にする（0 件の項目は落とす） */
function formatBreakdown(counts: Record<string, number>): string {
  const parts = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([name, n]) => `${name} ${n}`);
  return parts.length ? parts.join(" / ") : "-";
}

/**
 * 対象サイトの選択。**未選択（null）では分析を実行しない。**
 * 指定漏れによる情報過多を防ぐのが目的なので、暗黙に全サイトを対象にしない。
 * `"all"` は「すべてのサイト」を明示的に選んだ状態。
 */
type SiteSelection = "all" | string[] | null;

const siteLabel = (site: HangapLogSite) => site.site_name || site.site_id;

/** 「AP 4 台 / 2026-08-15 20:06 〜 21:56」。どれを選ぶべきか判断するための情報 */
function siteDetail(site: HangapLogSite): string {
  const range = formatDataRange([site.first, site.last]);
  return `AP ${site.ap_count} 台` + (range ? ` / ${range}` : "");
}

function SiteSelect({
  sites, loading, value, onChange, onRefresh,
}: {
  sites: HangapLogSite[] | undefined;
  loading: boolean;
  value: SiteSelection;
  onChange: (next: SiteSelection) => void;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const list = sites ?? [];

  const summary = (() => {
    if (value === "all") return "すべてのサイト";
    if (value === null || value.length === 0) return "選択してください";
    if (value.length === 1) {
      const hit = list.find((s) => s.site_id === value[0]);
      return hit ? siteLabel(hit) : value[0];
    }
    return `${value.length} サイトを選択`;
  })();

  const toggle = (siteId: string) => {
    const current = Array.isArray(value) ? value : [];
    onChange(
      current.includes(siteId)
        ? current.filter((id) => id !== siteId)
        : [...current, siteId]
    );
  };

  const rowStyle = (selected: boolean) =>
    selected
      ? { borderColor: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
      : { borderColor: "var(--chart-grid)" };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 mt-1 px-2 py-1.5 rounded border text-sm w-72 text-left"
        style={{
          borderColor: value === null ? "var(--yellow)" : "var(--chart-grid)",
          backgroundColor: "var(--bg-primary)",
          color: value === null ? "var(--text-muted)" : "var(--text-primary)",
        }}
      >
        <span className="truncate">{loading && !sites ? "読み込み中..." : summary}</span>
        <ChevronDown className="w-4 h-4 ml-auto shrink-0" />
      </button>

      {open && (
        <>
          {/* 外側のクリックで閉じる */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="absolute z-50 mt-1 w-96 max-h-80 overflow-y-auto p-2 border rounded-lg shadow-lg"
            style={cardStyle}
          >
            <div className="flex items-center gap-2 px-1 pb-2">
              <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                収集済みログに含まれるサイト（監視対象の一覧ではありません）
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
              onClick={() => onChange("all")}
              className="w-full text-left px-2 py-1.5 mb-1 rounded border text-xs"
              style={rowStyle(value === "all")}
            >
              <span style={{ color: "var(--text-primary)" }}>すべてのサイト</span>
              <span className="block text-[10px]" style={{ color: "var(--text-muted)" }}>
                {list.length} サイト / AP {list.reduce((n, s) => n + s.ap_count, 0)} 台
              </span>
            </button>

            {list.length === 0 && (
              <p className="px-2 py-3 text-xs" style={{ color: "var(--text-muted)" }}>
                {loading ? "読み込み中..." : "ログにサイトが見つかりません"}
              </p>
            )}

            {list.map((site) => {
              const selected = Array.isArray(value) && value.includes(site.site_id);
              return (
                <label
                  key={site.site_id}
                  className="flex items-start gap-2 px-2 py-1.5 mb-1 rounded border text-xs cursor-pointer"
                  style={rowStyle(selected)}
                >
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={selected}
                    onChange={() => toggle(site.site_id)}
                  />
                  <span className="min-w-0">
                    <span className="block truncate" style={{ color: "var(--text-primary)" }}>
                      {siteLabel(site)}
                    </span>
                    <span
                      className="block text-[10px] font-mono"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {siteDetail(site)}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="border rounded-lg p-4" style={cardStyle}>
      <p className="text-xs mb-2" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="text-2xl font-bold" style={{ color: color ?? "var(--text-primary)" }}>
        {value}
      </p>
    </div>
  );
}

function Breakdown({ title, counts }: { title: string; counts: Record<string, number> }) {
  return (
    <div className="border rounded-lg p-4" style={cardStyle}>
      <p className="text-xs mb-2" style={{ color: "var(--text-muted)" }}>{title}</p>
      <ul className="space-y-1">
        {Object.entries(counts).map(([name, n]) => (
          <li key={name} className="flex justify-between text-sm">
            <span style={{ color: "var(--text-secondary)" }}>{name}</span>
            <span className="font-mono" style={{ color: "var(--text-primary)" }}>{n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** データ範囲・間隔・ギャップ。数字だけを見て誤読しないための前提情報 */
function DataInfo({ summary }: { summary: HangapSummary }) {
  const l = summary.loader;
  const period = (p: (string | null)[] | null) =>
    p ? `${p[0] ?? "-"} 〜 ${p[1] ?? "-"}` : "（なし）";
  const rows: [string, string][] = [
    ["ファイル数", `${l.files_scanned}（種別不明 ${l.unclassified}）`],
    ["metrics 行数", `${l.metrics_rows.toLocaleString()} / AP ${l.ap_count} 台`],
    ["events 行数", l.events_rows.toLocaleString()],
    [
      "推定サンプリング間隔",
      l.sampling_interval_seconds === null
        ? "不明"
        : `${l.sampling_interval_seconds} 秒` +
          (l.interval_groups.length > 1
            ? `（混在: ${l.interval_groups.map((g) => `${g.interval_seconds}s×${g.ap_count}台`).join(" / ")}）`
            : ""),
    ],
    [
      "ギャップ",
      `${l.gaps.count} 件 / 欠測サンプル ${l.gaps.total_missing_samples} / 最大 ${l.gaps.max_seconds} 秒`,
    ],
    ["metrics 期間", period(l.metrics_period)],
    ["events 期間", period(l.events_period)],
    [
      "rf_neighbors",
      `${l.rf_neighbors_rows.toLocaleString()} 行（最終 ${l.rf_neighbors_latest ?? "-"}）`,
    ],
  ];
  return (
    <div className="border rounded-lg p-4" style={cardStyle}>
      <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>データ情報</p>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex gap-3">
            <dt className="shrink-0" style={{ color: "var(--text-muted)" }}>{label}</dt>
            <dd className="font-mono text-xs pt-0.5" style={{ color: "var(--text-primary)" }}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** 警告。**結果テーブルより上に出す**（前提を知らずに数字だけ見ると誤読する） */
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

/**
 * 保存済み結果の表示。**結果テーブルは分析直後と同じ HangapResultTable を使う**
 * （別実装を作らないこと）。サマリーは保存時の json（一覧が返す値）から出す。
 *
 * 過去の結果であることが分かるよう、保存日時を明示した帯を必ず上に出す。
 * 分析直後の結果と取り違えると、いま直った / 直っていないの判断を誤る。
 */
function SavedResultView({ row, onClose }: { row: HangapSavedResult; onClose: () => void }) {
  const { timezone } = useTimezone();
  const savedAt = row.saved_at ? toLocalString(row.saved_at, timezone) : row.name;
  const period = (p: (string | null)[] | null) =>
    p ? `${p[0] ?? "-"} 〜 ${p[1] ?? "-"}` : "（なし）";

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

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-4">
        <Stat label="検出区間数" value={row.detected_intervals} color="var(--cyan)" />
        <Breakdown title="回復状況" counts={row.recovery_status} />
        <Breakdown title="周辺AP判定" counts={row.neighbor_verdict} />
        <Stat label="退場疑い" value={row.exodus_suspected} />
        <Stat label="イベント該当区間" value={row.event_matched_intervals} />
      </div>

      {/* 保存時の json に入っている範囲のデータ情報（分析直後の「データ情報」より粗い） */}
      <div className="border rounded-lg p-4 mb-4" style={cardStyle}>
        <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
          データ情報（保存時に記録された分）
        </p>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
          {([
            ["metrics 期間", period(row.metrics_period)],
            ["events 期間", period(row.events_period)],
            ["AP 台数", String(row.ap_count)],
            ["ファイル数", String(row.files_scanned)],
          ] as [string, string][]).map(([label, value]) => (
            <div key={label} className="flex gap-3">
              <dt className="shrink-0" style={{ color: "var(--text-muted)" }}>{label}</dt>
              <dd className="font-mono text-xs pt-0.5" style={{ color: "var(--text-primary)" }}>
                {value}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
          {row.condition_text || "分析条件は記録されていません"}
        </span>
      </div>

      <Warnings warnings={row.warnings} />

      {/* key で保存名ごとに状態（ページ・ソート・フィルタ）を作り直す */}
      <HangapResultTable key={row.name} source={{ kind: "saved", name: row.name }} />
    </section>
  );
}

/**
 * 保存済みの分析結果。分析が done で完了すると**サーバ側で自動保存**される
 * （保存ボタンは無い）。行をクリックすると SavedResultView で画面上に再表示する
 * （再分析はしない）。
 */
function SavedResults({
  doneJobId, viewingName, onView,
}: {
  doneJobId: string | null;
  viewingName: string | null;
  onView: (row: HangapSavedResult | null) => void;
}) {
  const { timezone } = useTimezone();
  const { masked } = useMask();
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pseudonymizing, setPseudonymizing] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR<HangapSavedResult[]>(
    "hangap-saved-results",
    fetchHangapSavedResults
  );

  // 分析が完了したら一覧に出るはずなので取り直す
  useEffect(() => {
    if (doneJobId) mutate();
  }, [doneJobId, mutate]);

  // 仮名化ダウンロードは csv のみ。xlsx は 1〜3 行目の自由記述（タイトル・分析条件・
  // 警告）にサイト名・site_id・時刻が入るため対象外で、導線も出さない。
  const handlePseudonymize = async (row: HangapSavedResult) => {
    setPseudonymizing(row.name);
    setError(null);
    try {
      await downloadPseudonymized(getPseudonymizedResultUrl(row.name), `${row.name}.csv`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPseudonymizing(null);
    }
  };

  const handleDelete = async (row: HangapSavedResult) => {
    const when = row.saved_at ? toLocalString(row.saved_at, timezone) : row.name;
    if (!window.confirm(`${when} の分析結果を削除します。\nxlsx / csv / json をまとめて削除し、元に戻せません。よろしいですか？`)) {
      return;
    }
    setDeleting(row.name);
    setError(null);
    try {
      await deleteHangapSavedResult(row.name);
      if (viewingName === row.name) onView(null); // 消したものを表示し続けない
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
          分析が完了すると自動で保存されます（古いものから順に整理されます）。行をクリックすると画面上に表示します
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
                {["保存日時", "検出区間数", "回復状況", "警告", "分析条件", "サイズ", ""].map((h) => (
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
                    backgroundColor:
                      viewingName === row.name ? "rgba(0,212,255,0.08)" : undefined,
                  }}
                  onClick={() => onView(row)}
                  title="クリックするとこの結果を画面上に表示します"
                >
                  <td className="py-2.5 px-3 whitespace-nowrap font-mono text-xs" style={{ color: "var(--text-primary)" }}>
                    {row.saved_at ? toLocalString(row.saved_at, timezone) : row.name}
                  </td>
                  <td className="py-2.5 px-3 font-mono text-xs" style={{ color: "var(--cyan)" }}>
                    {row.detected_intervals}
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap text-xs" style={{ color: "var(--text-secondary)" }}>
                    {formatBreakdown(row.recovery_status)}
                  </td>
                  <td
                    className="py-2.5 px-3 font-mono text-xs"
                    style={{ color: row.warning_count > 0 ? "var(--yellow)" : "var(--text-muted)" }}
                  >
                    {row.warning_count}
                  </td>
                  {/* 全文は title に入れる。条件を知らずに件数だけ見ると誤読する */}
                  <td className="py-2.5 px-3 text-xs" style={{ color: "var(--text-muted)" }}>
                    <span className="block max-w-md truncate" title={row.condition_text}>
                      {row.condition_text || "-"}
                    </span>
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap font-mono text-xs" style={{ color: "var(--text-muted)" }}>
                    {formatSize(row.total_bytes)}
                  </td>
                  <td className="py-2.5 px-3">
                    {/* 行のクリックは「表示」。ダウンロード・削除には伝播させない */}
                    <div
                      className="flex items-center justify-end gap-2"
                      onClick={(e) => e.stopPropagation()}
                    >
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
                          href={getHangapSavedDownloadUrl(row.name, format)}
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

export default function HangApPage() {
  const { timezone } = useTimezone();
  const { masked } = useMask();

  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [advanced, setAdvanced] = useState<Record<AdvancedKey, string>>(EMPTY_ADVANCED);
  const [siteSelection, setSiteSelection] = useState<SiteSelection>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [pollStopped, setPollStopped] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  /** 保存済み結果を表示中ならその 1 件（分析直後の結果とは同時に出さない） */
  const [viewingSaved, setViewingSaved] = useState<HangapSavedResult | null>(null);

  /** 対象サイトの選択肢。**ログから作る**（現在の監視対象からではない） */
  const {
    data: logSites, isLoading: sitesLoading, error: sitesError, mutate: mutateSites,
  } = useSWR<HangapLogSites>("hangap-log-sites", () => fetchHangapLogSites());

  // 選択肢が 1 つしかない環境では、利用者が意識せずに済むよう自動で選択済みにする
  useEffect(() => {
    if (siteSelection !== null || !logSites) return;
    if (logSites.sites.length === 1) setSiteSelection([logSites.sites[0].site_id]);
  }, [logSites, siteSelection]);

  // マスク ON 中のみ: 選択されたサイトの AP 一覧を先行取得し、警告文（自由文）に
  // 出てくる AP 名をあらかじめ採番しておく。取りこぼし（一覧に無い AP 名）は残る。
  useEffect(() => {
    if (!masked) return;
    const siteIds = siteSelection === "all"
      ? (logSites?.sites ?? []).map((s) => s.site_id)
      : siteSelection ?? [];
    for (const siteId of siteIds) {
      prefetchForMask(() => fetchSiteAps(siteId));
    }
  }, [masked, siteSelection, logSites]);

  // 画面を離れてもジョブはサーバ側で走り続ける。戻ってきたら拾い直す
  useEffect(() => {
    const saved = window.localStorage.getItem(JOB_STORAGE_KEY);
    if (saved) setJobId(saved);
  }, []);

  /**
   * ジョブの監視。**SWR の refreshInterval は使わない。**
   * 経過秒数の表示で毎秒再レンダーされる画面では、SWR のポーリング用タイマーが
   * 張り直され続けて 2 秒間隔のポーリングが発火しなかった（実行中のまま画面が
   * 止まる）。ここでは setTimeout を自分で回し、止める条件も明示する。
   */
  const [job, setJob] = useState<HangapJob | null | undefined>(undefined);
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
        const state = await fetchHangapJob(jobId);
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

  // 経過秒数の表示用。実行中だけ動かす
  useEffect(() => {
    if (job?.status !== "running" || pollStopped) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [job?.status, pollStopped]);

  // ポーリングは無制限に続けない
  const startedAtMs = job?.started_at ? Date.parse(job.started_at) : null;
  useEffect(() => {
    if (job?.status !== "running" || startedAtMs === null) return;
    if (now - startedAtMs > POLL_LIMIT_MS) setPollStopped(true);
  }, [now, job?.status, startedAtMs]);

  const elapsedSec =
    startedAtMs === null ? null : Math.max(0, Math.floor((now - startedAtMs) / 1000));

  const attach = (id: string) => {
    if (!id) return;
    window.localStorage.setItem(JOB_STORAGE_KEY, id);
    setJobId(id);
    setPollStopped(false);
  };

  const handleRun = async () => {
    setStarting(true);
    setStartError(null);
    setConflict(null);
    // 新しい分析を始めたら保存済み結果の表示はやめる（取り違えを防ぐ）
    setViewingSaved(null);
    try {
      const body: HangapAnalyzeBody = {};
      // 「すべてのサイト」は sites を送らない（バックエンドの既定が全サイト）。
      // 未選択のまま実行はできない（ボタンを押せない）ので、ここでは分岐しない。
      if (Array.isArray(siteSelection)) body.sites = siteSelection;
      // datetime-local の値（"2026-08-15T20:00"）は API がそのまま受け付ける
      // ISO8601（TZ なし）。**変換を挟まないこと。** ログの時刻は naive なので、
      // UTC などへ直すと窓がずれる。
      if (from.trim()) body.from = from.trim();
      if (to.trim()) body.to = to.trim();
      for (const f of ADVANCED_FIELDS) {
        const raw = advanced[f.key].trim();
        if (raw === "") continue;
        const n = Number(raw);
        if (!Number.isFinite(n)) {
          setStartError(`${f.label}: 数値で指定してください（${raw}）`);
          return;
        }
        (body as Record<AdvancedKey, number>)[f.key] = n;
      }
      const started = await startHangapAnalysis(body);
      if (started.conflict) {
        setConflict(started.message ?? "別の分析が実行中です。");
      }
      if (started.job_id) {
        attach(started.job_id);
        reloadJob();
      }
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const summary = job?.status === "done" ? job.summary : null;

  // 直近の分析で読み込めたデータの実期間。次に期間を指定するときの目安として、
  // 新しい分析を回している間も直前の値を出したままにする
  const [dataRange, setDataRange] = useState<string | null>(null);
  useEffect(() => {
    const range = formatDataRange(job?.summary?.loader.metrics_period);
    if (range) setDataRange(range);
  }, [job]);

  const running = job?.status === "running";
  const btnStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };

  /** 対象サイトを選ぶまで分析は実行できない（暗黙に全サイトを対象にしない） */
  const siteReady = siteSelection === "all" || (Array.isArray(siteSelection) && siteSelection.length > 0);

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2">
          <WifiOff className="w-5 h-5" style={{ color: "var(--cyan)" }} />
          <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
            Hang AP
          </h1>
        </div>
        <div className="flex items-center gap-2 flex-nowrap">
          <p className="text-xs mr-2 whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
            収集済みログを分析（実行したときだけ走ります）
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

      {/* 分析条件 */}
      <section className="border rounded-lg p-4 mb-6" style={cardStyle}>
        <h2 className="text-sm font-display font-semibold mb-3 tracking-wider" style={{ color: "var(--cyan)" }}>
          分析条件
        </h2>
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-xs" style={{ color: "var(--text-muted)" }}>
            期間（開始）
            <input
              type="datetime-local"
              step={60}
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="block mt-1 px-2 py-1.5 rounded border text-sm w-56 font-mono"
              style={{ borderColor: "var(--chart-grid)", backgroundColor: "var(--bg-primary)", color: "var(--text-primary)" }}
            />
          </label>
          <label className="text-xs" style={{ color: "var(--text-muted)" }}>
            期間（終了）
            <input
              type="datetime-local"
              step={60}
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="block mt-1 px-2 py-1.5 rounded border text-sm w-56 font-mono"
              style={{ borderColor: "var(--chart-grid)", backgroundColor: "var(--bg-primary)", color: "var(--text-primary)" }}
            />
          </label>
          <label className="text-xs" style={{ color: "var(--text-muted)" }}>
            サイト
            <SiteSelect
              sites={logSites?.sites}
              loading={sitesLoading}
              value={siteSelection}
              onChange={setSiteSelection}
              onRefresh={() => mutateSites(fetchHangapLogSites(true))}
            />
          </label>
          <button
            onClick={handleRun}
            disabled={running || starting || !siteReady}
            className="flex items-center gap-2 px-4 py-2 border rounded-lg text-sm transition-all disabled:opacity-40"
            style={btnStyle}
            title={siteReady ? undefined : "対象サイトを選んでください"}
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
        {!siteReady && !sitesError && (
          <p className="text-xs mt-2" style={{ color: "var(--yellow)" }}>
            対象サイトを選ぶと分析を実行できます。全サイトを対象にする場合は「すべてのサイト」を選んでください。
          </p>
        )}

        {/* プリセット。値を入れるだけで、分析は実行しない */}
        <div className="flex flex-wrap items-center gap-2 mt-3">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>プリセット:</span>
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => {
                const now = new Date();
                setTo(toLocalInput(now));
                setFrom(toLocalInput(new Date(now.getTime() - p.hours * 3600_000)));
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
          {dataRange && (
            <span className="ml-2 text-xs font-mono" style={{ color: "var(--text-muted)" }}>
              データ範囲: {dataRange}
            </span>
          )}
        </div>

        <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
          期間はログの時刻表記（タイムゾーンなし）で指定します。両方とも省略すると全データが対象です。
          期間を指定した場合はその期間のサンプルだけで分析するため、期間の先頭で始まる区間は検出されず、
          終了時点でゼロが続く区間は「継続中」になります。
        </p>

        <button
          onClick={() => setShowAdvanced((v) => !v)}
          className="flex items-center gap-1 mt-3 text-xs"
          style={{ color: "var(--cyan)" }}
        >
          {showAdvanced ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          詳細設定
        </button>
        {showAdvanced && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
            {ADVANCED_FIELDS.map((f) => (
              <label key={f.key} className="text-xs" style={{ color: "var(--text-muted)" }}>
                {f.label}
                <input
                  value={advanced[f.key]}
                  onChange={(e) => setAdvanced((v) => ({ ...v, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  inputMode="decimal"
                  className="block mt-1 px-2 py-1.5 rounded border text-sm w-full font-mono"
                  style={{ borderColor: "var(--chart-grid)", backgroundColor: "var(--bg-primary)", color: "var(--text-primary)" }}
                />
              </label>
            ))}
            <p className="col-span-2 sm:col-span-4 text-xs" style={{ color: "var(--text-muted)" }}>
              空欄の項目は既定値（薄字）で分析します。実際に適用された条件は結果の「分析条件」に出ます。
            </p>
          </div>
        )}
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

      {/* 実行中 */}
      {running && (
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
          <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
            分析はサーバ側で実行しています。この画面を離れても中断されません。
          </p>
        </section>
      )}

      {/* 失敗（**「結果0件」と取り違えないこと**） */}
      {job?.status === "failed" && (
        <section
          className="border rounded-lg p-4 mb-6 flex gap-3"
          style={{ borderColor: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }}
        >
          <AlertTriangle className="w-6 h-6 shrink-0" style={{ color: "var(--red)" }} />
          <div>
            <p className="text-base font-semibold" style={{ color: "var(--red)" }}>
              分析は完了していません（検出0件ではありません）
            </p>
            <p className="text-sm mt-2 whitespace-pre-wrap" style={{ color: "var(--text-primary)" }}>
              {job.error ?? "原因が記録されていません。"}
            </p>
            <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
              分析対象のログが存在しない場合もここに来ます。ハングが無かったという意味ではありません。
            </p>
          </div>
        </section>
      )}

      {/* 警告（結果テーブルより上に出す。前提を知らずに数字だけ見ると誤読する） */}
      {job && !viewingSaved && <Warnings warnings={job.warnings} />}

      {/* 保存済み結果を表示中は、分析直後の結果と同時に出さない（取り違えを防ぐ） */}
      {viewingSaved && (
        <SavedResultView row={viewingSaved} onClose={() => setViewingSaved(null)} />
      )}

      {/* 結果 */}
      {!viewingSaved && summary && job && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-4">
            <Stat label="検出区間数" value={summary.detected_intervals} color="var(--cyan)" />
            <Breakdown title="回復状況" counts={summary.recovery_status} />
            <Breakdown title="周辺AP判定" counts={summary.neighbor_verdict} />
            <Stat label="退場疑い" value={summary.exodus_suspected} />
            <Stat label="イベント該当区間" value={summary.event_matched_intervals} />
          </div>

          <div className="mb-4">
            <DataInfo summary={summary} />
          </div>

          <div className="flex flex-wrap items-center gap-3 mb-4">
            <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
              {summary.condition_text}
            </span>
            {job.finished_at && (
              <span className="ml-auto text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                完了 {toLocalString(job.finished_at, timezone)}
              </span>
            )}
          </div>

          {/* 結果テーブル（保存済み結果の再表示と同じコンポーネント） */}
          <HangapResultTable key={job.job_id} source={{ kind: "job", jobId: job.job_id }} />
        </>
      )}

      {!jobId && !startError && !viewingSaved && (
        <div
          className="border rounded-lg py-16 text-center text-sm"
          style={{ ...cardStyle, color: "var(--text-muted)" }}
        >
          「分析を実行」を押すと、収集済みのログからハングAPの候補を検出します。
          過去の結果は下の「保存済みの分析結果」から表示できます。
        </div>
      )}

      <SavedResults
        doneJobId={job?.status === "done" ? job.job_id : null}
        viewingName={viewingSaved?.name ?? null}
        onView={setViewingSaved}
      />
    </main>
  );
}
