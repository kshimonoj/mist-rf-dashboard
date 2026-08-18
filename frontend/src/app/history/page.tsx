"use client";

import { Download, RefreshCw, Trash2, FileDown, Camera, Eye, Map, History, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  fetchSnapshots, fetchLogs, fetchSites, fetchSiteAps, fetchSnapshotDbs, fetchClientList,
  getSnapshotDownloadUrl, getSnapshotDbDownloadUrl, getLogDownloadUrl, getLogFilteredDownloadUrl,
  getLogsZipUrl, deleteLogs, backfillApEvents,
  downloadPseudonymized, getPseudonymizedLogsUrl,
  PSEUDONYMIZE_MAX_FILES, PSEUDONYMIZE_NOTICE,
  SnapshotInfo, LogFileInfo, SiteInfo, ApInfo, SnapshotDbMeta, ClientListItem,
} from "@/lib/api";
import ThemeToggle from "@/app/components/ThemeToggle";
import SaveNowButton from "@/app/components/SaveNowButton";
import TabNav from "@/app/components/TabNav";
import RestorePanel from "@/app/components/RestorePanel";
import { toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";

type TriggerFilter = "all" | "manual" | "auto" | "manual-backfill";
type TypeFilter = "all" | "ap_metrics" | "floormap" | "sle_metrics" | "client_metrics" | "ap_events";
type FileType = "ap_metrics" | "floormap" | "sle_metrics" | "client_metrics" | "ap_events";
type Tab = "snapshots" | "csv-logs";

interface UnifiedLogRow {
  filename: string;
  fileType: FileType;
  savedAt: string;
  triggeredBy: string;
  siteCount: number | null;
  apCount: number | null;
  sizeBytes: number;
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function TriggerBadge({ trigger }: { trigger: string }) {
  if (trigger === "manual-backfill") {
    return (
      <span
        className="px-2 py-0.5 rounded border text-xs font-mono"
        style={{ borderColor: "var(--orange)", color: "var(--orange)", backgroundColor: "rgba(255,140,0,0.08)" }}
      >
        manual-backfill
      </span>
    );
  }
  const isManual = trigger === "manual";
  return (
    <span
      className="px-2 py-0.5 rounded border text-xs font-mono"
      style={
        isManual
          ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
          : { borderColor: "var(--green)", color: "var(--green)", backgroundColor: "rgba(0,255,128,0.08)" }
      }
    >
      {isManual ? "manual" : "auto"}
    </span>
  );
}

function ApEventsBackfillButton({ onDone }: { onDone: () => void }) {
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  const handleClick = async () => {
    if (loading) return;
    if (
      !window.confirm(
        "過去7日分のAPイベントを取得してCSV保存します。サイト数によっては数十秒かかる場合があります。実行しますか？"
      )
    ) {
      return;
    }
    setLoading(true);
    try {
      const result = await backfillApEvents(7);
      const errSuffix = result.errors.length > 0 ? `（${result.errors.length}サイトでエラー）` : "";
      setToast({
        msg: `${result.new_events}件の新規イベントを取得しました（既存${result.skipped_existing}件はスキップ）${errSuffix}`,
        ok: result.errors.length === 0,
      });
      onDone();
    } catch {
      setToast({ msg: "バックフィルに失敗しました", ok: false });
    } finally {
      setLoading(false);
      setTimeout(() => setToast(null), 5000);
    }
  };

  return (
    <>
      <button
        onClick={handleClick}
        disabled={loading}
        className="flex items-center gap-2 px-3 py-1.5 border rounded-lg text-sm font-mono transition-all disabled:opacity-50"
        style={{ borderColor: "var(--orange)", color: "var(--orange)" }}
      >
        <History className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
        {loading ? "取得中..." : "過去7日分のイベントログを取得"}
      </button>
      {toast && (
        <div
          className="fixed bottom-6 right-6 z-50 px-4 py-3 rounded-lg border text-sm font-mono shadow-xl max-w-md"
          style={{
            backgroundColor: "var(--bg-card)",
            borderColor: toast.ok ? "var(--green)" : "var(--red)",
            color: toast.ok ? "var(--green)" : "var(--red)",
          }}
        >
          {toast.msg}
        </div>
      )}
    </>
  );
}

function TypeBadge({ fileType }: { fileType: FileType }) {
  if (fileType === "floormap") {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-mono"
        style={{ borderColor: "var(--purple)", color: "var(--purple)", backgroundColor: "rgba(124,58,237,0.08)" }}
      >
        <Map className="w-3 h-3" />
        Floor Map
      </span>
    );
  }
  if (fileType === "sle_metrics") {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-mono"
        style={{ borderColor: "var(--green)", color: "var(--green)", backgroundColor: "rgba(0,255,128,0.08)" }}
      >
        SLE Metrics
      </span>
    );
  }
  if (fileType === "client_metrics") {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-mono"
        style={{ borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }}
      >
        Client Metrics
      </span>
    );
  }
  if (fileType === "ap_events") {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-mono"
        style={{ borderColor: "var(--red)", color: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }}
      >
        AP Events
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-mono"
      style={{ borderColor: "var(--text-muted)", color: "var(--text-muted)", backgroundColor: "transparent" }}
    >
      AP Metrics
    </span>
  );
}

function SnapshotsTab() {
  const { timezone } = useTimezone();
  const { data: metas, isLoading } = useSWR<SnapshotDbMeta[]>("snapshot-dbs", fetchSnapshotDbs);

  return (
    <div className="space-y-4">
      <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
        スナップショットDBスロット（最大2件）— 72時間分のメトリクスを保存
      </p>

      {isLoading && (
        <div className="flex justify-center py-10">
          <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading...</div>
        </div>
      )}

      {!isLoading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {(metas ?? [
            { slot: 1, saved_at: null, ap_count: null, site_count: null, from_dt: null, to_dt: null, size_bytes: null },
            { slot: 2, saved_at: null, ap_count: null, site_count: null, from_dt: null, to_dt: null, size_bytes: null },
          ]).map((meta) => {
            const hasData = !!meta.saved_at;
            return (
              <div
                key={meta.slot}
                className="border rounded-lg p-5 space-y-3"
                style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
              >
                <div className="flex items-center justify-between">
                  <p className="text-sm font-mono font-semibold" style={{ color: "var(--cyan)" }}>
                    Slot {meta.slot}
                  </p>
                  {hasData && (
                    <span className="text-xs font-mono px-2 py-0.5 rounded border"
                      style={{ borderColor: "var(--green)", color: "var(--green)", backgroundColor: "rgba(0,255,128,0.08)" }}>
                      有効
                    </span>
                  )}
                </div>

                {hasData ? (
                  <div className="text-xs font-mono space-y-1">
                    <p style={{ color: "var(--text-primary)" }}>
                      保存: {toLocalString(meta.saved_at!, timezone)}
                    </p>
                    {meta.from_dt && meta.to_dt && (
                      <p style={{ color: "var(--text-muted)" }}>
                        期間: {toLocalString(meta.from_dt, timezone).slice(0, 16)}
                        {" "}〜{" "}
                        {toLocalString(meta.to_dt, timezone).slice(0, 16)}
                      </p>
                    )}
                    <p style={{ color: "var(--text-muted)" }}>
                      {meta.site_count} sites · {meta.ap_count?.toLocaleString()} APs · {formatSize(meta.size_bytes ?? 0)}
                    </p>
                  </div>
                ) : (
                  <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>Empty</p>
                )}

                {hasData && (
                  <div className="flex gap-2">
                    <Link
                      href={`/snapshot/${meta.slot}`}
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-mono transition-all"
                      style={{ borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }}
                    >
                      <Eye className="w-3.5 h-3.5" />
                      閲覧
                    </Link>
                    <a
                      href={getSnapshotDbDownloadUrl(meta.slot, timezone)}
                      download
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-mono transition-all"
                      style={{ borderColor: "var(--border-cyan)", color: "var(--text-secondary)" }}
                    >
                      <Download className="w-3.5 h-3.5" />
                      DL
                    </a>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CsvLogsTab() {
  const { timezone } = useTimezone();
  const [selectedSiteId, setSelectedSiteId] = useState<string>("");
  const [selectedApId, setSelectedApId] = useState<string>("");
  const [selectedApMac, setSelectedApMac] = useState<string>("");
  const [selectedClientMac, setSelectedClientMac] = useState<string>("");
  const [triggerFilter, setTriggerFilter] = useState<TriggerFilter>("all");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [pseudoBusy, setPseudoBusy] = useState(false);
  const [pseudoError, setPseudoError] = useState<string | null>(null);

  // fetchLogs: ファイルシステムから全CSV（ap_metrics + floormap）を取得
  const { data: logData, isLoading, mutate } = useSWR("log-files", fetchLogs);
  // fetchSnapshots: ap_metrics のメタデータ（site_count, ap_count, triggered_by）を補完
  const { data: snapshots } = useSWR<SnapshotInfo[]>("snapshots", fetchSnapshots);
  const { data: sites } = useSWR<SiteInfo[]>("sites", fetchSites);
  const { data: aps } = useSWR<ApInfo[]>(
    selectedSiteId ? `site-aps-${selectedSiteId}` : null,
    () => fetchSiteAps(selectedSiteId),
  );
  // Client Filter 用: client_metrics 選択時のみ、選択中の Site/AP に接続したクライアント一覧を取得
  const { data: clientList } = useSWR<ClientListItem[]>(
    typeFilter === "client_metrics"
      ? `client-list-${selectedSiteId}-${selectedApMac}`
      : null,
    () => fetchClientList(selectedSiteId || undefined, selectedApMac || undefined),
  );

  // ap_metrics のメタデータをファイル名でインデックス化
  const snapIndex = useMemo(() => {
    const m: Record<string, SnapshotInfo> = {};
    (snapshots ?? []).forEach((s) => { m[s.filename] = s; });
    return m;
  }, [snapshots]);

  // ファイル一覧をUnifiedLogRowに統合（降順ソート）
  const allRows = useMemo((): UnifiedLogRow[] => {
    return (logData?.files ?? []).map((f: LogFileInfo) => {
      const snap = snapIndex[f.filename];
      const isFloormap = f.filename.startsWith("floormap_");
      const isSleMetrics = f.filename.startsWith("sle_metrics_");
      const isClientMetrics = f.filename.startsWith("client_metrics_");
      const isApEvents = f.filename.startsWith("ap_events_");
      const isBackfill = f.filename.includes("_backfill");
      const isManual = f.filename.includes("_manual");
      const fileType: FileType = isFloormap
        ? "floormap"
        : isSleMetrics
        ? "sle_metrics"
        : isClientMetrics
        ? "client_metrics"
        : isApEvents
        ? "ap_events"
        : "ap_metrics";
      return {
        filename: f.filename,
        fileType,
        savedAt: snap?.saved_at ?? f.created_at,
        triggeredBy: snap?.triggered_by ?? (isBackfill ? "manual-backfill" : isManual ? "manual" : "auto"),
        siteCount: snap?.site_count ?? null,
        apCount: snap?.ap_count ?? null,
        sizeBytes: f.size_bytes,
      };
    }).sort((a, b) => b.savedAt.localeCompare(a.savedAt));
  }, [logData, snapIndex]);

  const filtered = allRows.filter((row) => {
    if (typeFilter !== "all" && row.fileType !== typeFilter) return false;
    if (triggerFilter !== "all" && row.triggeredBy !== triggerFilter) return false;
    return true;
  });

  const hasFilter =
    selectedSiteId || selectedApId || selectedApMac || selectedClientMac ||
    triggerFilter !== "all" || typeFilter !== "all";

  const clearFilters = () => {
    setSelectedSiteId("");
    setSelectedApId("");
    setSelectedApMac("");
    setSelectedClientMac("");
    setTriggerFilter("all");
    setTypeFilter("all");
  };
  const totalBytes = logData?.total_bytes ?? 0;
  const allSelected = filtered.length > 0 && filtered.every((r) => selected.has(r.filename));
  const someSelected = selected.size > 0;
  const tooManyForPseudonymize = selected.size > PSEUDONYMIZE_MAX_FILES;

  const toggleAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(filtered.map((r) => r.filename)));
  };

  const toggleOne = (filename: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  };

  // 仮名化ダウンロード。サーバがその場で変換して返す（仮名化版は保存されない）。
  // leak check の発火はファイルではなくエラーで返るので、fetch で受けて表示する。
  const handlePseudonymize = async () => {
    const filenames = Array.from(selected);
    setPseudoBusy(true);
    setPseudoError(null);
    try {
      await downloadPseudonymized(
        getPseudonymizedLogsUrl(filenames),
        filenames.length === 1 ? filenames[0] : "pseudonymized_logs.zip",
      );
    } catch (e) {
      setPseudoError(e instanceof Error ? e.message : "仮名化ダウンロードに失敗しました");
    } finally {
      setPseudoBusy(false);
    }
  };

  const handleDelete = async () => {
    const filenames = Array.from(selected);
    if (!window.confirm(`${filenames.length} 件のログファイルを削除しますか？`)) return;
    setDeleting(true);
    try {
      await deleteLogs(filenames);
      setSelected(new Set());
      await mutate();
    } catch {
      alert("削除に失敗しました");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div>
      {totalBytes > 0 && (
        <p className="text-xs font-mono mb-4" style={{ color: "var(--text-muted)" }}>
          合計 {formatSize(totalBytes)}
        </p>
      )}

      {/* 仮名化ダウンロードの裏返し。手元で加工したファイルを元の値に戻す */}
      <RestorePanel />

      {/* フィルター */}
      <div className="border rounded-lg p-4 mb-4 flex flex-wrap gap-4 items-end"
        style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}>
        <div>
          <label className="block text-xs mb-1" style={{ color: "var(--text-muted)" }}>Type</label>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as TypeFilter)}
            className="text-sm font-mono px-3 py-1.5 rounded border bg-transparent"
            style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
          >
            <option value="all">All</option>
            <option value="ap_metrics">AP Metrics</option>
            <option value="floormap">Floor Map</option>
            <option value="sle_metrics">SLE Metrics</option>
            <option value="client_metrics">Client Metrics</option>
            <option value="ap_events">AP Events</option>
          </select>
        </div>
        <div>
          <label className="block text-xs mb-1" style={{ color: "var(--text-muted)" }}>Trigger</label>
          <select
            value={triggerFilter}
            onChange={(e) => setTriggerFilter(e.target.value as TriggerFilter)}
            className="text-sm font-mono px-3 py-1.5 rounded border bg-transparent"
            style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
          >
            <option value="all">All</option>
            <option value="manual">Manual</option>
            <option value="auto">Auto</option>
            <option value="manual-backfill">Manual Backfill</option>
          </select>
        </div>
        <div>
          <ApEventsBackfillButton onDone={() => mutate()} />
        </div>
        {typeFilter !== "floormap" && typeFilter !== "sle_metrics" && typeFilter !== "client_metrics" && typeFilter !== "ap_events" && (
          <>
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--text-muted)" }}>Site Filter</label>
              <select
                value={selectedSiteId}
                onChange={(e) => { setSelectedSiteId(e.target.value); setSelectedApId(""); }}
                className="text-sm font-mono px-3 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              >
                <option value="">All Sites</option>
                {sites?.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--text-muted)" }}>AP Filter</label>
              <select
                value={selectedApId}
                onChange={(e) => setSelectedApId(e.target.value)}
                disabled={!selectedSiteId}
                className="text-sm font-mono px-3 py-1.5 rounded border bg-transparent disabled:opacity-40"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              >
                <option value="">All APs</option>
                {aps?.map((ap) => (
                  <option key={ap.id} value={ap.id}>{ap.name || ap.mac}</option>
                ))}
              </select>
            </div>
          </>
        )}
        {typeFilter === "client_metrics" && (
          <>
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--text-muted)" }}>Site Filter</label>
              <select
                value={selectedSiteId}
                onChange={(e) => { setSelectedSiteId(e.target.value); setSelectedApMac(""); setSelectedClientMac(""); }}
                className="text-sm font-mono px-3 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              >
                <option value="">All Sites</option>
                {sites?.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--text-muted)" }}>AP Filter</label>
              <select
                value={selectedApMac}
                onChange={(e) => { setSelectedApMac(e.target.value); setSelectedClientMac(""); }}
                disabled={!selectedSiteId}
                className="text-sm font-mono px-3 py-1.5 rounded border bg-transparent disabled:opacity-40"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              >
                <option value="">All APs</option>
                {aps?.map((ap) => (
                  <option key={ap.id} value={ap.mac}>{ap.name || ap.mac}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--text-muted)" }}>Client Filter</label>
              <select
                value={selectedClientMac}
                onChange={(e) => setSelectedClientMac(e.target.value)}
                className="text-sm font-mono px-3 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              >
                <option value="">All Clients</option>
                {clientList?.map((c) => (
                  <option key={c.mac} value={c.mac}>{c.hostname || c.mac}</option>
                ))}
              </select>
            </div>
          </>
        )}
        {hasFilter && (
          <button
            onClick={clearFilters}
            className="text-xs px-3 py-1.5 border rounded"
            style={{ borderColor: "var(--chart-grid)", color: "var(--text-muted)" }}
          >
            Clear
          </button>
        )}
      </div>

      {/* 選択時アクションバー */}
      {someSelected && (
        <div className="border rounded-lg px-4 py-2 mb-4"
          style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}>
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-sm font-mono" style={{ color: "var(--text-secondary)" }}>
              {selected.size} 件選択中
            </span>
            <a
              href={getLogsZipUrl(Array.from(selected))}
              download
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-sm font-mono transition-all"
              style={{ borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }}
            >
              <FileDown className="w-4 h-4" />
              ZIP ダウンロード
            </a>
            {/* 通常のダウンロードとは別の導線にする（取り違えると実データが出る） */}
            <button
              onClick={handlePseudonymize}
              disabled={pseudoBusy || tooManyForPseudonymize}
              title={
                tooManyForPseudonymize
                  ? `一度に仮名化できるのは ${PSEUDONYMIZE_MAX_FILES} 件までです`
                  : PSEUDONYMIZE_NOTICE
              }
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-sm font-mono transition-all disabled:opacity-40"
              style={{ borderColor: "var(--green)", color: "var(--green)", backgroundColor: "rgba(0,255,128,0.08)" }}
            >
              <ShieldCheck className={`w-4 h-4 ${pseudoBusy ? "animate-pulse" : ""}`} />
              {pseudoBusy
                ? "仮名化中..."
                : `仮名化ダウンロード${selected.size > 1 ? "（ZIP）" : ""}`}
            </button>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-sm font-mono transition-all disabled:opacity-40"
              style={{ borderColor: "var(--red)", color: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }}
            >
              <Trash2 className="w-4 h-4" />
              {deleting ? "削除中..." : "選択削除"}
            </button>
          </div>
          {/* 仮名化 ≠ 匿名化。落とす人がREADMEを読むとは限らないので導線の隣に置く */}
          <p className="text-xs mt-2 leading-relaxed" style={{ color: "var(--text-muted)" }}>
            {PSEUDONYMIZE_NOTICE}
            {tooManyForPseudonymize && (
              <span style={{ color: "var(--yellow)" }}>
                {" "}一度に仮名化できるのは {PSEUDONYMIZE_MAX_FILES} 件までです（現在 {selected.size} 件選択中）。
              </span>
            )}
          </p>
          {pseudoError && (
            <p className="text-xs mt-2 font-mono whitespace-pre-wrap" style={{ color: "var(--red)" }}>
              {pseudoError}
            </p>
          )}
        </div>
      )}

      {isLoading && (
        <div className="flex justify-center py-20">
          <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading...</div>
        </div>
      )}

      {!isLoading && filtered.length === 0 && (
        <p className="text-center py-20 text-sm" style={{ color: "var(--text-muted)" }}>
          {allRows.length === 0
            ? "CSVログがありません。「Save Now」で保存してください。"
            : "該当するログがありません。"}
        </p>
      )}

      {!isLoading && filtered.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm font-mono border-collapse">
            <thead>
              <tr className="border-b" style={{ borderColor: "var(--border-cyan)" }}>
                <th className="py-3 px-3 w-8">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="w-4 h-4 cursor-pointer accent-cyan-400"
                  />
                </th>
                {["Saved At", "Type", "Trigger", "Sites", "Records", "Size", ""].map((h) => (
                  <th key={h} className="text-left py-3 px-3 font-normal whitespace-nowrap"
                    style={{ color: "var(--text-muted)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr
                  key={row.filename}
                  className="border-b transition-colors"
                  style={{ borderColor: "var(--chart-grid)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}
                >
                  <td className="py-3 px-3">
                    <input
                      type="checkbox"
                      checked={selected.has(row.filename)}
                      onChange={() => toggleOne(row.filename)}
                      className="w-4 h-4 cursor-pointer accent-cyan-400"
                    />
                  </td>
                  <td className="py-3 px-3 whitespace-nowrap" style={{ color: "var(--text-primary)" }}>
                    {toLocalString(row.savedAt, timezone)}
                  </td>
                  <td className="py-3 px-3"><TypeBadge fileType={row.fileType} /></td>
                  <td className="py-3 px-3"><TriggerBadge trigger={row.triggeredBy} /></td>
                  <td className="py-3 px-3" style={{ color: "var(--text-secondary)" }}>
                    {row.siteCount ?? <span style={{ color: "var(--text-muted)" }}>—</span>}
                  </td>
                  <td className="py-3 px-3" style={{ color: "var(--text-secondary)" }}>
                    {row.apCount != null
                      ? `${row.apCount.toLocaleString()} records`
                      : <span style={{ color: "var(--text-muted)" }}>—</span>}
                  </td>
                  <td className="py-3 px-3" style={{ color: "var(--text-secondary)" }}>
                    {formatSize(row.sizeBytes)}
                  </td>
                  <td className="py-3 px-3">
                    {row.fileType === "ap_metrics" ? (
                      <a
                        href={getSnapshotDownloadUrl(
                          row.filename,
                          selectedSiteId || undefined,
                          selectedApId || undefined,
                        )}
                        download
                        className="flex items-center gap-1 px-2 py-1 rounded border text-xs transition-all whitespace-nowrap"
                        style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
                      >
                        <Download className="w-3 h-3" />
                        {selectedSiteId || selectedApId ? "Filtered DL" : "Download"}
                      </a>
                    ) : row.fileType === "sle_metrics" ? (
                      <a
                        href={getLogDownloadUrl(row.filename)}
                        download
                        className="flex items-center gap-1 px-2 py-1 rounded border text-xs transition-all whitespace-nowrap"
                        style={{ borderColor: "var(--green)", color: "var(--green)" }}
                      >
                        <Download className="w-3 h-3" />
                        Download
                      </a>
                    ) : row.fileType === "client_metrics" ? (
                      <a
                        href={getLogFilteredDownloadUrl(row.filename, {
                          siteId: selectedSiteId || undefined,
                          apMac: selectedApMac || undefined,
                          clientMac: selectedClientMac || undefined,
                        })}
                        download
                        className="flex items-center gap-1 px-2 py-1 rounded border text-xs transition-all whitespace-nowrap"
                        style={{ borderColor: "var(--cyan)", color: "var(--cyan)" }}
                      >
                        <Download className="w-3 h-3" />
                        {selectedSiteId || selectedApMac || selectedClientMac ? "Filtered DL" : "Download"}
                      </a>
                    ) : (
                      <a
                        href={getLogDownloadUrl(row.filename)}
                        download
                        className="flex items-center gap-1 px-2 py-1 rounded border text-xs transition-all whitespace-nowrap"
                        style={{ borderColor: "var(--purple)", color: "var(--purple)" }}
                      >
                        <Download className="w-3 h-3" />
                        Download
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function HistoryPage() {
  const [tab, setTab] = useState<Tab>("csv-logs");
  const { data: snapshots, isLoading, mutate } = useSWR<SnapshotInfo[]>("snapshots", fetchSnapshots);

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-8">
        <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
          History
        </h1>
        <div className="flex items-center gap-2 flex-nowrap">
          <SaveNowButton />
          <button
            onClick={() => mutate()}
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all whitespace-nowrap"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <ThemeToggle />
        </div>
      </header>

      <TabNav />

      {/* タブ */}
      <div className="flex gap-1 mb-6 border-b" style={{ borderColor: "var(--border-cyan)" }}>
        {([
          { id: "snapshots" as Tab, label: "Snapshots", icon: <Camera className="w-4 h-4" /> },
          { id: "csv-logs" as Tab, label: "CSV Logs", icon: <FileDown className="w-4 h-4" /> },
        ]).map(({ id, label, icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-mono transition-all border-b-2 -mb-px"
            style={
              tab === id
                ? { borderColor: "var(--cyan)", color: "var(--cyan)" }
                : { borderColor: "transparent", color: "var(--text-muted)" }
            }
          >
            {icon}
            {label}
          </button>
        ))}
      </div>

      {tab === "snapshots" ? <SnapshotsTab /> : <CsvLogsTab />}
    </main>
  );
}
