"use client";

import { Save } from "lucide-react";
import { useState } from "react";
import { createSnapshot, saveFloorMapLog, FloorMapSaveRow } from "@/lib/api";

interface SaveNowButtonProps {
  getFloorMapRows?: () => FloorMapSaveRow[] | null;
  /** ドロップダウンのメニュー項目として表示する(ボタン枠なしの全幅・左寄せ) */
  asMenuItem?: boolean;
}

export default function SaveNowButton({ getFloorMapRows, asMenuItem }: SaveNowButtonProps = {}) {
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  const handleSave = async () => {
    if (loading) return;
    setLoading(true);
    try {
      const snap = await createSnapshot();

      // Floor Map データが取得できる場合は一緒に保存
      if (getFloorMapRows) {
        const rows = getFloorMapRows();
        if (rows && rows.length > 0) {
          try {
            await saveFloorMapLog(rows);
          } catch {
            // non-critical: AP metrics 保存は成功しているので無視
          }
        }
      }

      setToast({ msg: `保存完了: ${snap.ap_count} APs`, ok: true });
    } catch {
      setToast({ msg: "保存に失敗しました", ok: false });
    } finally {
      setLoading(false);
      setTimeout(() => setToast(null), 4000);
    }
  };

  return (
    <>
      <button
        onClick={handleSave}
        disabled={loading}
        role={asMenuItem ? "menuitem" : undefined}
        className={
          asMenuItem
            ? "w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors disabled:opacity-50"
            : "flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all disabled:opacity-50"
        }
        style={asMenuItem ? { color: "var(--cyan)" } : { borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
        onMouseEnter={asMenuItem ? (e) => (e.currentTarget.style.backgroundColor = "var(--bg-hover)") : undefined}
        onMouseLeave={asMenuItem ? (e) => (e.currentTarget.style.backgroundColor = "") : undefined}
      >
        <Save className={`w-4 h-4 ${loading ? "animate-pulse" : ""}`} />
        {loading ? "Saving..." : "Save Now"}
      </button>
      {toast && (
        <div
          className="fixed bottom-6 right-6 z-50 px-4 py-3 rounded-lg border text-sm font-mono shadow-xl"
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
