"use client";

import { ArrowLeft, Home } from "lucide-react";
import Link from "next/link";
import SaveNowButton from "@/app/components/SaveNowButton";
import PollNowButton from "@/app/components/PollNowButton";
import { useState } from "react";
import useSWR from "swr";
import { Line } from "recharts";
import { fetchApMetrics, fetchApRadioConfig, ApMetric, ApRadioConfig } from "@/lib/api";
import { toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";
import clsx from "clsx";
import {
  CHANGE_FIELD_COLORS, SourceBadge, MetricCard, CurrentConfigSnapshot,
  RadioBandPanel, ChartSection,
} from "@/app/components/ApDetailShared";

type HourRange = 1 | 6 | 24 | 72;

export default function ApDetailPage({
  params,
}: {
  params: { siteId: string; apId: string };
}) {
  const { siteId, apId } = params;
  const { timezone } = useTimezone();
  const [hours, setHours] = useState<HourRange>(24);
  const [bandTab, setBandTab] = useState<"24" | "5" | "6">("24");

  const { data: metrics, mutate: mutateMetrics } = useSWR<ApMetric[]>(
    `ap-metrics-${apId}-${hours}`,
    () => fetchApMetrics(apId, hours)
  );

  const { data: radioConfig } = useSWR(
    `ap-radio-${apId}`,
    () => fetchApRadioConfig(apId, siteId)
  );

  const latestMetric = metrics && metrics.length > 0 ? metrics[metrics.length - 1] : null;

  const chartData = metrics?.map((m) => ({
    timestamp: m.timestamp,
    clients: m.num_clients,
    util24: m.radio_24_utilization,
    util5: m.radio_5_utilization,
    util6: m.radio_6_utilization,
    util24_tx: m.radio_24_util_tx,
    util24_rx: m.radio_24_util_rx_in_bss,
    util24_nw: m.radio_24_util_non_wifi,
    util5_tx: m.radio_5_util_tx,
    util5_rx: m.radio_5_util_rx_in_bss,
    util5_nw: m.radio_5_util_non_wifi,
    util6_tx: m.radio_6_util_tx,
    util6_rx: m.radio_6_util_rx_in_bss,
    util6_nw: m.radio_6_util_non_wifi,
    nf24: m.radio_24_noise_floor,
    nf5: m.radio_5_noise_floor,
    nf6: m.radio_6_noise_floor,
    tx24: m.radio_24_tx_power,
    tx5: m.radio_5_tx_power,
    tx6: m.radio_6_tx_power,
    ch24: m.radio_24_channel,
    ch5: m.radio_5_channel,
    ch6: m.radio_6_channel,
    bw24: m.radio_24_bandwidth,
    bw5: m.radio_5_bandwidth,
    bw6: m.radio_6_bandwidth,
  })) ?? [];

  const changes = radioConfig?.changes ?? [];

  const bandData = (band: "24" | "5" | "6") => {
    const c = radioConfig?.current;
    if (!c) return null;
    return band === "24" ? c.band_24 : band === "5" ? c.band_5 : c.band_6;
  };

  const bandSource = (band: "24" | "5" | "6") => {
    const c = radioConfig?.current;
    if (!c) return null;
    return band === "24" ? c.config_source_24 : band === "5" ? c.config_source_5 : c.config_source_6;
  };

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
              AP Detail
            </h1>
            <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
              {radioConfig?.current?.ap_name || apId}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <PollNowButton onSuccess={() => mutateMetrics()} />
          <SaveNowButton />
        </div>
      </header>

      {/* Section 1: リアルタイム概要 */}
      <section className={sectionClass} style={sectionStyle}>
        <h2 className="text-sm font-display font-semibold mb-4 tracking-wider" style={{ color: "var(--cyan)" }}>
          REALTIME STATUS
        </h2>
        {latestMetric ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <MetricCard label="Connected Clients" value={latestMetric.num_clients} />
              <MetricCard label="2.4G Channel" value={latestMetric.radio_24_channel} />
              <MetricCard label="5G Channel" value={latestMetric.radio_5_channel} />
              <MetricCard label="6G Channel" value={latestMetric.radio_6_channel} />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <MetricCard label="2.4G Bandwidth" value={latestMetric.radio_24_bandwidth} unit=" MHz" />
              <MetricCard label="5G Bandwidth" value={latestMetric.radio_5_bandwidth} unit=" MHz" />
              <MetricCard label="6G Bandwidth" value={latestMetric.radio_6_bandwidth} unit=" MHz" />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <MetricCard label="2.4G Tx Power" value={latestMetric.radio_24_tx_power} unit=" dBm" />
              <MetricCard label="5G Tx Power" value={latestMetric.radio_5_tx_power} unit=" dBm" />
              <MetricCard label="6G Tx Power" value={latestMetric.radio_6_tx_power} unit=" dBm" />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <MetricCard label="2.4G Utilization" value={latestMetric.radio_24_utilization} unit="%" />
              <MetricCard label="5G Utilization" value={latestMetric.radio_5_utilization} unit="%" />
              <MetricCard label="6G Utilization" value={latestMetric.radio_6_utilization} unit="%" />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <MetricCard label="2.4G Noise Floor" value={latestMetric.radio_24_noise_floor} unit=" dBm" />
              <MetricCard label="5G Noise Floor" value={latestMetric.radio_5_noise_floor} unit=" dBm" />
              <MetricCard label="6G Noise Floor" value={latestMetric.radio_6_noise_floor} unit=" dBm" />
            </div>
          </div>
        ) : (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            データ収集中... (初回ポーリングをお待ちください)
          </p>
        )}
      </section>

      {/* Section 2: 推移グラフ */}
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

        {chartData.length === 0 ? (
          <div className="h-40 flex items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>
            データ収集中...
          </div>
        ) : (
          <div className="space-y-6">
            <ChartSection title="Connected Clients" data={chartData} changes={changes} hours={hours}>
              <Line type="monotone" dataKey="clients" stroke="var(--cyan)" dot={false} strokeWidth={2} name="Clients" />
            </ChartSection>

            <ChartSection title="Channel Utilization (%)" data={chartData} changes={changes} hours={hours}>
              <Line type="monotone" dataKey="util24" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="2.4G%" />
              <Line type="monotone" dataKey="util5" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="5G%" />
              <Line type="monotone" dataKey="util6" stroke="var(--green)" dot={false} strokeWidth={1.5} name="6G%" />
            </ChartSection>

            <ChartSection title="Utilization Breakdown 2.4G (%)" data={chartData} changes={changes} hours={hours}>
              <Line type="monotone" dataKey="util24_tx" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="2.4G TX" />
              <Line type="monotone" dataKey="util24_rx" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="2.4G RX in BSS" />
              <Line type="monotone" dataKey="util24_nw" stroke="var(--yellow)" dot={false} strokeWidth={1.5} name="2.4G Non-WiFi" />
            </ChartSection>

            <ChartSection title="Utilization Breakdown 5G (%)" data={chartData} changes={changes} hours={hours}>
              <Line type="monotone" dataKey="util5_tx" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="5G TX" />
              <Line type="monotone" dataKey="util5_rx" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="5G RX in BSS" />
              <Line type="monotone" dataKey="util5_nw" stroke="var(--yellow)" dot={false} strokeWidth={1.5} name="5G Non-WiFi" />
            </ChartSection>

            <ChartSection title="Utilization Breakdown 6G (%)" data={chartData} changes={changes} hours={hours}>
              <Line type="monotone" dataKey="util6_tx" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="6G TX" />
              <Line type="monotone" dataKey="util6_rx" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="6G RX in BSS" />
              <Line type="monotone" dataKey="util6_nw" stroke="var(--yellow)" dot={false} strokeWidth={1.5} name="6G Non-WiFi" />
            </ChartSection>

            <ChartSection title="Noise Floor (dBm)" data={chartData} changes={changes} hours={hours}>
              <Line type="monotone" dataKey="nf24" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="2.4G NF" />
              <Line type="monotone" dataKey="nf5" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="5G NF" />
              <Line type="monotone" dataKey="nf6" stroke="var(--green)" dot={false} strokeWidth={1.5} name="6G NF" />
            </ChartSection>

            <ChartSection title="Tx Power (dBm)" data={chartData} changes={changes} hours={hours}>
              <Line type="monotone" dataKey="tx24" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="2.4G TxP" />
              <Line type="monotone" dataKey="tx5" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="5G TxP" />
              <Line type="monotone" dataKey="tx6" stroke="var(--green)" dot={false} strokeWidth={1.5} name="6G TxP" />
            </ChartSection>

            <ChartSection title="Channel Number" data={chartData} changes={changes} hours={hours}>
              <Line type="stepAfter" dataKey="ch24" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="2.4G Ch" />
              <Line type="stepAfter" dataKey="ch5" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="5G Ch" />
              <Line type="stepAfter" dataKey="ch6" stroke="var(--green)" dot={false} strokeWidth={1.5} name="6G Ch" />
            </ChartSection>

            <ChartSection
              title="Channel Bandwidth (MHz)"
              data={chartData}
              changes={changes}
              hours={hours}
              yTicks={[0, 20, 40, 80, 160]}
            >
              <Line type="stepAfter" dataKey="bw24" stroke="var(--cyan)" dot={false} strokeWidth={1.5} name="2.4G BW" />
              <Line type="stepAfter" dataKey="bw5" stroke="var(--purple)" dot={false} strokeWidth={1.5} name="5G BW" />
              <Line type="stepAfter" dataKey="bw6" stroke="var(--green)" dot={false} strokeWidth={1.5} name="6G BW" />
            </ChartSection>

            {changes.length > 0 && (
              <div
                className="flex flex-wrap gap-3 pt-2 border-t"
                style={{ borderColor: "var(--chart-grid)" }}
              >
                <p className="text-xs w-full" style={{ color: "var(--text-muted)" }}>Config Change Markers:</p>
                {Object.entries(CHANGE_FIELD_COLORS).map(([field, color]) => (
                  <span key={field} className="flex items-center gap-1 text-xs font-mono" style={{ color }}>
                    <span className="w-6 border-t-2" style={{ borderColor: color, borderStyle: "dashed" }} />
                    {field}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* Section 3: Radio設定パネル */}
      <section className={sectionClass} style={sectionStyle}>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-display font-semibold tracking-wider" style={{ color: "var(--cyan)" }}>
            RADIO CONFIG
          </h2>
          {radioConfig?.current?.config_source && (
            <SourceBadge level={radioConfig.current.config_source} />
          )}
        </div>

        {(radioConfig?.current?.deviceprofile_name || radioConfig?.current?.rftemplate_name) && (
          <div className="flex gap-4 mb-3 text-xs font-mono" style={{ color: "var(--text-muted)" }}>
            {radioConfig.current.deviceprofile_name && (
              <span>Device Profile: <span style={{ color: "#7c3aed" }}>{radioConfig.current.deviceprofile_name}</span></span>
            )}
            {radioConfig.current.rftemplate_name && (
              <span>RF Template: <span style={{ color: "#3b82f6" }}>{radioConfig.current.rftemplate_name}</span></span>
            )}
          </div>
        )}

        <div className="flex gap-2 mb-4">
          {(["24", "5", "6"] as const).map((b) => (
            <button
              key={b}
              onClick={() => setBandTab(b)}
              className="px-3 py-1 rounded text-sm font-mono border transition-all"
              style={
                bandTab === b
                  ? { borderColor: "var(--purple)", color: "var(--purple)", backgroundColor: "rgba(124,58,237,0.1)" }
                  : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
              }
            >
              {b === "24" ? "2.4 GHz" : b === "5" ? "5 GHz" : "6 GHz"}
            </button>
          ))}
        </div>

        <RadioBandPanel
          data={bandData(bandTab) as Record<string, unknown> | null}
          configSource={bandSource(bandTab)}
        />
      </section>

      {/* Section 4: 変更履歴テーブル */}
      <section className={sectionClass} style={sectionStyle}>
        <h2 className="text-sm font-display font-semibold mb-4 tracking-wider" style={{ color: "var(--cyan)" }}>
          CONFIG CHANGE HISTORY
        </h2>
        {changes.length === 0 ? (
          <CurrentConfigSnapshot radioConfig={radioConfig?.current ?? null} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm font-mono border-collapse">
              <thead>
                <tr className="border-b" style={{ borderColor: "var(--border-cyan)" }}>
                  {["Detected At", "Band", "Field", "Old → New", "Source Change"].map((h) => (
                    <th key={h} className="text-left py-3 px-3 font-normal" style={{ color: "var(--text-muted)" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {changes.map((c) => (
                  <tr key={c.id} className="border-b transition-colors" style={{ borderColor: "var(--chart-grid)" }}>
                    <td className="py-3 px-3 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {toLocalString(c.detected_at, timezone)}
                    </td>
                    <td className="py-3 px-3" style={{ color: "var(--text-primary)" }}>
                      {c.band}
                    </td>
                    <td className="py-2 px-3">
                      <span
                        className="px-2 py-0.5 rounded text-xs font-mono border"
                        style={{
                          color: CHANGE_FIELD_COLORS[c.changed_field] ?? "var(--text-primary)",
                          borderColor: CHANGE_FIELD_COLORS[c.changed_field] ?? "var(--chart-grid)",
                          backgroundColor: `${CHANGE_FIELD_COLORS[c.changed_field] ?? "#fff"}15`,
                        }}
                      >
                        {c.changed_field}
                      </span>
                    </td>
                    <td className="py-3 px-3" style={{ color: "var(--text-primary)" }}>
                      {c.old_value ?? "-"} → {c.new_value ?? "-"}
                    </td>
                    <td className="py-3 px-3" style={{ color: "var(--text-secondary)" }}>
                      {c.old_source || c.new_source
                        ? `${c.old_source ?? "-"} → ${c.new_source ?? "-"}`
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
