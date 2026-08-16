"use client";

import { X, Camera, Eye, Download, RefreshCw, Upload } from "lucide-react";
import { useRef, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import {
  fetchSnapshotDbs, createSnapshotDb, uploadSnapshotDb,
  getSnapshotDbDownloadUrl, SnapshotDbMeta,
} from "@/lib/api";
import { toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";

function formatSize(bytes: number | null) {
  if (!bytes) return "-";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function SlotCard({
  meta, onSave, onView, saving, timezone,
}: {
  meta: SnapshotDbMeta;
  onSave: () => void;
  onView: () => void;
  saving: boolean;
  timezone: string;
}) {
  const hasData = !!meta.saved_at;
  return (
    <div className="border rounded-lg p-4 space-y-3"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-hover)" }}>
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
            {meta.site_count} sites · {meta.ap_count?.toLocaleString()} APs · {formatSize(meta.size_bytes)}
          </p>
        </div>
      ) : (
        <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>Empty</p>
      )}

      <div className="flex flex-wrap gap-2">
        {hasData && (
          <button onClick={onView}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-mono transition-all"
            style={{ borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }}>
            <Eye className="w-3.5 h-3.5" />
            閲覧
          </button>
        )}
        {hasData && (
          <a href={getSnapshotDbDownloadUrl(meta.slot, timezone)}
            download
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-mono transition-all"
            style={{ borderColor: "var(--border-cyan)", color: "var(--text-secondary)" }}>
            <Download className="w-3.5 h-3.5" />
            DL
          </a>
        )}
        <button onClick={onSave} disabled={saving}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-mono transition-all disabled:opacity-40"
          style={{ borderColor: "var(--purple)", color: "var(--purple)", backgroundColor: "rgba(124,58,237,0.08)" }}>
          <RefreshCw className={`w-3.5 h-3.5 ${saving ? "animate-spin" : ""}`} />
          {hasData ? "上書き" : "保存"}
        </button>
      </div>
    </div>
  );
}

interface SnapshotButtonProps {
  /** ドロップダウンのメニュー項目として表示する(ボタン枠なしの全幅・左寄せ) */
  asMenuItem?: boolean;
}

export default function SnapshotButton({ asMenuItem }: SnapshotButtonProps = {}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        role={asMenuItem ? "menuitem" : undefined}
        className={
          asMenuItem
            ? "w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors"
            : "flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all"
        }
        style={asMenuItem ? { color: "var(--cyan)" } : { borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
        onMouseEnter={asMenuItem ? (e) => (e.currentTarget.style.backgroundColor = "var(--bg-hover)") : undefined}
        onMouseLeave={asMenuItem ? (e) => (e.currentTarget.style.backgroundColor = "") : undefined}
      >
        <Camera className="w-4 h-4" />
        Snapshot
      </button>
      {open && <SnapshotModal onClose={() => setOpen(false)} />}
    </>
  );
}

function SnapshotModal({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const { timezone } = useTimezone();
  const { data: metas, mutate } = useSWR("snapshot-dbs", fetchSnapshotDbs);
  const [savingSlot, setSavingSlot] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const showToast = (msg: string, ok: boolean) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 4000);
  };

  const handleSave = async (slot?: number) => {
    setSavingSlot(slot ?? 0);
    try {
      await createSnapshotDb(slot);
      await mutate();
      showToast("スナップショットを保存しました", true);
    } catch (e) {
      showToast(e instanceof Error ? e.message : "保存に失敗しました", false);
    } finally {
      setSavingSlot(null);
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await uploadSnapshotDb(file);
      await mutate();
      showToast("アップロードしました", true);
    } catch (e) {
      showToast(e instanceof Error ? e.message : "アップロードに失敗しました", false);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.6)" }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-lg rounded-xl shadow-2xl border flex flex-col"
        style={{ backgroundColor: "var(--bg-card)", borderColor: "var(--border-cyan)", maxHeight: "90vh" }}>
        <div className="flex items-center justify-between p-4 border-b flex-shrink-0"
          style={{ borderColor: "var(--border-cyan)" }}>
          <h2 className="font-display font-semibold tracking-wider text-sm" style={{ color: "var(--cyan)" }}>
            SNAPSHOTS
          </h2>
          <button onClick={onClose} style={{ color: "var(--text-muted)" }}>
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4 overflow-y-auto">
          <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
            72時間分のメトリクスを最大2スロットに保存できます
          </p>

          {/* Slot cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {(metas ?? [{slot:1,saved_at:null,ap_count:null,site_count:null,from_dt:null,to_dt:null,size_bytes:null},{slot:2,saved_at:null,ap_count:null,site_count:null,from_dt:null,to_dt:null,size_bytes:null}]).map((meta) => (
              <SlotCard
                key={meta.slot}
                meta={meta}
                timezone={timezone}
                saving={savingSlot === meta.slot}
                onSave={() => handleSave(meta.slot)}
                onView={() => { router.push(`/snapshot/${meta.slot}`); onClose(); }}
              />
            ))}
          </div>

          {/* Actions */}
          <div className="flex flex-wrap gap-2 pt-2 border-t" style={{ borderColor: "var(--chart-grid)" }}>
            <button
              onClick={() => handleSave()}
              disabled={savingSlot !== null}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-mono transition-all disabled:opacity-40"
              style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }}
            >
              <Camera className={`w-4 h-4 ${savingSlot === 0 ? "animate-pulse" : ""}`} />
              {savingSlot === 0 ? "保存中..." : "新規スナップショット保存"}
            </button>

            <button
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-mono transition-all disabled:opacity-40"
              style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
            >
              <Upload className="w-4 h-4" />
              {uploading ? "アップロード中..." : "DBファイルをアップロード"}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".db"
              className="hidden"
              onChange={handleUpload}
            />
          </div>
        </div>

        {toast && (
          <div className="mx-5 mb-4 px-4 py-2 rounded border text-sm font-mono flex-shrink-0"
            style={{
              borderColor: toast.ok ? "var(--green)" : "var(--red)",
              color: toast.ok ? "var(--green)" : "var(--red)",
              backgroundColor: toast.ok ? "rgba(0,255,128,0.05)" : "rgba(255,68,68,0.05)",
            }}>
            {toast.msg}
          </div>
        )}
      </div>
    </div>
  );
}
