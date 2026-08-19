"use client";

import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { EyeOff } from "lucide-react";
import { fetchAllSites, fetchSettings } from "@/lib/api";
import { isMaskEnabled, setMaskEnabled } from "@/lib/mask";

interface TimezoneContextValue {
  timezone: string;
  setTimezone: (tz: string) => void;
}

const TimezoneContext = createContext<TimezoneContextValue>({
  timezone: "Asia/Tokyo",
  setTimezone: () => {},
});

export const useTimezone = () => useContext(TimezoneContext);

// ── デモ用マスク ───────────────────────────────────────────────────────────────
// 実際の置き換えは lib/mask.ts（api.ts が全応答を通す）。ここは「状態を全画面から
// 参照できるようにする」ことと「ON 中であることを常時見せる」ことだけを担う。

interface MaskContextValue {
  /** マスク表示中か。サーバ描画時と初回描画時は必ず false（ハイドレーションずれを避ける） */
  masked: boolean;
  toggle: () => void;
}

const MaskContext = createContext<MaskContextValue>({ masked: false, toggle: () => {} });

export const useMask = () => useContext(MaskContext);

/**
 * 切り替えたらページを再読み込みする。取得済みのデータ（SWR のキャッシュや
 * コンポーネントの state）は変換前／変換後のどちらかで固まっているので、
 * 作り直さないと画面に実名が残る。
 */
function toggleMask(next: boolean): void {
  setMaskEnabled(next);
  window.location.reload();
}

function MaskBanner({ onDisable }: { onDisable: () => void }) {
  return (
    <div
      className="sticky top-0 z-50 flex items-center justify-center gap-3 px-4 py-2 text-sm font-bold"
      style={{ backgroundColor: "#f59e0b", color: "#1f2937" }}
      role="status"
    >
      <EyeOff className="w-4 h-4 shrink-0" />
      <span>
        デモ用マスク表示中 — AP名・サイト名・MAC・IP などは架空の値です（実データではありません）
      </span>
      <button
        onClick={onDisable}
        className="px-2 py-0.5 rounded border text-xs font-bold whitespace-nowrap"
        style={{ borderColor: "#1f2937", color: "#1f2937" }}
      >
        解除
      </button>
    </div>
  );
}

export function Providers({ children }: { children: ReactNode }) {
  const [timezone, setTimezone] = useState("Asia/Tokyo");
  const [masked, setMasked] = useState(false);

  useEffect(() => {
    fetchSettings()
      .then((s) => { if (s.timezone) setTimezone(s.timezone); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    setMasked(isMaskEnabled());
  }, []);

  useEffect(() => {
    if (!masked) return;
    // タブのタイトルにも出す（画面共有ではタブが見える）
    const original = document.title;
    document.title = `【マスク中】${original}`;
    // サイト名を先に採番しておく。自由文（分析条件など）の置き換えは
    // 「すでに採番済みの実名」を手掛かりにするため、早めに一覧を通しておく。
    fetchAllSites().catch(() => {});
    return () => { document.title = original; };
  }, [masked]);

  return (
    <MaskContext.Provider value={{ masked, toggle: () => toggleMask(!masked) }}>
      <TimezoneContext.Provider value={{ timezone, setTimezone }}>
        {masked && <MaskBanner onDisable={() => toggleMask(false)} />}
        {children}
      </TimezoneContext.Provider>
    </MaskContext.Provider>
  );
}
