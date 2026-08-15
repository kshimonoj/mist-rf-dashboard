"use client";

import { RefreshCw, Wifi, WifiOff, Activity, History, Map, Search, Tag as TagIcon, ZapOff } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import useSWR from "swr";
import { fetchSites, SiteInfo } from "@/lib/api";
import ThemeToggle from "./components/ThemeToggle";
import SaveNowButton from "./components/SaveNowButton";
import PollNowButton from "./components/PollNowButton";
import SettingsButton from "./components/SettingsModal";
import SnapshotButton from "./components/SnapshotModal";
import FloorMapTab, { FloorMapTabHandle } from "./components/FloorMapTab";

const TABS = ["Site Overview", "Floor Map"] as const;
type Tab = (typeof TABS)[number];

function SiteCard({ site }: { site: SiteInfo }) {
  const onlineRate = site.ap_count > 0 ? (site.online_count / site.ap_count) * 100 : 0;

  return (
    <Link href={`/sites/${site.id}`}>
      <div
        className="border rounded-lg p-5 hover:shadow-lg transition-all cursor-pointer group"
        style={{
          borderColor: "var(--border-cyan)",
          backgroundColor: "var(--bg-card)",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--border-cyan-hover)")}
        onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border-cyan)")}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3
              className="font-display font-semibold text-lg transition-colors"
              style={{ color: "var(--text-primary)" }}
            >
              {site.name}
            </h3>
            {site.address && (
              <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                {site.address}
              </p>
            )}
          </div>
          <Activity className="w-5 h-5 transition-colors" style={{ color: "var(--cyan)" }} />
        </div>

        <div className="grid grid-cols-3 gap-3 mb-4">
          <div className="text-center">
            <p className="text-3xl font-bold" style={{ color: "var(--text-primary)" }}>
              {site.ap_count}
            </p>
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>TOTAL APs</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold" style={{ color: "var(--green)" }}>
              {site.online_count}
            </p>
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>ONLINE</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold" style={{ color: "var(--red)" }}>
              {site.offline_count}
            </p>
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>OFFLINE</p>
          </div>
        </div>

        <div className="space-y-1">
          <div className="flex justify-between text-sm" style={{ color: "var(--text-secondary)" }}>
            <span>Online Rate</span>
            <span>{onlineRate.toFixed(0)}%</span>
          </div>
          <div className="w-full rounded-full h-1.5" style={{ backgroundColor: "var(--bg-hover)" }}>
            <div
              className="h-1.5 rounded-full transition-all"
              style={{ width: `${onlineRate}%`, backgroundColor: "var(--cyan)" }}
            />
          </div>
        </div>
      </div>
    </Link>
  );
}

export default function HomePage() {
  const [activeTab, setActiveTab] = useState<Tab>("Site Overview");
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const floorMapRef = useRef<FloorMapTabHandle>(null);

  const { data: sites, isLoading, mutate } = useSWR<SiteInfo[]>(
    "sites",
    () => fetchSites().then((d) => { setLastUpdated(new Date()); return d; }),
    { refreshInterval: 300000 }
  );

  const totalAps = sites?.reduce((s, x) => s + x.ap_count, 0) ?? 0;
  const totalOnline = sites?.reduce((s, x) => s + x.online_count, 0) ?? 0;

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-8">
        <div>
          <h1
            className="font-display font-extrabold text-3xl tracking-widest"
            style={{ color: "var(--cyan)" }}
          >
            MIST DASHBOARD
          </h1>
          <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
            Juniper Mist AP監視
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="text-right mr-2">
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>Last updated</p>
            <p className="text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
              {lastUpdated.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </p>
          </div>
          <PollNowButton onSuccess={() => mutate()} />
          <SaveNowButton getFloorMapRows={() => floorMapRef.current?.getRows() ?? null} />
          <SnapshotButton />
          <SettingsButton />
          <Link
            href="/tags"
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <TagIcon className="w-4 h-4" />
            Tags
          </Link>
          <Link
            href="/hangap"
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <ZapOff className="w-4 h-4" />
            Hang AP
          </Link>
          <Link
            href="/insights"
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <Search className="w-4 h-4" />
            Insights
          </Link>
          <Link
            href="/history"
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <History className="w-4 h-4" />
            History
          </Link>
          <button
            onClick={() => mutate()}
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <ThemeToggle />
        </div>
      </header>

      {/* Tab bar */}
      <div className="flex gap-1 mb-6 border-b" style={{ borderColor: "var(--chart-grid)" }}>
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className="flex items-center gap-1.5 px-4 py-2 text-sm transition-colors -mb-px border-b-2"
            style={{
              borderColor: activeTab === tab ? "var(--cyan)" : "transparent",
              color: activeTab === tab ? "var(--cyan)" : "var(--text-muted)",
            }}
          >
            {tab === "Floor Map" && <Map className="w-3.5 h-3.5" />}
            {tab}
          </button>
        ))}
      </div>

      {/* Always mounted — visibility controlled by CSS to preserve state */}
      <div className={activeTab === "Floor Map" ? "block" : "hidden"}>
        <FloorMapTab ref={floorMapRef} />
      </div>

      <div className={activeTab === "Site Overview" ? "block" : "hidden"}>
        <div className="grid grid-cols-2 gap-4 mb-8 max-w-sm">
          <div
            className="border rounded-lg p-4 flex items-center gap-3"
            style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
          >
            <Wifi className="w-6 h-6" style={{ color: "var(--green)" }} />
            <div>
              <p className="text-3xl font-bold" style={{ color: "var(--text-primary)" }}>
                {totalOnline}
              </p>
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>Online APs</p>
            </div>
          </div>
          <div
            className="border rounded-lg p-4 flex items-center gap-3"
            style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
          >
            <WifiOff className="w-6 h-6" style={{ color: "var(--red)" }} />
            <div>
              <p className="text-3xl font-bold" style={{ color: "var(--text-primary)" }}>
                {totalAps - totalOnline}
              </p>
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>Offline APs</p>
            </div>
          </div>
        </div>

        {isLoading && (
          <div className="flex justify-center py-20">
            <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>
              Loading sites...
            </div>
          </div>
        )}

        {!isLoading && sites && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {sites.map((site) => (
              <SiteCard key={site.id} site={site} />
            ))}
          </div>
        )}

        {!isLoading && sites?.length === 0 && (
          <div className="text-center py-20" style={{ color: "var(--text-muted)" }}>
            No sites found. Check your API token and Org ID.
          </div>
        )}
      </div>

    </main>
  );
}
