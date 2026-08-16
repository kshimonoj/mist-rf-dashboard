"use client";

import { Zap } from "lucide-react";
import { useState } from "react";
import { pollNow } from "@/lib/api";

interface Props {
  onSuccess?: () => void;
  /** ドロップダウンのメニュー項目として表示する（ボタン枠なしの全幅・左寄せ） */
  asMenuItem?: boolean;
}

export default function PollNowButton({ onSuccess, asMenuItem }: Props) {
  const [polling, setPolling] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const handleClick = async () => {
    setPolling(true);
    try {
      await pollNow();
      onSuccess?.();
      setToast("ポーリング完了");
      setTimeout(() => setToast(null), 3000);
    } catch {
      setToast("ポーリング失敗");
      setTimeout(() => setToast(null), 3000);
    } finally {
      setPolling(false);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={handleClick}
        disabled={polling}
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
        <Zap className={`w-4 h-4 ${polling ? "animate-pulse" : ""}`} />
        {polling ? "Polling..." : "Poll Now"}
      </button>
      {toast && (
        <div
          className="absolute top-full mt-1 right-0 px-3 py-1.5 rounded border text-xs font-mono whitespace-nowrap z-10"
          style={{
            backgroundColor: "var(--bg-card)",
            borderColor: "var(--cyan)",
            color: "var(--cyan)",
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}
