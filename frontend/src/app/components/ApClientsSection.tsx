"use client";

import { ChevronUp, ChevronDown, ChevronsUpDown, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import clsx from "clsx";
import { fetchApClients, fetchSettings, ClientInfo } from "@/lib/api";

// ── Formatters ──────────────────────────────────────────────────────────────

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "-";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function formatUptime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "-";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function bandLabel(band: string | null | undefined): string {
  switch (String(band)) {
    case "24": return "2.4GHz";
    case "5":  return "5GHz";
    case "6":  return "6GHz";
    default:   return band ? String(band) : "-";
  }
}

function rssiColor(rssi: number | null | undefined): string {
  if (rssi === null || rssi === undefined) return "var(--text-muted)";
  if (rssi >= -60) return "var(--green)";
  if (rssi >= -70) return "var(--yellow, #facc15)";
  return "var(--red)";
}

function snrColor(snr: number | null | undefined): string {
  if (snr === null || snr === undefined) return "var(--text-muted)";
  if (snr >= 25) return "var(--green)";
  if (snr >= 15) return "var(--yellow, #facc15)";
  return "var(--red)";
}

function osFamily(c: ClientInfo): string {
  const parts = [c.family, c.os, c.model].filter(Boolean);
  return parts.length ? parts.join(" / ") : "-";
}

function num(v: unknown): string {
  return v === null || v === undefined ? "-" : String(v);
}

// ── Sort ──────────────────────────────────────────────────────────────────────

type SortKey =
  | "name" | "manufacture" | "osfamily" | "band" | "channel"
  | "rssi" | "snr" | "tx_rate" | "rx_rate" | "txrx" | "uptime"
  | "ssid" | "vlan_id" | "key_mgmt";

type SortDir = "asc" | "desc";

function getSortValue(c: ClientInfo, key: SortKey): string | number | null {
  switch (key) {
    case "name":        return (c.hostname || c.mac || "").toString();
    case "manufacture": return c.manufacture || "";
    case "osfamily":    return osFamily(c);
    case "band":        return c.band ? Number(c.band) : null;
    case "channel":     return c.channel ?? null;
    case "rssi":        return c.rssi ?? null;
    case "snr":         return c.snr ?? null;
    case "tx_rate":     return c.tx_rate ?? null;
    case "rx_rate":     return c.rx_rate ?? null;
    case "txrx":        return (c.tx_bytes ?? 0) + (c.rx_bytes ?? 0);
    case "uptime":      return c.uptime ?? null;
    case "ssid":        return c.ssid || "";
    case "vlan_id":     return c.vlan_id != null ? String(c.vlan_id) : "";
    case "key_mgmt":    return c.key_mgmt || "";
    default:            return null;
  }
}

type ColDef = { label: string; sortKey: SortKey };

const COLUMNS: ColDef[] = [
  { label: "Hostname / MAC", sortKey: "name" },
  { label: "Manufacturer",   sortKey: "manufacture" },
  { label: "OS / Family",    sortKey: "osfamily" },
  { label: "Band",           sortKey: "band" },
  { label: "Channel",        sortKey: "channel" },
  { label: "RSSI",           sortKey: "rssi" },
  { label: "SNR",            sortKey: "snr" },
  { label: "TX Rate",        sortKey: "tx_rate" },
  { label: "RX Rate",        sortKey: "rx_rate" },
  { label: "TX / RX",        sortKey: "txrx" },
  { label: "Uptime",         sortKey: "uptime" },
  { label: "SSID",           sortKey: "ssid" },
  { label: "VLAN",           sortKey: "vlan_id" },
  { label: "Auth",           sortKey: "key_mgmt" },
];

// ── Component ───────────────────────────────────────────────────────────────

export default function ApClientsSection({ apId, siteId }: { apId: string; siteId: string }) {
  const router = useRouter();
  const { data: settings } = useSWR("settings", fetchSettings);
  const refreshInterval = (settings?.client_polling_interval_seconds ?? 600) * 1000;

  const { data: clients, isLoading, isValidating, mutate } = useSWR<ClientInfo[]>(
    `ap-clients-${apId}`,
    () => fetchApClients(apId),
    { refreshInterval },
  );

  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "rssi", dir: "desc" });

  const sorted = useMemo(() => {
    return [...(clients ?? [])].sort((a, b) => {
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
  }, [clients, sort]);

  const handleSort = (key: SortKey) => {
    setSort((prev) =>
      prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }
    );
  };

  return (
    <section className="border rounded-lg p-5" style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-display font-semibold tracking-wider" style={{ color: "var(--cyan)" }}>
          CONNECTED CLIENTS ({clients?.length ?? 0})
        </h2>
        <button
          onClick={() => mutate()}
          className="flex items-center gap-2 px-3 py-1.5 border rounded-lg text-sm transition-all"
          style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
        >
          <RefreshCw className={`w-4 h-4 ${isValidating ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {isLoading && !clients ? (
        <div className="flex justify-center py-10">
          <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading clients...</div>
        </div>
      ) : sorted.length === 0 ? (
        <p className="text-center py-10" style={{ color: "var(--text-muted)" }}>
          接続中のクライアントはいません
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm font-mono border-collapse">
            <thead>
              <tr className="border-b" style={{ borderColor: "var(--border-cyan)" }}>
                {COLUMNS.map((col) => (
                  <th
                    key={col.label}
                    onClick={() => handleSort(col.sortKey)}
                    className={clsx("text-left py-3 px-3 font-normal whitespace-nowrap select-none cursor-pointer hover:opacity-80")}
                    style={{ color: sort.key === col.sortKey ? "var(--cyan)" : "var(--text-muted)" }}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {sort.key === col.sortKey ? (
                        sort.dir === "asc" ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />
                      ) : (
                        <ChevronsUpDown className="w-3 h-3 opacity-40" />
                      )}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((c) => (
                <tr
                  key={c.mac}
                  className="border-b transition-colors cursor-pointer"
                  style={{ borderColor: "var(--chart-grid)" }}
                  onClick={() => router.push(`/sites/${siteId}/clients/${c.mac}`)}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}
                >
                  <td className="py-2 px-3" style={{ color: "var(--cyan)" }}>{c.hostname || c.mac}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.manufacture || "-"}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{osFamily(c)}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{bandLabel(c.band)}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{num(c.channel)}</td>
                  <td className="py-2 px-3 font-bold" style={{ color: rssiColor(c.rssi) }}>
                    {c.rssi != null ? `${c.rssi} dBm` : "-"}
                  </td>
                  <td className="py-2 px-3 font-bold" style={{ color: snrColor(c.snr) }}>
                    {c.snr != null ? `${c.snr} dB` : "-"}
                  </td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>
                    {c.tx_rate != null ? `${c.tx_rate} Mbps` : "-"}
                  </td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>
                    {c.rx_rate != null ? `${c.rx_rate} Mbps` : "-"}
                  </td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>
                    {formatBytes(c.tx_bytes)} / {formatBytes(c.rx_bytes)}
                  </td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{formatUptime(c.uptime)}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.ssid || "-"}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{num(c.vlan_id)}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.key_mgmt || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
