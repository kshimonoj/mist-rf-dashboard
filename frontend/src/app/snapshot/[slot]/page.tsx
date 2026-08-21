"use client";

import { ArrowLeft, Home, Camera, Map } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { fetchSnapshotSites, fetchSnapshotDbs, SnapshotSite, SnapshotDbMeta } from "@/lib/api";
import { toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";
import MaskToggle from "@/app/components/MaskToggle";
import ThemeToggle from "@/app/components/ThemeToggle";
import FloorMapTab from "@/app/components/FloorMapTab";
import { useMask } from "@/app/providers";
import { FLOOR_MAP_BLOCKED_TITLE } from "@/lib/mask";

const TABS = ["Site Overview", "Floor Map"] as const;
type TabName = typeof TABS[number];

export default function SnapshotSitePage({ params }: { params: { slot: string } }) {
  const slot = Number(params.slot);
  const { timezone } = useTimezone();
  const { masked } = useMask();
  const [activeTab, setActiveTab] = useState<TabName>("Site Overview");

  const { data: metas } = useSWR<SnapshotDbMeta[]>("snapshot-dbs", fetchSnapshotDbs);
  const { data: sites, isLoading } = useSWR<SnapshotSite[]>(
    `snapshot-${slot}-sites`,
    () => fetchSnapshotSites(slot),
  );

  const meta = metas?.find((m) => m.slot === slot);

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}>
            <Home className="w-4 h-4" />
            Home
          </Link>
          <span style={{ color: "var(--chart-grid)" }}>|</span>
          <Link href="/" className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}>
            <ArrowLeft className="w-4 h-4" />
            Back
          </Link>
          <div className="ml-1">
            <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
              Snapshot — Slot {slot}
            </h1>
            <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
              {meta?.saved_at ? `保存: ${toLocalString(meta.saved_at, timezone)}` : "スナップショット閲覧モード"}
            </p>
          </div>
        </div>
          <MaskToggle />
          <ThemeToggle />
      </header>

      {/* スナップショットバナー */}
      {meta && (
        <div className="border rounded-lg p-4 mb-6 flex flex-wrap gap-4 items-center"
          style={{ borderColor: "var(--purple)", backgroundColor: "rgba(124,58,237,0.08)" }}>
          <Camera className="w-5 h-5 flex-shrink-0" style={{ color: "var(--purple)" }} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-mono font-semibold" style={{ color: "var(--purple)" }}>
              スナップショット閲覧モード
            </p>
            {meta.from_dt && meta.to_dt && (
              <p className="text-xs font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>
                期間: {toLocalString(meta.from_dt, timezone).slice(0, 16)}
                {" "}〜{" "}
                {toLocalString(meta.to_dt, timezone).slice(0, 16)}
              </p>
            )}
          </div>
          {meta.site_count != null && (
            <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
              {meta.site_count} sites · {meta.ap_count?.toLocaleString()} APs
            </span>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b" style={{ borderColor: "var(--chart-grid)" }}>
        {TABS.map((tab) => {
          const blocked = tab === "Floor Map" && masked;
          return (
            <button
              key={tab}
              onClick={() => !blocked && setActiveTab(tab)}
              disabled={blocked}
              title={blocked ? FLOOR_MAP_BLOCKED_TITLE : undefined}
              aria-disabled={blocked}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${blocked ? "opacity-40 cursor-not-allowed" : ""}`}
              style={{
                borderBottomColor: activeTab === tab ? "var(--cyan)" : "transparent",
                color: activeTab === tab ? "var(--cyan)" : "var(--text-muted)",
              }}
            >
              {tab === "Floor Map" && <Map className="w-4 h-4" />}
              {tab}
            </button>
          );
        })}
      </div>

      {activeTab === "Site Overview" && (
        <>
          {isLoading && (
            <div className="flex justify-center py-20">
              <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading...</div>
            </div>
          )}
          {!isLoading && sites && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {sites.map((site) => (
                <Link key={site.id} href={`/snapshot/${slot}/sites/${site.id}`}>
                  <div
                    className="border rounded-lg p-5 hover:shadow-lg transition-all cursor-pointer"
                    style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
                    onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--border-cyan-hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border-cyan)")}
                  >
                    <h3 className="font-display font-semibold text-lg mb-2"
                      style={{ color: "var(--text-primary)" }}>
                      {site.name}
                    </h3>
                    <p className="text-2xl font-bold font-mono" style={{ color: "var(--cyan)" }}>
                      {site.ap_count}
                    </p>
                    <p className="text-sm" style={{ color: "var(--text-muted)" }}>APs</p>
                  </div>
                </Link>
              ))}
              {sites.length === 0 && (
                <p className="col-span-full text-center py-10 text-sm" style={{ color: "var(--text-muted)" }}>
                  サイトが見つかりません
                </p>
              )}
            </div>
          )}
        </>
      )}

      {activeTab === "Floor Map" && (
        <FloorMapTab snapshotSlot={slot} />
      )}
    </main>
  );
}
