"use client";

import { RefreshCw, Wifi, WifiOff, Activity, Play, Settings, Tag as TagIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { fetchSites, SiteInfo } from "@/lib/api";
import ThemeToggle from "./components/ThemeToggle";
import SaveNowButton from "./components/SaveNowButton";
import PollNowButton from "./components/PollNowButton";
import SettingsButton from "./components/SettingsModal";
import SnapshotButton from "./components/SnapshotModal";
import Dropdown from "./components/Dropdown";
import TabNav, { HomeTab } from "./components/TabNav";
import FloorMapTab, { FloorMapTabHandle } from "./components/FloorMapTab";

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
  const [activeTab, setActiveTab] = useState<HomeTab>("Site Overview");
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const floorMapRef = useRef<FloorMapTabHandle>(null);

  // 他ページの Floor Map タブから "/?tab=floormap" で遷移してきた場合、初期表示を合わせる
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("tab") === "floormap") setActiveTab("Floor Map");
  }, []);

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
            Mist RF Dashboard
          </h1>
          <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
            HPE Mist AP 監視
          </p>
        </div>
        <div className="flex items-center gap-2 flex-nowrap">
          <div className="text-right mr-2 whitespace-nowrap">
            <p className="text-xs whitespace-nowrap" style={{ color: "var(--text-muted)" }}>Last updated</p>
            <p className="text-xs font-mono whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
              {lastUpdated.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </p>
          </div>
          <button
            onClick={() => mutate()}
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all whitespace-nowrap"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <Dropdown label="実行" icon={<Play className="w-4 h-4" />}>
            {() => (
              <>
                <PollNowButton asMenuItem onSuccess={() => mutate()} />
                <SaveNowButton asMenuItem getFloorMapRows={() => floorMapRef.current?.getRows() ?? null} />
                <SnapshotButton asMenuItem />
              </>
            )}
          </Dropdown>
          <Dropdown ariaLabel="設定" icon={<Settings className="w-4 h-4" />}>
            {(close) => (
              <>
                <SettingsButton asMenuItem />
                <Link
                  href="/tags"
                  role="menuitem"
                  onClick={close}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors"
                  style={{ color: "var(--cyan)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}
                >
                  <TagIcon className="w-4 h-4" />
                  Tags
                </Link>
              </>
            )}
          </Dropdown>
          <ThemeToggle />
        </div>
      </header>

      <TabNav homeTab={activeTab} onHomeTabChange={setActiveTab} />

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
