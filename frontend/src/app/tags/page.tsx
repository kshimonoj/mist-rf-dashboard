"use client";

import { ArrowLeft, Home, RefreshCw, Tag as TagIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import useSWR from "swr";
import clsx from "clsx";
import {
  fetchAllTags, fetchTagAps, fetchTagClients, ApInfo, ClientInfo,
} from "@/lib/api";
import ThemeToggle from "@/app/components/ThemeToggle";
import { TagBadge } from "@/app/components/TagCell";

type TagAp = ApInfo & { site_id?: string; tags?: string[] };
type TagClient = ClientInfo & { site_id?: string; tags?: string[] };

// ── formatters ──────────────────────────────────────────────────────────────

function formatUptime(seconds: number | null | undefined): string {
  if (!seconds) return "-";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "-";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
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

function StatusBadge({ status }: { status: string }) {
  const isOnline = status === "connected";
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-sm font-mono">
      <span className={clsx("w-2 h-2 rounded-full", isOnline ? "pulse-green" : "pulse-red")} />
      <span style={{ color: isOnline ? "var(--green)" : "var(--red)" }}>
        {isOnline ? "ONLINE" : "OFFLINE"}
      </span>
    </span>
  );
}

function RadioCell({ val, unit = "" }: { val: number | null | undefined; unit?: string }) {
  if (val === null || val === undefined) return <span style={{ color: "var(--text-muted)" }}>-</span>;
  return <span style={{ color: "var(--cyan)" }}>{val}{unit}</span>;
}

function TagList({ tags }: { tags?: string[] }) {
  if (!tags?.length) return <span style={{ color: "var(--text-muted)" }}>-</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {tags.map((t) => <TagBadge key={t} tag={t} />)}
    </div>
  );
}

const AP_COLUMNS = [
  "Status", "AP Name", "MAC", "Model", "IP", "Uptime", "Clients",
  "2.4G Ch", "2.4G Util%", "5G Ch", "5G Util%", "6G Ch", "6G Util%", "Tags",
];

const CLIENT_COLUMNS = [
  "Hostname / MAC", "Manufacturer", "OS / Family", "AP Name", "Band", "Channel",
  "Protocol", "RSSI", "SNR", "TX Rate", "RX Rate", "TX / RX", "Uptime", "SSID", "VLAN", "Auth", "Tags",
];

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TagsPage() {
  const router = useRouter();
  const [selected, setSelected] = useState<string[]>([]);

  const { data: allTags, isLoading: tagsLoading, mutate: mutateTags } =
    useSWR<string[]>("all-tags", fetchAllTags);

  const sortedKey = [...selected].sort().join(",");

  const { data: taggedAps, isLoading: apsLoading } = useSWR<TagAp[]>(
    selected.length ? `tag-aps-${sortedKey}` : null,
    async () => {
      const lists = await Promise.all(selected.map((t) => fetchTagAps(t) as Promise<TagAp[]>));
      const map = new Map<string, TagAp>();
      lists.flat().forEach((ap) => map.set(ap.id, ap));
      return Array.from(map.values());
    },
    { refreshInterval: 30000 },
  );

  const { data: taggedClients, isLoading: clientsLoading } = useSWR<TagClient[]>(
    selected.length ? `tag-clients-${sortedKey}` : null,
    async () => {
      const lists = await Promise.all(selected.map((t) => fetchTagClients(t) as Promise<TagClient[]>));
      const map = new Map<string, TagClient>();
      lists.flat().forEach((c) => map.set(c.mac, c));
      return Array.from(map.values());
    },
    { refreshInterval: 30000 },
  );

  const toggle = (tag: string) => {
    setSelected((prev) => prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]);
  };

  const sectionClass = "border rounded-lg p-5";
  const sectionStyle = { borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" };

  return (
    <main className="min-h-screen p-6 space-y-6">
      <header className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-1.5 text-sm transition-colors" style={{ color: "var(--text-muted)" }}>
            <Home className="w-4 h-4" />
            Home
          </Link>
          <span style={{ color: "var(--chart-grid)" }}>|</span>
          <Link href="/" className="flex items-center gap-1.5 text-sm transition-colors" style={{ color: "var(--text-muted)" }}>
            <ArrowLeft className="w-4 h-4" />
            Back
          </Link>
          <div className="ml-1">
            <h1 className="font-display font-bold text-2xl flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
              <TagIcon className="w-5 h-5" style={{ color: "var(--cyan)" }} />
              Tags
            </h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => mutateTags()}
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all"
            style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
          >
            <RefreshCw className={`w-4 h-4 ${tagsLoading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <ThemeToggle />
        </div>
      </header>

      {/* タグ選択 */}
      <section className={sectionClass} style={sectionStyle}>
        <h2 className="text-sm font-display font-semibold mb-3 tracking-wider" style={{ color: "var(--cyan)" }}>
          SELECT TAGS
        </h2>
        {tagsLoading ? (
          <p className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading tags...</p>
        ) : allTags && allTags.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {allTags.map((tag) => {
              const isSel = selected.includes(tag);
              return (
                <button
                  key={tag}
                  onClick={() => toggle(tag)}
                  className="px-3 py-1 rounded-full text-sm font-mono border transition-all"
                  style={
                    isSel
                      ? { borderColor: "var(--cyan)", color: "var(--bg-primary, #000)", backgroundColor: "var(--cyan)" }
                      : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
                  }
                >
                  {tag}
                </button>
              );
            })}
          </div>
        ) : (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            まだタグがありません。AP List / Client List の Tags 列から追加してください。
          </p>
        )}
      </section>

      {selected.length === 0 ? (
        <p className="text-center py-16 text-sm" style={{ color: "var(--text-muted)" }}>
          タグを選択すると、そのタグが付いた AP / Client が表示されます。
        </p>
      ) : (
        <>
          {/* タグ付き AP */}
          <section className={sectionClass} style={sectionStyle}>
            <h2 className="text-sm font-display font-semibold mb-4 tracking-wider" style={{ color: "var(--cyan)" }}>
              TAGGED APs ({taggedAps?.length ?? 0})
            </h2>
            {apsLoading && !taggedAps ? (
              <p className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading...</p>
            ) : taggedAps && taggedAps.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm font-mono border-collapse">
                  <thead>
                    <tr className="border-b" style={{ borderColor: "var(--border-cyan)" }}>
                      {AP_COLUMNS.map((h) => (
                        <th key={h} className="text-left py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {taggedAps.map((ap) => (
                      <tr key={ap.id} className="border-b transition-colors" style={{ borderColor: "var(--chart-grid)" }}
                        onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-hover)")}
                        onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}>
                        <td className="py-3 px-3"><StatusBadge status={ap.status} /></td>
                        <td className="py-3 px-3">
                          {ap.site_id ? (
                            <Link href={`/sites/${ap.site_id}/aps/${ap.id}`} className="hover:underline" style={{ color: "var(--cyan)" }}>
                              {ap.name || ap.mac}
                            </Link>
                          ) : (
                            <span style={{ color: "var(--cyan)" }}>{ap.name || ap.mac}</span>
                          )}
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
                        <td className="py-2 px-3"><TagList tags={ap.tags} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>該当する AP はありません。</p>
            )}
          </section>

          {/* タグ付き Client */}
          <section className={sectionClass} style={sectionStyle}>
            <h2 className="text-sm font-display font-semibold mb-4 tracking-wider" style={{ color: "var(--cyan)" }}>
              TAGGED CLIENTS ({taggedClients?.length ?? 0})
            </h2>
            {clientsLoading && !taggedClients ? (
              <p className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading...</p>
            ) : taggedClients && taggedClients.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm font-mono border-collapse">
                  <thead>
                    <tr className="border-b" style={{ borderColor: "var(--border-cyan)" }}>
                      {CLIENT_COLUMNS.map((h) => (
                        <th key={h} className="text-left py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {taggedClients.map((c) => (
                      <tr key={c.mac} className="border-b transition-colors cursor-pointer" style={{ borderColor: "var(--chart-grid)" }}
                        onClick={() => c.site_id && router.push(`/sites/${c.site_id}/clients/${c.mac}`)}
                        onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-hover)")}
                        onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}>
                        <td className="py-2 px-3" style={{ color: "var(--cyan)" }}>{c.hostname || c.mac}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.manufacture || "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{[c.family, c.os, c.model].filter(Boolean).join(" / ") || "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.ap_name || "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{bandLabel(c.band)}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.channel ?? "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.proto || "-"}</td>
                        <td className="py-2 px-3 font-bold" style={{ color: rssiColor(c.rssi) }}>{c.rssi != null ? `${c.rssi} dBm` : "-"}</td>
                        <td className="py-2 px-3 font-bold" style={{ color: snrColor(c.snr) }}>{c.snr != null ? `${c.snr} dB` : "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.tx_rate != null ? `${c.tx_rate} Mbps` : "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.rx_rate != null ? `${c.rx_rate} Mbps` : "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{formatBytes(c.tx_bytes)} / {formatBytes(c.rx_bytes)}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{formatUptime(c.uptime)}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.ssid || "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.vlan_id != null ? String(c.vlan_id) : "-"}</td>
                        <td className="py-2 px-3" style={{ color: "var(--text-secondary)" }}>{c.key_mgmt || "-"}</td>
                        <td className="py-2 px-3" onClick={(e) => e.stopPropagation()}><TagList tags={c.tags} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>該当する Client はありません。</p>
            )}
          </section>
        </>
      )}
    </main>
  );
}
