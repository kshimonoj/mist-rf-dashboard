"use client";

/**
 * 仮名化の復元（再識別）。
 *
 * 加工・統合したあとのファイルは利用者の手元にあるので、復元はアップロードで行う。
 * サーバは一時ディレクトリで処理し、処理後に削除する（`data/` には保存しない）。
 *
 * **復元後のファイルは実名を含む。** 導線のすぐ隣に必ずその注意書きを出す。
 */

import { AlertTriangle, RotateCcw, Upload, X } from "lucide-react";
import { useRef, useState } from "react";
import useSWR from "swr";
import {
  fetchPseudonymizeLimits,
  restorePseudonymized,
  RESTORE_COUNT_LABELS,
  RESTORE_LIMITS_NOTICE,
  RESTORE_MAX_UPLOAD_BYTES,
  RESTORE_NOTICE,
  RESTORE_RESIDUAL_LABELS,
  RestoreReport,
} from "@/lib/api";

const DEFAULT_EXTENSIONS = [".csv", ".tsv", ".json", ".txt", ".md", ".log", ".xlsx"];

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function RestorePanel() {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [noTime, setNoTime] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<RestoreReport | null>(null);
  const [savedName, setSavedName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const { data: limits } = useSWR("pseudonymize-limits", fetchPseudonymizeLimits);
  const maxBytes = limits?.restore_max_upload_bytes ?? RESTORE_MAX_UPLOAD_BYTES;
  const maxFiles = limits?.restore_max_files ?? 50;
  const extensions = limits?.restore_extensions ?? DEFAULT_EXTENSIONS;

  const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
  const tooBig = totalBytes > maxBytes;
  const tooMany = files.length > maxFiles;
  const canSubmit = files.length > 0 && !tooBig && !tooMany && !busy;

  const pickFiles = (list: FileList | null) => {
    setFiles(list ? Array.from(list) : []);
    setReport(null);
    setSavedName(null);
    setError(null);
  };

  const clearFiles = () => {
    if (inputRef.current) inputRef.current.value = "";
    pickFiles(null);
  };

  const handleRestore = async () => {
    setBusy(true);
    setError(null);
    setReport(null);
    setSavedName(null);
    try {
      const result = await restorePseudonymized(files, noTime);
      setReport(result.report);
      setSavedName(result.filename);
    } catch (e) {
      setError(e instanceof Error ? e.message : "復元に失敗しました");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="border rounded-lg mb-4"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-2.5 text-sm font-mono"
        style={{ color: "var(--yellow)" }}
      >
        <RotateCcw className="w-4 h-4" />
        仮名化を復元
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          （加工後のファイルを元の値に戻す）
        </span>
        <span className="ml-auto text-xs" style={{ color: "var(--text-muted)" }}>
          {open ? "▲" : "▼"}
        </span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          {/* 実名が戻ることを、操作するボタンと同じ視界に置く */}
          <p
            className="text-xs leading-relaxed px-3 py-2 rounded border"
            style={{
              color: "var(--yellow)",
              borderColor: "var(--yellow)",
              backgroundColor: "rgba(255,204,0,0.08)",
            }}
          >
            <AlertTriangle className="w-3.5 h-3.5 inline-block mr-1 -mt-0.5" />
            {RESTORE_NOTICE}
          </p>

          <div className="flex items-center gap-3 flex-wrap">
            <input
              ref={inputRef}
              type="file"
              multiple
              accept={extensions.join(",")}
              onChange={(e) => pickFiles(e.target.files)}
              className="text-xs font-mono"
              style={{ color: "var(--text-secondary)" }}
            />
            {files.length > 0 && (
              <button
                onClick={clearFiles}
                className="flex items-center gap-1 text-xs px-2 py-1 border rounded"
                style={{ borderColor: "var(--chart-grid)", color: "var(--text-muted)" }}
              >
                <X className="w-3 h-3" />
                クリア
              </button>
            )}
          </div>

          {files.length > 0 && (
            <p className="text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
              {files.length} 件 / {formatSize(totalBytes)}
              {tooBig && (
                <span style={{ color: "var(--red)" }}>
                  {" "}アップロードできるのは合計 {formatSize(maxBytes)} までです。
                </span>
              )}
              {tooMany && (
                <span style={{ color: "var(--red)" }}>
                  {" "}一度に復元できるのは {maxFiles} 件までです。
                </span>
              )}
            </p>
          )}

          <div className="flex items-center gap-4 flex-wrap">
            <label
              className="flex items-center gap-1.5 text-xs font-mono cursor-pointer"
              style={{ color: "var(--text-secondary)" }}
            >
              <input
                type="checkbox"
                checked={noTime}
                onChange={(e) => setNoTime(e.target.checked)}
              />
              時刻は戻さない（識別子だけ戻す）
            </label>
            <button
              onClick={handleRestore}
              disabled={!canSubmit}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-sm font-mono transition-all disabled:opacity-40"
              style={{
                borderColor: "var(--yellow)",
                color: "var(--yellow)",
                backgroundColor: "rgba(255,204,0,0.08)",
              }}
            >
              <Upload className={`w-4 h-4 ${busy ? "animate-pulse" : ""}`} />
              {busy ? "復元中..." : "復元してダウンロード"}
            </button>
          </div>

          <p className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
            {RESTORE_LIMITS_NOTICE}
            {" "}アップロードしたファイルはサーバに保存されません（一時ディレクトリで処理して削除します）。
            {" "}対応形式: {extensions.join(" / ")}
          </p>

          {error && (
            <p
              className="text-xs font-mono whitespace-pre-wrap"
              style={{ color: "var(--red)" }}
            >
              {error}
            </p>
          )}

          {report && <RestoreReportView report={report} savedName={savedName} />}
        </div>
      )}
    </div>
  );
}

/** 復元レポート。「戻ったつもりで戻っていない」を防ぐため、件数と残存を必ず出す。 */
function RestoreReportView({
  report,
  savedName,
}: {
  report: RestoreReport;
  savedName: string | null;
}) {
  const entries = Object.entries(report.counts).sort((a, b) => b[1] - a[1]);
  return (
    <div
      className="border rounded px-3 py-2 space-y-2"
      style={{ borderColor: "var(--chart-grid)" }}
    >
      {savedName && (
        <p className="text-xs font-mono" style={{ color: "var(--green)" }}>
          {savedName} をダウンロードしました
        </p>
      )}
      <div>
        <p className="text-xs font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
          置換件数
        </p>
        {entries.length === 0 ? (
          <p className="text-xs font-mono" style={{ color: "var(--yellow)" }}>
            置換が 1 件もありませんでした。仮名化に使ったソルトと違う可能性があります。
          </p>
        ) : (
          <ul className="text-xs font-mono grid grid-cols-2 sm:grid-cols-3 gap-x-4">
            {entries.map(([key, count]) => (
              <li key={key} style={{ color: "var(--text-secondary)" }}>
                {RESTORE_COUNT_LABELS[key] ?? key}: {count}
              </li>
            ))}
          </ul>
        )}
      </div>

      {report.residual_total > 0 && (
        <div>
          <p className="text-xs font-mono" style={{ color: "var(--yellow)" }}>
            <AlertTriangle className="w-3.5 h-3.5 inline-block mr-1 -mt-0.5" />
            マッピングに無い仮名らしき文字列が {report.residual_total} 件残っています。
            マッピングが古いか、別環境のソルトで仮名化されたファイルの可能性があります。
          </p>
          <ul className="text-xs font-mono mt-1 space-y-0.5">
            {report.files.flatMap((file) =>
              file.residuals.map((group, i) => (
                <li key={`${file.filename}-${i}`} style={{ color: "var(--text-muted)" }}>
                  {file.filename}: {RESTORE_RESIDUAL_LABELS[group.kind] ?? group.kind}{" "}
                  {group.count} 件
                  {group.sheet && `（シート ${group.sheet}）`}
                  {group.column && `（列 ${group.column}）`}
                  {group.rows.length > 0 && `（行 ${group.rows.join(", ")}）`}
                </li>
              )),
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
