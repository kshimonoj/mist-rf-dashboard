"use client";

import {
  AlertTriangle, Archive, ArrowUpDown, ChevronDown, ChevronRight,
  Download, Play, RefreshCw, Trash2, WifiOff,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  deleteHangapSavedResult, fetchHangapJob, fetchHangapResult, fetchHangapSavedResults,
  getHangapDownloadUrl, getHangapSavedDownloadUrl, startHangapAnalysis,
  HangapAnalyzeBody, HangapCell, HangapJob, HangapPhase, HangapResultPage,
  HangapSavedResult, HangapSummary,
} from "@/lib/api";
import ThemeToggle from "@/app/components/ThemeToggle";
import TabNav from "@/app/components/TabNav";
import { toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";

const PHASE_LABELS: Record<HangapPhase, string> = {
  loading: "読み込み中",
  neighbors: "近傍判定中",
  detecting: "検出中",
  writing: "書き出し中",
};

/**
 * 既定で表示する列。結果は 30 列あり、全部横に並べると読めない。
 * 表示順は API が返す columns（= detector.RESULT_COLUMNS）の順をそのまま使うので、
 * ここは「出すかどうか」だけを決める。ダウンロードは常に全 30 列。
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
const PAGE_SIZE = 100;
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

function fmtCell(value: HangapCell): string {
  if (value === null || value === undefined) return "-";
  return String(value);
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

/**
 * 保存済みの分析結果。分析が done で完了すると**サーバ側で自動保存**される
 * （保存ボタンは無い）。ここは一覧・ダウンロード・削除だけを持ち、
 * **結果テーブルの再表示はしない**（ダウンロードで足りる）。
 */
function SavedResults({ doneJobId }: { doneJobId: string | null }) {
  const { timezone } = useTimezone();
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR<HangapSavedResult[]>(
    "hangap-saved-results",
    fetchHangapSavedResults
  );

  // 分析が完了したら一覧に出るはずなので取り直す
  useEffect(() => {
    if (doneJobId) mutate();
  }, [doneJobId, mutate]);

  const handleDelete = async (row: HangapSavedResult) => {
    const when = row.saved_at ? toLocalString(row.saved_at, timezone) : row.name;
    if (!window.confirm(`${when} の分析結果を削除します。\nxlsx / csv / json をまとめて削除し、元に戻せません。よろしいですか？`)) {
      return;
    }
    setDeleting(row.name);
    setError(null);
    try {
      await deleteHangapSavedResult(row.name);
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
          分析が完了すると自動で保存されます（古いものから順に整理されます）
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

      {error && (
        <p className="text-sm mb-3" style={{ color: "var(--red)" }}>{error}</p>
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
                <tr key={row.name} className="border-b" style={{ borderColor: "var(--chart-grid)" }}>
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
                    <div className="flex items-center justify-end gap-2">
                      {(["xlsx", "csv"] as const).map((format) => (
                        <a
                          key={format}
                          href={getHangapSavedDownloadUrl(row.name, format)}
                          className={`flex items-center gap-1 px-2 py-1 border rounded text-xs ${row.files[format] === undefined ? "pointer-events-none opacity-40" : ""}`}
                          style={linkStyle}
                        >
                          <Download className="w-3.5 h-3.5" />
                          {format}
                        </a>
                      ))}
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

  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [advanced, setAdvanced] = useState<Record<AdvancedKey, string>>(EMPTY_ADVANCED);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [pollStopped, setPollStopped] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [sort, setSort] = useState<string | null>(null);
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [offset, setOffset] = useState(0);
  const [showAllColumns, setShowAllColumns] = useState(false);

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
    setOffset(0);
    setStatusFilter(null);
    setSort(null);
    setOrder("asc");
  };

  const handleRun = async () => {
    setStarting(true);
    setStartError(null);
    setConflict(null);
    try {
      const body: HangapAnalyzeBody = {};
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

  const { data: page, isLoading: resultLoading } = useSWR<HangapResultPage>(
    job?.status === "done"
      ? ["hangap-result", job.job_id, offset, statusFilter, sort, order]
      : null,
    () =>
      fetchHangapResult(job!.job_id, {
        offset,
        limit: PAGE_SIZE,
        status: statusFilter ?? undefined,
        sort: sort ?? undefined,
        order,
      }),
    { keepPreviousData: true }
  );

  const columns = useMemo(() => {
    const all = page?.columns ?? [];
    return showAllColumns ? all : all.filter((c) => DEFAULT_COLUMNS.has(c));
  }, [page?.columns, showAllColumns]);

  const toggleSort = (column: string) => {
    if (sort === column) {
      setOrder((v) => (v === "asc" ? "desc" : "asc"));
    } else {
      setSort(column);
      setOrder("asc");
    }
    setOffset(0);
  };

  const running = job?.status === "running";
  const btnStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };

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
      {job && job.warnings.length > 0 && (
        <section
          className="border rounded-lg p-4 mb-6"
          style={{ borderColor: "var(--yellow)", backgroundColor: "rgba(255,215,0,0.08)" }}
        >
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-5 h-5" style={{ color: "var(--yellow)" }} />
            <p className="text-sm font-semibold" style={{ color: "var(--yellow)" }}>
              警告 {job.warnings.length} 件（結果を共有する前に必ず読むこと）
            </p>
          </div>
          <ul className="space-y-1.5">
            {job.warnings.map((w) => (
              <li key={w} className="text-sm flex gap-2" style={{ color: "var(--text-primary)" }}>
                <span style={{ color: "var(--yellow)" }}>⚠</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 結果 */}
      {summary && job && (
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
          </div>

          <div className="flex flex-wrap items-center gap-2 mb-4">
            <a
              href={getHangapDownloadUrl(job.job_id, "xlsx")}
              className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm"
              style={btnStyle}
            >
              <Download className="w-4 h-4" />
              xlsx ダウンロード
            </a>
            <a
              href={getHangapDownloadUrl(job.job_id, "csv")}
              className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm"
              style={btnStyle}
            >
              <Download className="w-4 h-4" />
              csv ダウンロード
            </a>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              ダウンロードは常に全 {page?.columns.length ?? 30} 列
            </span>
            {job.finished_at && (
              <span className="ml-auto text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                完了 {toLocalString(job.finished_at, timezone)}
              </span>
            )}
          </div>

          {/* フィルタ・列表示 */}
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>回復状況:</span>
            {[null, ...Object.keys(summary.recovery_status)].map((s) => (
              <button
                key={s ?? "all"}
                onClick={() => { setStatusFilter(s); setOffset(0); }}
                className="px-3 py-1 rounded border text-xs transition-all"
                style={
                  statusFilter === s
                    ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
                    : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
                }
              >
                {s ?? "すべて"}
              </button>
            ))}
            <label className="ml-4 flex items-center gap-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
              <input
                type="checkbox"
                checked={showAllColumns}
                onChange={(e) => setShowAllColumns(e.target.checked)}
              />
              全列を表示
            </label>
            <span className="ml-auto text-xs" style={{ color: "var(--text-muted)" }}>
              {page ? `${page.total} 件中 ${page.total === 0 ? 0 : offset + 1}–${Math.min(offset + PAGE_SIZE, page.total)} 件` : ""}
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

          {resultLoading && !page ? (
            <div className="flex justify-center py-16">
              <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading result...</div>
            </div>
          ) : !page || page.total === 0 ? (
            <div
              className="border rounded-lg py-16 text-center text-sm"
              style={{ ...cardStyle, color: "var(--text-secondary)" }}
            >
              {statusFilter ? `「${statusFilter}」の区間はありません` : "検出された区間はありません（0 件）"}
            </div>
          ) : (
            <div className="border rounded-lg overflow-x-auto" style={cardStyle}>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left" style={{ borderColor: "var(--chart-grid)" }}>
                    {columns.map((col) => (
                      <th
                        key={col}
                        className="py-3 px-3 font-normal cursor-pointer select-none whitespace-nowrap"
                        style={{ color: "var(--text-muted)" }}
                        onClick={() => toggleSort(col)}
                      >
                        <span className="inline-flex items-center gap-1">
                          {col}
                          <ArrowUpDown
                            className="w-3 h-3"
                            style={{ color: sort === col ? "var(--cyan)" : "var(--text-muted)" }}
                          />
                        </span>
                      </th>
                    ))}
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
      )}

      {!jobId && !startError && (
        <div
          className="border rounded-lg py-16 text-center text-sm"
          style={{ ...cardStyle, color: "var(--text-muted)" }}
        >
          「分析を実行」を押すと、収集済みのログからハングAPの候補を検出します。
        </div>
      )}

      <SavedResults doneJobId={job?.status === "done" ? job.job_id : null} />
    </main>
  );
}
