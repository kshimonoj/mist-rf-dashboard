"use client";

import { ArrowLeft, Home } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend,
} from "recharts";
import {
  fetchSiteClients, fetchClientMetrics, ClientInfo, ClientMetric,
} from "@/lib/api";
import { toLocalString, toLocalTimeShort, toLocalDateTimeShort } from "@/lib/time";
import { useTimezone } from "@/app/providers";
import clsx from "clsx";

type HourRange = 1 | 6 | 24 | 72;

function bandLabel(band: string | null | undefined): string {
  switch (String(band)) {
    case "24": return "2.4GHz";
    case "5":  return "5GHz";
    case "6":  return "6GHz";
    default:   return band ? String(band) : "-";
  }
}

function bandToNum(band: string | null | undefined): number | null {
  switch (String(band)) {
    case "24": return 2.4;
    case "5":  return 5;
    case "6":  return 6;
    default:   return null;
  }
}

// ── Info card ─────────────────────────────────────────────────────────────────

function InfoItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="border rounded-lg p-4" style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}>
      <p className="text-sm mb-1" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="text-base font-bold font-mono break-all" style={{ color: "var(--cyan)" }}>
        {value !== null && value !== undefined && value !== "" ? value : "-"}
      </p>
    </div>
  );
}

// ── Tooltip ──────────────────────────────────────────────────────────────────

function ChartTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string; unit?: string }>;
  label?: string;
}) {
  const { timezone } = useTimezone();
  if (!active || !payload?.length) return null;
  return (
    <div className="border rounded-lg p-3 text-xs font-mono"
      style={{ backgroundColor: "var(--bg-card)", borderColor: "var(--border-cyan)" }}>
      <p className="mb-2" style={{ color: "var(--text-secondary)" }}>
        {label ? toLocalString(label, timezone) : ""}
      </p>
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: {p.value ?? "-"}{p.unit ?? ""}
        </p>
      ))}
    </div>
  );
}

// ── Chart wrapper ─────────────────────────────────────────────────────────────

function ChartBox({ title, data, hours, markers, children, rightAxis, yDomain }: {
  title: string;
  data: Record<string, unknown>[];
  hours: number;
  markers?: string[];
  children: React.ReactNode;
  rightAxis?: { domain?: [number | string, number | string] };
  yDomain?: [number | string, number | string];
}) {
  const { timezone } = useTimezone();
  const tickFmt = (v: string) =>
    hours >= 48 ? toLocalDateTimeShort(v, timezone) : toLocalTimeShort(v, timezone);

  return (
    <div>
      <p className="text-sm mb-2" style={{ color: "var(--text-muted)" }}>{title}</p>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
          <XAxis dataKey="timestamp" tickFormatter={tickFmt}
            tick={{ fill: "var(--text-muted)", fontSize: 10 }} axisLine={{ stroke: "var(--chart-grid)" }} />
          <YAxis yAxisId="left" tick={{ fill: "var(--text-muted)", fontSize: 10 }}
            axisLine={{ stroke: "var(--chart-grid)" }} domain={yDomain} />
          {rightAxis && (
            <YAxis yAxisId="right" orientation="right" tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              axisLine={{ stroke: "var(--chart-grid)" }} domain={rightAxis.domain} />
          )}
          <Tooltip content={<ChartTooltip />} />
          <Legend wrapperStyle={{ fontSize: 10, color: "var(--text-secondary)" }} />
          {children}
          {(markers ?? []).map((m, i) => (
            <ReferenceLine key={`${m}-${i}`} x={m} yAxisId="left"
              stroke="#ff8c00" strokeDasharray="4 2" />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ClientDetailPage({
  params,
}: {
  params: { siteId: string; clientMac: string };
}) {
  const { siteId, clientMac } = params;
  const [hours, setHours] = useState<HourRange>(24);

  // 現在のクライアント基本情報（ライブ一覧から該当MACを抽出）
  const { data: clients } = useSWR<ClientInfo[]>(
    `site-clients-${siteId}`,
    () => fetchSiteClients(siteId),
  );
  const client = useMemo(
    () => (clients ?? []).find((c) => (c.mac || "").toLowerCase() === clientMac.toLowerCase()),
    [clients, clientMac],
  );

  const { data: metrics, isLoading } = useSWR<ClientMetric[]>(
    `client-metrics-${clientMac}-${hours}`,
    () => fetchClientMetrics(clientMac, hours, siteId),
  );

  // ライブ情報が無い場合は最新メトリクスから AP名 / Band / Channel を補完
  const latestMetric = metrics && metrics.length > 0 ? metrics[metrics.length - 1] : null;

  const chartData = useMemo(
    () =>
      (metrics ?? []).map((m) => ({
        timestamp: m.timestamp,
        rssi: m.rssi,
        snr: m.snr,
        tx_rate: m.tx_rate,
        rx_rate: m.rx_rate,
        tx_bps: m.tx_bps,
        rx_bps: m.rx_bps,
        bandNum: bandToNum(m.band),
        channel: m.channel,
      })),
    [metrics],
  );

  // band / channel が変化したタイミング（ローミング / バンド切替）を検出
  const roamMarkers = useMemo(() => {
    const out: string[] = [];
    let prevBand: string | null | undefined;
    let prevCh: number | null | undefined;
    (metrics ?? []).forEach((m, i) => {
      if (i > 0 && (m.band !== prevBand || m.channel !== prevCh)) {
        out.push(m.timestamp);
      }
      prevBand = m.band;
      prevCh = m.channel;
    });
    return out;
  }, [metrics]);

  const hasData = chartData.length > 0;

  const title = client?.hostname || clientMac;
  const band = client?.band ?? latestMetric?.band;
  const channel = client?.channel ?? latestMetric?.channel;
  const apName = client?.ap_name ?? latestMetric?.ap_name;

  const sectionClass = "border rounded-lg p-5";
  const sectionStyle = { borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" };

  return (
    <main className="min-h-screen p-6 space-y-6">
      <header className="flex items-center justify-between gap-4">
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
            href={`/sites/${siteId}`}
            className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}
          >
            <ArrowLeft className="w-4 h-4" />
            Back
          </Link>
          <div className="ml-1">
            <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
              Client Detail
            </h1>
            <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
              {title}
            </p>
          </div>
        </div>
      </header>

      {/* Section 1: 基本情報 */}
      <section className={sectionClass} style={sectionStyle}>
        <h2 className="text-sm font-display font-semibold mb-4 tracking-wider" style={{ color: "var(--cyan)" }}>
          CLIENT INFO
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <InfoItem label="Hostname" value={client?.hostname} />
          <InfoItem label="MAC" value={client?.mac || clientMac} />
          <InfoItem label="Manufacturer" value={client?.manufacture} />
          <InfoItem label="OS / Family" value={[client?.os, client?.family].filter(Boolean).join(" / ")} />
          <InfoItem label="AP Name" value={apName} />
          <InfoItem label="Band / Channel" value={`${bandLabel(band)} / ${channel ?? "-"}`} />
          <InfoItem label="SSID" value={client?.ssid} />
          <InfoItem label="VLAN" value={client?.vlan_id != null ? String(client.vlan_id) : "-"} />
          <InfoItem label="Auth" value={client?.key_mgmt} />
        </div>
      </section>

      {/* Section 2: METRICS HISTORY */}
      <section className={sectionClass} style={sectionStyle}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-display font-semibold tracking-wider" style={{ color: "var(--cyan)" }}>
            METRICS HISTORY
          </h2>
          <div className="flex gap-2">
            {([1, 6, 24, 72] as HourRange[]).map((h) => (
              <button
                key={h}
                onClick={() => setHours(h)}
                className={clsx("px-3 py-1 rounded text-sm font-mono border transition-all")}
                style={
                  hours === h
                    ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.1)" }
                    : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
                }
              >
                {h}h
              </button>
            ))}
          </div>
        </div>

        {!hasData ? (
          <div className="h-40 flex items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>
            {isLoading ? "読み込み中..." : "データ収集中..."}
          </div>
        ) : (
          <div className="space-y-8">
            {/* RSSI / SNR */}
            <ChartBox title="RSSI (dBm) / SNR (dB)" data={chartData} hours={hours} rightAxis={{ domain: [0, "auto"] }}>
              <ReferenceLine yAxisId="left" y={-60} stroke="var(--green)" strokeDasharray="4 2"
                label={{ value: "-60", fill: "var(--green)", fontSize: 9, position: "insideTopLeft" }} />
              <ReferenceLine yAxisId="left" y={-70} stroke="var(--yellow, #facc15)" strokeDasharray="4 2"
                label={{ value: "-70", fill: "var(--yellow, #facc15)", fontSize: 9, position: "insideBottomLeft" }} />
              <Line yAxisId="left" type="monotone" dataKey="rssi" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="RSSI (dBm)" connectNulls />
              <Line yAxisId="right" type="monotone" dataKey="snr" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="SNR (dB)" connectNulls />
            </ChartBox>

            {/* TX / RX Rate */}
            <ChartBox title="TX / RX Rate (Mbps)" data={chartData} hours={hours}>
              <Line yAxisId="left" type="monotone" dataKey="tx_rate" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="TX Rate" connectNulls />
              <Line yAxisId="left" type="monotone" dataKey="rx_rate" stroke="var(--green)" dot={false} strokeWidth={1.5} name="RX Rate" connectNulls />
            </ChartBox>

            {/* TX / RX Throughput */}
            <ChartBox title="TX / RX Throughput (bps)" data={chartData} hours={hours}>
              <Line yAxisId="left" type="monotone" dataKey="tx_bps" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="TX bps" connectNulls />
              <Line yAxisId="left" type="monotone" dataKey="rx_bps" stroke="var(--green)" dot={false} strokeWidth={1.5} name="RX bps" connectNulls />
            </ChartBox>

            {/* Band / Channel */}
            <ChartBox
              title="Band / Channel（変化点 = ローミング / バンド切替）"
              data={chartData}
              hours={hours}
              markers={roamMarkers}
              yDomain={[0, 7]}
              rightAxis={{ domain: [0, "auto"] }}
            >
              <Line yAxisId="left" type="stepAfter" dataKey="bandNum" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="Band (GHz)" connectNulls />
              <Line yAxisId="right" type="stepAfter" dataKey="channel" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="Channel" connectNulls />
            </ChartBox>

            {roamMarkers.length > 0 && (
              <div className="flex items-center gap-2 text-xs font-mono pt-1" style={{ color: "var(--text-muted)" }}>
                <span className="w-6 border-t-2" style={{ borderColor: "#ff8c00", borderStyle: "dashed" }} />
                ローミング / バンド切替検知（{roamMarkers.length} 回）
              </div>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
