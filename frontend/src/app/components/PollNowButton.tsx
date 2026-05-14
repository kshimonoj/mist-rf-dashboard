"use client";

import { Zap } from "lucide-react";
import { useState } from "react";
import { pollNow } from "@/lib/api";

interface Props {
  onSuccess?: () => void;
}

export default function PollNowButton({ onSuccess }: Props) {
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
        className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all disabled:opacity-50"
        style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
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
