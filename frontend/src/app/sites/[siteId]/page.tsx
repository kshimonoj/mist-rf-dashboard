"use client";

import { ArrowLeft, Home, RefreshCw, ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { useState, useMemo } from "react";
import { fetchSiteAps, fetchSite, ApInfo, SiteSummary } from "@/lib/api";
import clsx from "clsx";
import ThemeToggle from "@/app/components/ThemeToggle";
import SleSection from "@/app/components/SleSection";

function formatUptime(seconds: number | null): string {
  if (!seconds) return "-";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function StatusBadge({ status }: { status: string }) {
  const isOnline = status === "connected";
  return (
    <span className={clsx("inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-sm font-mono")}>
      <span className={clsx("w-2 h-2 rounded-full", isOnline ? "pulse-green" : "pulse-red")} />
      <span style={{ color: isOnline ? "var(--green)" : "var(--red)" }}>
        {isOnline ? "ONLINE" : "OFFLINE"}
      </span>
    </span>
  );
}

function RadioCell({ val, unit = "" }: { val: number | null; unit?: string }) {
  if (val === null || val === undefined) {
    return <span style={{ color: "var(--text-muted)" }}>-</span>;
  }
  return <span style={{ color: "var(--cyan)" }}>{val}{unit}</span>;
}

// ── Sort ──────────────────────────────────────────────────────────────────────

type SortKey =
  | "name" | "mac" | "model" | "ip" | "uptime" | "num_clients"
  | "radio_24_channel" | "radio_24_utilization"
  | "radio_5_channel" | "radio_5_utilization"
  | "radio_6_channel" | "radio_6_utilization";

type SortDir = "asc" | "desc";

function getSortValue(ap: ApInfo, key: SortKey): string | number | null {
  switch (key) {
    case "name":                  return ap.name || ap.mac;
    case "mac":                   return ap.mac;
    case "model":                 return ap.model;
    case "ip":                    return ap.ip || "";
    case "uptime":                return ap.uptime;
    case "num_clients":           return ap.num_clients;
    case "radio_24_channel":      return ap.radio_24?.channel ?? null;
    case "radio_24_utilization":  return ap.radio_24?.utilization ?? null;
    case "radio_5_channel":       return ap.radio_5?.channel ?? null;
    case "radio_5_utilization":   return ap.radio_5?.utilization ?? null;
    case "radio_6_channel":       return ap.radio_6?.channel ?? null;
    case "radio_6_utilization":   return ap.radio_6?.utilization ?? null;
    default:                      return null;
  }
}

type ColDef = { label: string; sortKey: SortKey | null };

const COLUMNS: ColDef[] = [
  { label: "Status",      sortKey: null },
  { label: "AP Name",     sortKey: "name" },
  { label: "MAC",         sortKey: "mac" },
  { label: "Model",       sortKey: "model" },
  { label: "IP",          sortKey: "ip" },
  { label: "Uptime",      sortKey: "uptime" },
  { label: "Clients",     sortKey: "num_clients" },
  { label: "2.4G Ch",     sortKey: "radio_24_channel" },
  { label: "2.4G Util%",  sortKey: "radio_24_utilization" },
  { label: "5G Ch",       sortKey: "radio_5_channel" },
  { label: "5G Util%",    sortKey: "radio_5_utilization" },
  { label: "6G Ch",       sortKey: "radio_6_channel" },
  { label: "6G Util%",    sortKey: "radio_6_utilization" },
];

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SitePage({ params }: { params: { siteId: string } }) {
  const { siteId } = params;
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({
    key: "name",
    dir: "asc",
  });

  const { data: aps, isLoading, mutate } = useSWR<ApInfo[]>(
    `site-aps-${siteId}`,
    () => fetchSiteAps(siteId),
    { refreshInterval: 300000 }
  );
  const { data: site } = useSWR<SiteSummary>(
    `site-${siteId}`,
    () => fetchSite(siteId),
  );

  const siteName = site?.name || siteId;

  const sortedAps = useMemo(() => {
    if (!aps) return [];
    return [...aps].sort((a, b) => {
      const va = getSortValue(a, sort.key);
      const vb = getSortValue(b, sort.key);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const cmp =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb));
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [aps, sort]);

  const handleSort = (key: SortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" }
    );
  };

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}
          >
            <Home className="w-4 h-4" />
            Home
          </Link>
          <span style={{ color: "var(--chart-grid)" }}>|</span>
          <Link
            href="/"
            className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}
          >
            <ArrowLeft className="w-4 h-4" />
            Back
          </Link>
          <div className="ml-1">
            <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
              {siteName} — AP List
            </h1>
            <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>{siteId}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => mutate()}
            className="flex items-center gap-2 px-4 py-2 border rounded-lg text-sm transition-all"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <ThemeToggle />
        </div>
      </header>

      <SleSection mode="site" id={siteId} />

      {isLoading && (
        <div className="flex justify-center py-20">
          <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading APs...</div>
        </div>
      )}

      {!isLoading && aps && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm font-mono border-collapse">
            <thead>
              <tr className="border-b" style={{ borderColor: "var(--border-cyan)" }}>
                {COLUMNS.map((col) => (
                  <th
                    key={col.label}
                    onClick={col.sortKey ? () => handleSort(col.sortKey!) : undefined}
                    className={clsx(
                      "text-left py-3 px-3 font-normal whitespace-nowrap select-none",
                      col.sortKey && "cursor-pointer hover:opacity-80"
                    )}
                    style={{
                      color: col.sortKey && sort.key === col.sortKey
                        ? "var(--cyan)"
                        : "var(--text-muted)",
                    }}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {col.sortKey && (
                        sort.key === col.sortKey ? (
                          sort.dir === "asc"
                            ? <ChevronUp className="w-3 h-3" />
                            : <ChevronDown className="w-3 h-3" />
                        ) : (
                          <ChevronsUpDown className="w-3 h-3 opacity-40" />
                        )
                      )}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedAps.map((ap) => (
                <tr
                  key={ap.id}
                  className="border-b transition-colors"
                  style={{ borderColor: "var(--chart-grid)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}
                >
                  <td className="py-3 px-3">
                    <StatusBadge status={ap.status} />
                  </td>
                  <td className="py-3 px-3">
                    <Link
                      href={`/sites/${siteId}/aps/${ap.id}`}
                      className="hover:underline"
                      style={{ color: "var(--cyan)" }}
                    >
                      {ap.name || ap.mac}
                    </Link>
                  </td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{ap.mac}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{ap.model}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{ap.ip || "-"}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{formatUptime(ap.uptime)}</td>
                  <td className="py-2 px-3 font-bold" style={{ color: "var(--text-primary)" }}>{ap.num_clients}</td>
                  <td className="py-3 px-3"><RadioCell val={ap.radio_24?.channel} /></td>
                  <td className="py-3 px-3"><RadioCell val={ap.radio_24?.utilization} unit="%" /></td>
                  <td className="py-3 px-3"><RadioCell val={ap.radio_5?.channel} /></td>
                  <td className="py-3 px-3"><RadioCell val={ap.radio_5?.utilization} unit="%" /></td>
                  <td className="py-3 px-3"><RadioCell val={ap.radio_6?.channel} /></td>
                  <td className="py-3 px-3"><RadioCell val={ap.radio_6?.utilization} unit="%" /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {aps.length === 0 && (
            <p className="text-center py-10" style={{ color: "var(--text-muted)" }}>
              No APs found for this site.
            </p>
          )}
        </div>
      )}
    </main>
  );
}
