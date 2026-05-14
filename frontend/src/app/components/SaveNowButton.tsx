"use client";

import { Save } from "lucide-react";
import { useState } from "react";
import { createSnapshot } from "@/lib/api";

export default function SaveNowButton() {
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  const handleSave = async () => {
    if (loading) return;
    setLoading(true);
    try {
      const snap = await createSnapshot();
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
        className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all disabled:opacity-50"
        style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
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
