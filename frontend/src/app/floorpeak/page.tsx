"use client";

import {
  AlertTriangle, Archive, BarChart3, ChevronDown, Clock, Download, Eye,
  Play, RefreshCw, Trash2, X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  deleteFloorPeakSavedResult, fetchFloorPeakJob, fetchFloorPeakLogSites,
  fetchFloorPeakResult, fetchFloorPeakSavedResults, fetchFloorPeakSavedRows,
  getFloorPeakDownloadUrl, startFloorPeakAnalysis,
  FloorPeakAnalyzeBody, FloorPeakJob, FloorPeakMeta, FloorPeakPhase, FloorPeakResult,
  FloorPeakRow, FloorPeakSavedResult, HangapLogSite, HangapLogSites,
} from "@/lib/api";
import FloorPeakChart from "@/app/components/FloorPeakChart";
import TabNav from "@/app/components/TabNav";
import MaskToggle from "@/app/components/MaskToggle";
import ThemeToggle from "@/app/components/ThemeToggle";
import { toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";

const PHASE_LABELS: Record<FloorPeakPhase, string> = {
  loading: "読み込み中",
  peak: "ピーク判定中",
  floors: "フロア解決中",
  writing: "書き出し中",
};

const POLL_INTERVAL_MS = 2000;
/** ポーリングを続ける上限。これを過ぎたら止めて「状態を再取得」を出す */
const POLL_LIMIT_MS = 15 * 60 * 1000;
/** 画面を離れて戻ってきたときに実行中のジョブを拾うためのキー */
const JOB_STORAGE_KEY = "floorpeak:job_id";

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

/** 「12 分」「1.5 時間」。ずれは必ず人が読める形で出す */
function formatOffset(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分`;
  return `${(seconds / 3600).toFixed(1)} 時間`;
}

const siteLabel = (site: HangapLogSite) => site.site_name || site.site_id;

function siteDetail(site: HangapLogSite): string {
  const range = formatDataRange([site.first, site.last]);
  return `AP ${site.ap_count} 台` + (range ? ` / ${range}` : "");
}

/**
 * 対象サイトの選択。**単一選択・未選択では実行できない。**
 * 「すべてのサイト」は選択肢に置かない（複数サイトを混ぜると
 * 「サイト全体のピーク」が定義できない）。
 */
function SiteSelect({
  sites, loading, value, onChange, onRefresh,
}: {
  sites: HangapLogSite[] | undefined;
  loading: boolean;
  value: string | null;
  onChange: (next: string) => void;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const list = sites ?? [];
  const selected = list.find((s) => s.site_id === value);
  const summary = value === null
    ? "選択してください"
    : selected ? siteLabel(selected) : value;

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
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="absolute z-50 mt-1 w-96 max-h-80 overflow-y-auto p-2 border rounded-lg shadow-lg"
            style={cardStyle}
          >
            <div className="flex items-center gap-2 px-1 pb-2">
              <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                収集済みログに含まれるサイト（1 つだけ選べます）
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

            {list.length === 0 && (
              <p className="px-2 py-3 text-xs" style={{ color: "var(--text-muted)" }}>
                {loading ? "読み込み中..." : "ログにサイトが見つかりません"}
              </p>
            )}

            {list.map((site) => {
              const active = site.site_id === value;
              return (
                <button
                  key={site.site_id}
                  onClick={() => { onChange(site.site_id); setOpen(false); }}
                  className="w-full text-left px-2 py-1.5 mb-1 rounded border text-xs"
                  style={
                    active
                      ? { borderColor: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
                      : { borderColor: "var(--chart-grid)" }
                  }
                >
                  <span className="block truncate" style={{ color: "var(--text-primary)" }}>
                    {siteLabel(site)}
                  </span>
                  <span className="block text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                    {siteDetail(site)}
                  </span>
                </button>
              );
            })}
          </div>
        </>
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

/**
 * 分析条件。**グラフの横に常時出す。**
 * 「いつの・どこの・何の値か」がグラフ単体で分かることがこのパネルの目的なので、
 * 折りたたまない。
 */
function ConditionPanel({ meta }: { meta: FloorPeakMeta }) {
  const window = meta.requested_at
    ? `時点指定 ${meta.requested_at}（期間指定は無視）`
    : `${meta.window_start ?? "(指定なし)"} 〜 ${meta.window_end ?? "(指定なし)"}`;
  const how = meta.selected_by === "auto"
    ? "期間内で合計端末数が最大の時点を自動選択"
    : "指定した時点に最も近い時点";
  const rows: [string, string][] = [
    ["対象サイト", meta.site_label || meta.site_name || meta.site_id],
    ["指定期間", window],
    ["ピーク時刻", `${meta.peak_time ?? "-"}（${how}）`],
    ["実サンプル範囲", `${meta.peak_sample_first ?? "-"} 〜 ${meta.peak_sample_last ?? "-"}`],
    ["サイト合計端末数", `${meta.peak_total_clients} 台`],
    [
      "バケット幅",
      `${meta.bucket_seconds ?? "-"} 秒` + (meta.bucket_seconds_estimated === false ? "（推定できず既定値）" : ""),
    ],
    [
      "フロア名の出典",
      meta.floormap_file
        ? `${meta.floormap_file}（${meta.floormap_timestamp ?? "-"} / ずれ ${formatOffset(meta.floormap_offset_seconds)}）`
        : "なし（すべて未割当）",
    ],
    ["対象AP数 / フロア数", `${meta.ap_count} 台 / ${meta.floor_count} フロア`],
    ...(meta.floor_resolution_notes && meta.floor_resolution_notes.length > 0
      ? ([["フロア解決の補足", meta.floor_resolution_notes.join(" / ")]] as [string, string][])
      : []),
  ];
  return (
    <div className="border rounded-lg p-4" style={cardStyle}>
      <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>分析条件</p>
      <dl className="grid grid-cols-1 gap-y-1.5 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex gap-3">
            <dt className="shrink-0 w-36" style={{ color: "var(--text-muted)" }}>{label}</dt>
            <dd className="font-mono text-xs pt-0.5 break-all" style={{ color: "var(--text-primary)" }}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/**
 * 結果の表示。**分析直後と保存済み結果で同じコンポーネントを使う**
 * （別実装を作らないこと）。フロアの選択とトップ N の切り出しはここで行う
 * （バックエンドはサイトの全 AP 行を返す）。
 */
function ResultView({
  result, source,
}: {
  result: FloorPeakResult;
  source: { kind: "job"; jobId: string } | { kind: "saved"; name: string };
}) {
  const meta = result.meta;
  const floors = meta.floors ?? [];
  const [floor, setFloor] = useState<string>(meta.default_floor ?? floors[0]?.map_name ?? "");

  useEffect(() => {
    setFloor(meta.default_floor ?? floors[0]?.map_name ?? "");
    // 結果が入れ替わったらフロアの選択も作り直す
  }, [meta.default_floor, result.job_id, result.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const topN = meta.top_n ?? 20;
  const shown: FloorPeakRow[] = useMemo(
    () =>
      result.rows
        .filter((r) => r.map_name === floor)
        .sort((a, b) => a.rank_in_floor - b.rank_in_floor)
        .slice(0, topN),
    [result.rows, floor, topN]
  );
  const floorTotal = floors.find((f) => f.map_name === floor);
  const linkStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };

  return (
    <>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
        <ConditionPanel meta={meta} />
        <div className="lg:col-span-2 flex flex-col gap-4">
          <div className="border rounded-lg p-4" style={cardStyle}>
            <div className="flex flex-wrap items-center gap-3">
              <label className="text-xs" style={{ color: "var(--text-muted)" }}>
                フロア
                <select
                  value={floor}
                  onChange={(e) => setFloor(e.target.value)}
                  className="block mt-1 px-2 py-1.5 rounded border text-sm w-64"
                  style={{
                    borderColor: "var(--chart-grid)",
                    backgroundColor: "var(--bg-primary)",
                    color: "var(--text-primary)",
                  }}
                >
                  {floors.map((f) => (
                    <option key={f.map_name} value={f.map_name}>
                      {f.map_name}（AP {f.ap_count} 台 / 端末 {f.num_clients}）
                    </option>
                  ))}
                </select>
              </label>
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                接続端末数トップ {topN}
                {floorTotal && floorTotal.ap_count > topN
                  ? `（このフロアの ${floorTotal.ap_count} 台のうち上位 ${shown.length} 台）`
                  : `（このフロアの全 ${shown.length} 台）`}
              </span>
              <div className="ml-auto flex items-center gap-2">
                {(["xlsx", "csv"] as const).map((format) => (
                  <a
                    key={format}
                    href={getFloorPeakDownloadUrl(source, format, floor)}
                    className="flex items-center gap-1 px-2 py-1 border rounded text-xs"
                    style={linkStyle}
                  >
                    <Download className="w-3.5 h-3.5" />
                    {format}
                  </a>
                ))}
              </div>
            </div>
            <p className="text-[10px] mt-2" style={{ color: "var(--text-muted)" }}>
              xlsx のグラフは選択中のフロア、data シートと csv は全フロアの全 AP が入ります。
            </p>
          </div>

          <FloorPeakChart rows={shown} meta={meta} />
        </div>
      </div>
    </>
  );
}

/** 保存済み結果の表示（過去の結果であることが分かる帯を必ず上に出す） */
function SavedResultView({ row, onClose }: { row: FloorPeakSavedResult; onClose: () => void }) {
  const { timezone } = useTimezone();
  const savedAt = row.saved_at ? toLocalString(row.saved_at, timezone) : row.name;
  const { data, error, isLoading } = useSWR<FloorPeakResult>(
    ["floorpeak-saved-rows", row.name],
    () => fetchFloorPeakSavedRows(row.name)
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
  onView: (row: FloorPeakSavedResult | null) => void;
}) {
  const { timezone } = useTimezone();
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR<FloorPeakSavedResult[]>(
    "floorpeak-saved-results",
    fetchFloorPeakSavedResults
  );

  useEffect(() => {
    if (doneJobId) mutate();
  }, [doneJobId, mutate]);

  const handleDelete = async (row: FloorPeakSavedResult) => {
    const when = row.saved_at ? toLocalString(row.saved_at, timezone) : row.name;
    if (!window.confirm(`${when} の分析結果を削除します。\nxlsx / csv / json をまとめて削除し、元に戻せません。よろしいですか？`)) {
      return;
    }
    setDeleting(row.name);
    setError(null);
    try {
      await deleteFloorPeakSavedResult(row.name);
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
                {["保存日時", "対象サイト", "ピーク時刻", "合計端末数", "フロア数", "警告", "サイズ", ""].map((h) => (
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
                    {row.site_name || row.site_id}
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap font-mono text-xs" style={{ color: "var(--text-secondary)" }}>
                    {row.peak_time ?? "-"}
                    <span className="ml-1" style={{ color: "var(--text-muted)" }}>
                      ({row.selected_by})
                    </span>
                  </td>
                  <td className="py-2.5 px-3 font-mono text-xs" style={{ color: "var(--cyan)" }}>
                    {row.peak_total_clients}
                  </td>
                  <td className="py-2.5 px-3 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>
                    {row.floor_count}
                  </td>
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
                        <a
                          key={format}
                          href={getFloorPeakDownloadUrl({ kind: "saved", name: row.name }, format)}
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

export default function FloorPeakPage() {
  const { timezone } = useTimezone();

  const [site, setSite] = useState<string | null>(null);
  /** "window": 期間からピークを自動選択 / "at": 時点を手動指定 */
  const [mode, setMode] = useState<"window" | "at">("window");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [at, setAt] = useState("");

  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [pollStopped, setPollStopped] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const [viewingSaved, setViewingSaved] = useState<FloorPeakSavedResult | null>(null);

  const {
    data: logSites, isLoading: sitesLoading, error: sitesError, mutate: mutateSites,
  } = useSWR<HangapLogSites>("floorpeak-log-sites", () => fetchFloorPeakLogSites());

  // 選択肢が 1 つしかない環境では、利用者が意識せずに済むよう自動で選択済みにする
  useEffect(() => {
    if (site !== null || !logSites) return;
    if (logSites.sites.length === 1) setSite(logSites.sites[0].site_id);
  }, [logSites, site]);

  useEffect(() => {
    const saved = window.localStorage.getItem(JOB_STORAGE_KEY);
    if (saved) setJobId(saved);
  }, []);

  /**
   * ジョブの監視。**SWR の refreshInterval は使わない**
   * （毎秒再レンダーする画面ではポーリング用タイマーが張り直され続けて発火しない）。
   */
  const [job, setJob] = useState<FloorPeakJob | null | undefined>(undefined);
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
        const state = await fetchFloorPeakJob(jobId);
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
  const { data: result } = useSWR<FloorPeakResult>(
    doneJobId ? ["floorpeak-result", doneJobId] : null,
    () => fetchFloorPeakResult(doneJobId as string)
  );

  const handleRun = async () => {
    setStarting(true);
    setStartError(null);
    setConflict(null);
    setViewingSaved(null);  // 新しい分析を始めたら保存済み結果の表示はやめる
    try {
      if (!site) return;
      const body: FloorPeakAnalyzeBody = { site };
      // datetime-local の値（"2026-08-15T20:00"）は API がそのまま受け付ける
      // ISO8601（TZ なし）。**変換を挟まないこと。** ログの時刻は naive なので、
      // UTC などへ直すと窓がずれる。
      if (mode === "at") {
        if (at.trim()) body.at = at.trim();
      } else {
        if (from.trim()) body.from = from.trim();
        if (to.trim()) body.to = to.trim();
      }
      const started = await startFloorPeakAnalysis(body);
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
  /** 対象サイトを選ぶまで分析は実行できない（単一指定が必須） */
  const siteReady = site !== null;

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5" style={{ color: "var(--cyan)" }} />
          <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
            Floor Peak
          </h1>
        </div>
        <div className="flex items-center gap-2 flex-nowrap">
          <p className="text-xs mr-2 whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
            収集済みログからピーク時点を分析（実行したときだけ走ります）
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
            サイト（必須・1 つ）
            <SiteSelect
              sites={logSites?.sites}
              loading={sitesLoading}
              value={site}
              onChange={setSite}
              onRefresh={() => mutateSites(fetchFloorPeakLogSites(true))}
            />
          </label>

          <div className="text-xs" style={{ color: "var(--text-muted)" }}>
            選び方
            <div className="flex gap-1 mt-1">
              {([
                ["window", "期間からピークを選ぶ"],
                ["at", "時点を指定する"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setMode(value)}
                  className="px-3 py-1.5 rounded border text-xs"
                  style={
                    mode === value
                      ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
                      : { borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {mode === "window" ? (
            <>
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
            </>
          ) : (
            <label className="text-xs" style={{ color: "var(--text-muted)" }}>
              時点
              <input
                type="datetime-local" step={60} value={at}
                onChange={(e) => setAt(e.target.value)}
                className="block mt-1 px-2 py-1.5 rounded border text-sm w-56 font-mono"
                style={inputStyle}
              />
            </label>
          )}

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
            対象サイトを 1 つ選ぶと分析を実行できます（サイト全体のピークは複数サイトでは定義できません）。
          </p>
        )}

        {mode === "window" && (
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
        )}

        <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
          {mode === "window"
            ? "期間はログの時刻表記（タイムゾーンなし）で指定します。半開区間 [開始, 終了) で、終了時刻ちょうどのサンプルは含みません。両方とも省略すると全データが対象です。"
            : "指定した時点に最も近いサンプル（バケット）を選びます。期間の指定は無視されます。ずれが大きいときは警告に出ます。"}
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
              分析は完了していません（ピークが無かったのではありません）
            </p>
            <p className="text-sm mt-2 whitespace-pre-wrap" style={{ color: "var(--text-primary)" }}>
              {job.error ?? "原因が記録されていません。"}
            </p>
            <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
              指定したサイトや期間にログが無い場合もここに来ます。
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
          サイトと期間を選んで「分析を実行」を押すと、その期間で最も混雑した時点の
          フロア別 AP 接続端末数を表示します。過去の結果は下の「保存済みの分析結果」から表示できます。
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
