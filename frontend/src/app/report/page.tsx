"use client";

import {
  AlertTriangle, Download, FileText, Play, RefreshCw, Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  deleteReportJob, fetchFloorPeakSavedResults, fetchHangapSavedResults, fetchReportJob,
  fetchReportResult, fetchRrmSavedResults, getReportDownloadUrl, startReportGeneration,
  FloorPeakSavedResult, HangapSavedResult, ReportGenerateBody, ReportJob, ReportPhase,
  ReportResult, ReportSection, RrmSavedResult, REPORT_SECTION_ORDER,
} from "@/lib/api";
import DownloadLink from "@/app/components/DownloadLink";
import MaskToggle from "@/app/components/MaskToggle";
import TabNav from "@/app/components/TabNav";
import ThemeToggle from "@/app/components/ThemeToggle";
import { toLocalString } from "@/lib/time";
import { useMask, useTimezone } from "@/app/providers";

const PHASE_LABELS: Record<ReportPhase, string> = {
  loading: "保存済み結果の読み込み中",
  building: "スライドの組み立て中",
  writing: "書き出し中",
};

const POLL_INTERVAL_MS = 1000;
/** ポーリングを続ける上限。これを過ぎたら止めて「状態を再取得」を出す */
const POLL_LIMIT_MS = 5 * 60 * 1000;

/**
 * マスク ON 中はこのページ全体を無効化する理由。
 *
 * 29 番の「ダウンロード一律無効化」と同じ考え方。レポートは**サーバ側で実データ
 * （AP名・サイト名・フロア名）からその場で組み立てる**ので、画面のマスク（表示層
 * だけの置き換え）は一切効かない。ボタンだけ止めて選択肢に実名を出し続けると
 * 「マスク中なのに実名が見える」状態が残るため、選択肢ごと隠す。
 */
const MASK_NOTICE =
  "マスク表示中はレポートを作成できません。レポートはサーバ側で実データから組み立てるため、" +
  "画面のマスクが効きません（保存済み結果の一覧も実名になるためここでは表示しません）。" +
  "作成する場合は上部バナーの「解除」でマスクを解除してください。";

const cardStyle = { borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" };

/** 一覧の 1 行に必要な最小限。3 モジュールの保存済み結果に共通する形だけを使う */
interface SavedOption {
  name: string;
  saved_at: string | null;
  detail: string;
}

interface SectionSpec {
  section: ReportSection;
  label: string;
  description: string;
  field: keyof ReportGenerateBody;
}

/** 章の並びは API と同じ固定順（選んだ順序では変わらない） */
const SECTIONS: SectionSpec[] = [
  {
    section: "hangap",
    label: "Hang AP",
    description: "接続端末数がゼロのまま戻らない区間",
    field: "hangap_result",
  },
  {
    section: "floorpeak",
    label: "Floor Peak",
    description: "サイトのピーク時点のフロア別 AP 分布",
    field: "floorpeak_result",
  },
  {
    section: "rrm",
    label: "RRM",
    description: "RRM / RADAR によるチャネル変更",
    field: "rrm_result",
  },
];

function formatCount(value: number | undefined, unit: string): string {
  return `${value ?? 0} ${unit}`;
}

function hangapOption(row: HangapSavedResult): SavedOption {
  return {
    name: row.name,
    saved_at: row.saved_at,
    detail: `検知 ${formatCount(row.detected_intervals, "件")} / AP ${formatCount(row.ap_count, "台")}`,
  };
}

function floorPeakOption(row: FloorPeakSavedResult): SavedOption {
  return {
    name: row.name,
    saved_at: row.saved_at,
    detail:
      `${row.site_label || row.site_name || "-"} / ピーク ${row.peak_time ?? "-"}` +
      ` / ${formatCount(row.floor_count, "フロア")}`,
  };
}

function rrmOption(row: RrmSavedResult): SavedOption {
  return {
    name: row.name,
    saved_at: row.saved_at,
    detail:
      `変更 ${formatCount(row.change_count, "件")} / no-op ${formatCount(row.noop_count, "件")}` +
      ` / レーダー ${formatCount(row.radar_detected, "件")}`,
  };
}

/**
 * 1 モジュールぶんのセレクタ。**1 件だけ選べる（複数選択不可）**。
 * 「選ばない」も選択肢に含める（その章はレポートに入らない）。
 */
function ResultSelect({
  spec, options, loading, error, value, onChange, disabled,
}: {
  spec: SectionSpec;
  options: SavedOption[] | undefined;
  loading: boolean;
  error: unknown;
  value: string | null;
  onChange: (name: string | null) => void;
  disabled: boolean;
}) {
  const { timezone } = useTimezone();
  const list = options ?? [];
  const name = `report-${spec.section}`;

  const rowStyle = (selected: boolean) => ({
    borderColor: selected ? "var(--cyan)" : "var(--chart-grid)",
    backgroundColor: selected ? "rgba(0,212,255,0.08)" : undefined,
  });

  return (
    <fieldset
      className={`border rounded-lg p-4 flex-1 min-w-[19rem] ${disabled ? "opacity-40" : ""}`}
      style={cardStyle}
      disabled={disabled}
    >
      <legend className="px-1 text-sm font-display font-semibold tracking-wider" style={{ color: "var(--cyan)" }}>
        {spec.label}
      </legend>
      <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
        {spec.description}（1 件だけ選べます）
      </p>

      {error != null ? (
        <p className="text-xs mb-2" style={{ color: "var(--red)" }}>
          保存済み結果を取得できませんでした（{error instanceof Error ? error.message : String(error)}）
        </p>
      ) : null}

      <div className="flex flex-col gap-1.5 max-h-64 overflow-y-auto pr-1">
        <label
          className="flex items-start gap-2 border rounded px-2.5 py-2 cursor-pointer"
          style={rowStyle(value === null)}
        >
          <input
            type="radio" name={name} className="mt-0.5"
            checked={value === null}
            onChange={() => onChange(null)}
          />
          <span className="text-sm" style={{ color: "var(--text-secondary)" }}>
            選ばない（この章を作らない）
          </span>
        </label>

        {list.map((option) => (
          <label
            key={option.name}
            className="flex items-start gap-2 border rounded px-2.5 py-2 cursor-pointer"
            style={rowStyle(value === option.name)}
          >
            <input
              type="radio" name={name} className="mt-0.5"
              checked={value === option.name}
              onChange={() => onChange(option.name)}
            />
            <span className="min-w-0">
              <span className="block text-sm font-mono" style={{ color: "var(--text-primary)" }}>
                {option.saved_at ? toLocalString(option.saved_at, timezone) : option.name}
              </span>
              <span className="block text-xs" style={{ color: "var(--text-muted)" }}>
                {option.detail}
              </span>
            </span>
          </label>
        ))}

        {list.length === 0 && (
          <p className="text-xs py-2" style={{ color: "var(--text-muted)" }}>
            {loading ? "読み込み中..." : "保存済みの分析結果はありません"}
          </p>
        )}
      </div>
    </fieldset>
  );
}

function ResultView({ result, jobId }: { result: ReportResult; jobId: string }) {
  const { timezone } = useTimezone();
  return (
    <section className="border rounded-lg p-4 mb-6" style={cardStyle}>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <FileText className="w-4 h-4" style={{ color: "var(--cyan)" }} />
        <h2 className="text-sm font-display font-semibold tracking-wider" style={{ color: "var(--cyan)" }}>
          レポートができました
        </h2>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {result.slide_count} スライド
          {result.generated_at && ` / 生成 ${toLocalString(result.generated_at, timezone)}`}
        </span>
        <DownloadLink
          href={getReportDownloadUrl(jobId)}
          className="ml-auto flex items-center gap-1.5 px-3 py-2 border rounded-lg text-sm"
          style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
        >
          <Download className="w-4 h-4" />
          {result.filename}
        </DownloadLink>
      </div>

      <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
        レポートは保存されません。必要なファイルはこの場でダウンロードしてください
        （しばらく経つとサーバから消えます）。
      </p>

      <ol className="text-sm" style={{ color: "var(--text-secondary)" }}>
        {result.slides.map((slide, index) => (
          <li key={`${slide.section}-${index}`} className="py-0.5 flex gap-3">
            <span className="font-mono w-8 text-right shrink-0" style={{ color: "var(--text-muted)" }}>
              {index + 1}
            </span>
            <span>{slide.title}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function ReportPage() {
  const { timezone } = useTimezone();
  const { masked } = useMask();

  const [selection, setSelection] = useState<Record<ReportSection, string | null>>({
    hangap: null, floorpeak: null, rrm: null,
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [pollStopped, setPollStopped] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // マスク ON 中は保存済み結果を取りに行かない（一覧に実名が並ぶため。MASK_NOTICE 参照）
  const hangap = useSWR<HangapSavedResult[]>(
    masked ? null : "report-hangap-results", fetchHangapSavedResults
  );
  const floorpeak = useSWR<FloorPeakSavedResult[]>(
    masked ? null : "report-floorpeak-results", fetchFloorPeakSavedResults
  );
  const rrm = useSWR<RrmSavedResult[]>(
    masked ? null : "report-rrm-results", fetchRrmSavedResults
  );

  const options = useMemo(
    () => ({
      hangap: hangap.data?.map(hangapOption),
      floorpeak: floorpeak.data?.map(floorPeakOption),
      rrm: rrm.data?.map(rrmOption),
    }),
    [hangap.data, floorpeak.data, rrm.data]
  );
  const sources = { hangap, floorpeak, rrm } as const;

  /**
   * ジョブの監視。RRM / Floor Peak と同じ形（**SWR の refreshInterval は使わない**。
   * 毎秒再レンダーする画面ではポーリング用タイマーが張り直され続けて発火しない）。
   */
  const [job, setJob] = useState<ReportJob | null | undefined>(undefined);
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
        const state = await fetchReportJob(jobId);
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
    if (jobId && job === null) setJobId(null);
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
  const { data: result } = useSWR<ReportResult>(
    doneJobId ? ["report-result", doneJobId] : null,
    () => fetchReportResult(doneJobId as string)
  );

  const chosen = REPORT_SECTION_ORDER.filter((section) => selection[section] !== null);
  const running = job?.status === "running";
  const canRun = chosen.length > 0 && !masked && !running && !starting;

  const handleRun = async () => {
    setStarting(true);
    setStartError(null);
    setConflict(null);
    try {
      const body: ReportGenerateBody = {};
      for (const spec of SECTIONS) {
        const name = selection[spec.section];
        if (name) body[spec.field] = name;
      }
      const started = await startReportGeneration(body);
      if (started.conflict) setConflict(started.message ?? "別のレポート生成が実行中です。");
      if (started.job_id) {
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

  const handleDiscard = async () => {
    if (!jobId) return;
    try {
      await deleteReportJob(jobId);
    } catch {
      /* すでに消えていても画面からは外す */
    }
    setJobId(null);
    setJob(undefined);
  };

  const btnStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2">
          <FileText className="w-5 h-5" style={{ color: "var(--cyan)" }} />
          <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
            Report
          </h1>
        </div>
        <div className="flex items-center gap-2 flex-nowrap">
          <p className="text-xs mr-2 whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
            保存済みの分析結果を選んで 1 つの PPTX にまとめます（再分析はしません）
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

      {masked && (
        <div
          className="border rounded-lg p-4 mb-6 flex gap-3"
          style={{ borderColor: "var(--yellow)", backgroundColor: "rgba(255,215,0,0.08)" }}
        >
          <AlertTriangle className="w-5 h-5 shrink-0" style={{ color: "var(--yellow)" }} />
          <p className="text-sm" style={{ color: "var(--text-primary)" }}>{MASK_NOTICE}</p>
        </div>
      )}

      <section className="mb-6">
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-sm font-display font-semibold tracking-wider" style={{ color: "var(--cyan)" }}>
            レポートに含める分析結果
          </h2>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            章は Hang AP → Floor Peak → RRM の順に並びます（選んだ順序では変わりません）
          </span>
        </div>

        <div className="flex flex-wrap gap-4">
          {SECTIONS.map((spec) => (
            <ResultSelect
              key={spec.section}
              spec={spec}
              options={masked ? [] : options[spec.section]}
              loading={sources[spec.section].isLoading}
              error={masked ? null : sources[spec.section].error}
              value={selection[spec.section]}
              onChange={(name) =>
                setSelection((prev) => ({ ...prev, [spec.section]: name }))
              }
              disabled={masked}
            />
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-3 mt-4">
          <button
            onClick={handleRun}
            disabled={!canRun}
            title={masked ? MASK_NOTICE : chosen.length === 0 ? "少なくとも 1 つ選んでください" : undefined}
            className="flex items-center gap-2 px-4 py-2 border rounded-lg text-sm transition-all disabled:opacity-40"
            style={btnStyle}
          >
            <Play className="w-4 h-4" />
            {running ? "作成中..." : "レポートを作成"}
          </button>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            {chosen.length === 0
              ? "1 つも選ばれていません（少なくとも 1 つ選んでください）"
              : `選択中: ${chosen.map((s) => SECTIONS.find((x) => x.section === s)?.label).join(" → ")}`}
          </span>
          {jobId && (
            <button
              onClick={handleDiscard}
              className="flex items-center gap-1.5 px-3 py-2 border rounded-lg text-sm ml-auto"
              style={{ borderColor: "var(--red)", color: "var(--red)" }}
            >
              <Trash2 className="w-3.5 h-3.5" />
              破棄
            </button>
          )}
        </div>
      </section>

      {startError && (
        <div
          className="border rounded-lg p-4 mb-6 flex gap-3"
          style={{ borderColor: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }}
        >
          <AlertTriangle className="w-5 h-5 shrink-0" style={{ color: "var(--red)" }} />
          <div>
            <p className="text-sm font-semibold" style={{ color: "var(--red)" }}>レポートを作成できませんでした</p>
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
          <p className="text-sm" style={{ color: "var(--text-primary)" }}>{conflict}</p>
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
              レポートは作成されていません
            </p>
            <p className="text-sm mt-2 whitespace-pre-wrap" style={{ color: "var(--text-primary)" }}>
              {job.error ?? "原因が記録されていません。"}
            </p>
            <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
              選んだ分析結果が削除されている場合（ローテートで消えた場合を含む）もここに来ます。
            </p>
          </div>
        </section>
      )}

      {result && doneJobId && <ResultView result={result} jobId={doneJobId} />}

      {!jobId && !startError && (
        <div
          className="border rounded-lg py-16 text-center text-sm"
          style={{ ...cardStyle, color: "var(--text-muted)" }}
        >
          各モジュールから 1 件ずつ（不要な章は「選ばない」）選び、「レポートを作成」を押すと
          PPTX を組み立てます。グラフはサーバ側で描き直すので、この画面の見た目とは異なります。
        </div>
      )}
    </main>
  );
}
