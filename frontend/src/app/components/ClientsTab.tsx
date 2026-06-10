"use client";

import { ChevronUp, ChevronDown, ChevronsUpDown, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import clsx from "clsx";
import { fetchSiteClients, fetchSettings, fetchClientTags, putClientTag, ClientInfo, ClientTagEntry } from "@/lib/api";
import TagCell from "./TagCell";

function normMac(mac: string | null | undefined): string {
  return (mac || "").replace(/[:-]/g, "").toLowerCase();
}

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
  | "name" | "manufacture" | "osfamily" | "ap_name" | "band" | "channel"
  | "proto" | "rssi" | "snr" | "tx_rate" | "rx_rate" | "txrx" | "uptime"
  | "ssid" | "vlan_id" | "key_mgmt";

type SortDir = "asc" | "desc";

function getSortValue(c: ClientInfo, key: SortKey): string | number | null {
  switch (key) {
    case "name":        return (c.hostname || c.mac || "").toString();
    case "manufacture": return c.manufacture || "";
    case "osfamily":    return osFamily(c);
    case "ap_name":     return c.ap_name || "";
    case "band":        return c.band ? Number(c.band) : null;
    case "channel":     return c.channel ?? null;
    case "proto":       return c.proto || "";
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

type ColDef = { label: string; sortKey: SortKey | null };

const COLUMNS: ColDef[] = [
  { label: "Hostname / MAC", sortKey: "name" },
  { label: "Manufacturer",   sortKey: "manufacture" },
  { label: "OS / Family",    sortKey: "osfamily" },
  { label: "AP Name",        sortKey: "ap_name" },
  { label: "Band",           sortKey: "band" },
  { label: "Channel",        sortKey: "channel" },
  { label: "Protocol",       sortKey: "proto" },
  { label: "RSSI",           sortKey: "rssi" },
  { label: "SNR",            sortKey: "snr" },
  { label: "TX Rate",        sortKey: "tx_rate" },
  { label: "RX Rate",        sortKey: "rx_rate" },
  { label: "TX / RX",        sortKey: "txrx" },
  { label: "Uptime",         sortKey: "uptime" },
  { label: "SSID",           sortKey: "ssid" },
  { label: "VLAN",           sortKey: "vlan_id" },
  { label: "Auth",           sortKey: "key_mgmt" },
  { label: "Tags",           sortKey: null },
];

const BAND_FILTERS = [
  { label: "All", value: "all" },
  { label: "2.4GHz", value: "24" },
  { label: "5GHz", value: "5" },
  { label: "6GHz", value: "6" },
] as const;

// ── Component ───────────────────────────────────────────────────────────────

export default function ClientsTab({ siteId }: { siteId: string }) {
  const router = useRouter();
  const { data: settings } = useSWR("settings", fetchSettings);
  const refreshInterval = (settings?.client_polling_interval_seconds ?? 600) * 1000;

  const { data: clients, isLoading } = useSWR<ClientInfo[]>(
    `site-clients-${siteId}`,
    () => fetchSiteClients(siteId),
    { refreshInterval }
  );
  const { data: clientTags, mutate: mutateClientTags } = useSWR<ClientTagEntry[]>("client-tags", fetchClientTags);

  const clientTagMap = useMemo(() => {
    const m: Record<string, string[]> = {};
    (clientTags ?? []).forEach((t) => { m[t.mac] = t.tags; });
    return m;
  }, [clientTags]);

  const [search, setSearch] = useState("");
  const [bandFilter, setBandFilter] = useState<string>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "rssi", dir: "desc" });

  const filtered = useMemo(() => {
    if (!clients) return [];
    const q = search.toLowerCase().trim();
    return clients.filter((c) => {
      if (bandFilter !== "all" && String(c.band) !== bandFilter) return false;
      if (!q) return true;
      return (
        (c.hostname || "").toLowerCase().includes(q) ||
        (c.mac || "").toLowerCase().includes(q) ||
        (c.manufacture || "").toLowerCase().includes(q) ||
        (c.os || "").toLowerCase().includes(q) ||
        (c.family || "").toLowerCase().includes(q)
      );
    });
  }, [clients, search, bandFilter]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
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
  }, [filtered, sort]);

  const handleSort = (key: SortKey) => {
    setSort((prev) =>
      prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }
    );
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <h2 className="font-display font-bold text-lg" style={{ color: "var(--text-primary)" }}>
          Clients ({clients?.length ?? 0})
        </h2>

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5" style={{ color: "var(--text-muted)" }} />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="hostname / MAC / manufacturer / OS"
            className="w-64 pl-8 pr-3 py-1.5 rounded border bg-transparent text-xs font-mono"
            style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
          />
        </div>

        {/* Band filter */}
        <div className="flex gap-1">
          {BAND_FILTERS.map((bf) => (
            <button
              key={bf.value}
              onClick={() => setBandFilter(bf.value)}
              className="px-2.5 py-1 rounded border text-xs font-mono transition-all"
              style={{
                borderColor: bandFilter === bf.value ? "var(--cyan)" : "var(--chart-grid)",
                color: bandFilter === bf.value ? "var(--cyan)" : "var(--text-muted)",
                backgroundColor: bandFilter === bf.value ? "rgba(0,212,255,0.1)" : "transparent",
              }}
            >
              {bf.label}
            </button>
          ))}
        </div>

        {isLoading && (
          <span className="text-xs animate-pulse" style={{ color: "var(--cyan)" }}>Loading clients...</span>
        )}
      </div>

      {isLoading && !clients ? (
        <div className="flex justify-center py-20">
          <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading clients...</div>
        </div>
      ) : (
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
                      color: col.sortKey && sort.key === col.sortKey ? "var(--cyan)" : "var(--text-muted)",
                    }}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {col.sortKey && (
                        sort.key === col.sortKey ? (
                          sort.dir === "asc" ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />
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
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.ap_name || "-"}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{bandLabel(c.band)}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{num(c.channel)}</td>
                  <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.proto || "-"}</td>
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
                  <td className="py-2 px-3">
                    <TagCell
                      tags={clientTagMap[normMac(c.mac)] ?? []}
                      onSave={async (tagsStr) => { await putClientTag(c.mac, tagsStr); await mutateClientTags(); }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {sorted.length === 0 && (
            <p className="text-center py-10" style={{ color: "var(--text-muted)" }}>
              No clients found.
            </p>
          )}
        </div>
      )}

    </div>
  );
}
