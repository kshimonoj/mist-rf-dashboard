"use client";

import { Eye, EyeOff } from "lucide-react";
import { useMask } from "@/app/providers";

/**
 * デモ用マスクの ON / OFF。ダークモードトグルの隣に置く。
 * 切り替えるとページを再読み込みする（取得済みのデータを作り直すため）。
 */
export default function MaskToggle() {
  const { masked, toggle } = useMask();
  const label = masked ? "マスクを解除する（実名が表示されます）" : "デモ用マスクを有効にする";

  return (
    <button
      onClick={toggle}
      className="px-3 py-2 border rounded-lg text-sm transition-all flex items-center gap-1.5 whitespace-nowrap"
      style={
        masked
          ? { borderColor: "#f59e0b", color: "#1f2937", backgroundColor: "#f59e0b" }
          : { borderColor: "var(--border-cyan)", color: "var(--cyan)" }
      }
      title={label}
      aria-label={label}
      aria-pressed={masked}
    >
      {masked ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      <span className="hidden sm:inline">{masked ? "マスク中" : "マスク"}</span>
    </button>
  );
}
