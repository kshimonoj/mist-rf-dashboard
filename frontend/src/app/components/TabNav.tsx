"use client";

import { History as HistoryIcon, Map, Search, ZapOff } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

export type HomeTab = "Site Overview" | "Floor Map";

interface TabNavProps {
  /** トップページでのみ有効。現在選択中のローカルタブ */
  homeTab?: HomeTab;
  /** トップページでのみ有効。ローカルタブの切り替えハンドラ */
  onHomeTabChange?: (tab: HomeTab) => void;
}

const ROUTE_TABS = [
  { href: "/insights", label: "Insights", icon: <Search className="w-3.5 h-3.5" /> },
  { href: "/history", label: "History", icon: <HistoryIcon className="w-3.5 h-3.5" /> },
  { href: "/hangap", label: "Hang AP", icon: <ZapOff className="w-3.5 h-3.5" /> },
] as const;

const tabClass =
  "flex items-center gap-1.5 px-4 py-2 text-sm transition-colors -mb-px border-b-2 whitespace-nowrap shrink-0";

function tabStyle(active: boolean) {
  return {
    borderColor: active ? "var(--cyan)" : "transparent",
    color: active ? "var(--cyan)" : "var(--text-muted)",
  };
}

/**
 * 全ページ共通のタブ行。Site Overview / Floor Map はトップページ内の状態切替
 * （他ページからは "/" への遷移になる）、Insights / History / Hang AP は別ルートへの
 * 遷移。見た目は統一し、実装（button vs Link）だけを分ける。
 */
export default function TabNav({ homeTab, onHomeTabChange }: TabNavProps) {
  const pathname = usePathname();
  const isHome = pathname === "/";

  return (
    <nav
      className="flex gap-1 mb-6 border-b overflow-x-auto"
      style={{ borderColor: "var(--chart-grid)" }}
      aria-label="ページナビゲーション"
    >
      {isHome ? (
        <button
          onClick={() => onHomeTabChange?.("Site Overview")}
          className={tabClass}
          style={tabStyle(homeTab === "Site Overview")}
          aria-current={homeTab === "Site Overview" ? "page" : undefined}
        >
          Site Overview
        </button>
      ) : (
        <Link href="/" className={tabClass} style={tabStyle(false)}>
          Site Overview
        </Link>
      )}

      {isHome ? (
        <button
          onClick={() => onHomeTabChange?.("Floor Map")}
          className={tabClass}
          style={tabStyle(homeTab === "Floor Map")}
          aria-current={homeTab === "Floor Map" ? "page" : undefined}
        >
          <Map className="w-3.5 h-3.5" />
          Floor Map
        </button>
      ) : (
        <Link href="/?tab=floormap" className={tabClass} style={tabStyle(false)}>
          <Map className="w-3.5 h-3.5" />
          Floor Map
        </Link>
      )}

      {ROUTE_TABS.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={tabClass}
            style={tabStyle(active)}
            aria-current={active ? "page" : undefined}
          >
            {tab.icon}
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
