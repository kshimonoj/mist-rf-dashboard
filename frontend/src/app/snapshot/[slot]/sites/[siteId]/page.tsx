"use client";

import { ArrowLeft, Home, Camera } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { fetchSnapshotSiteAps, ApInfo } from "@/lib/api";
import clsx from "clsx";
import ThemeToggle from "@/app/components/ThemeToggle";

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

export default function SnapshotSiteApPage({
  params,
}: {
  params: { slot: string; siteId: string };
}) {
  const slot = Number(params.slot);
  const { siteId } = params;

  const { data: aps, isLoading } = useSWR<ApInfo[]>(
    `snapshot-${slot}-site-${siteId}-aps`,
    () => fetchSnapshotSiteAps(slot, siteId),
  );

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
          <Link href={`/snapshot/${slot}`}
            className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}>
            <ArrowLeft className="w-4 h-4" />
            Back
          </Link>
          <div className="ml-1">
            <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
              AP List
            </h1>
            <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>{siteId}</p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      {/* スナップショットバナー */}
      <div className="border rounded-lg px-4 py-2 mb-6 flex items-center gap-2"
        style={{ borderColor: "var(--purple)", backgroundColor: "rgba(124,58,237,0.08)" }}>
        <Camera className="w-4 h-4" style={{ color: "var(--purple)" }} />
        <span className="text-xs font-mono" style={{ color: "var(--purple)" }}>
          Snapshot Slot {slot} — 閲覧モード
        </span>
      </div>

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
                {["Status", "AP Name", "MAC", "Model", "IP", "Uptime", "Clients",
                  "2.4G Ch", "2.4G Util%", "5G Ch", "5G Util%", "6G Ch", "6G Util%"].map((h) => (
                  <th
                    key={h}
                    className="text-left py-3 px-3 font-normal whitespace-nowrap"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {aps.map((ap) => (
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
                      href={`/snapshot/${slot}/sites/${siteId}/aps/${ap.id}`}
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
              APが見つかりません
            </p>
          )}
        </div>
      )}
    </main>
  );
}
