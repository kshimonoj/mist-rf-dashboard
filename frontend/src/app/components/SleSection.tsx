"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetchSiteSle, fetchApSle, SleData, SleMetricData } from "@/lib/api";
import clsx from "clsx";

type Duration = "1h" | "6h" | "24h";

function scoreColor(score: number | null | undefined): string {
  if (score == null) return "var(--text-muted)";
  if (score >= 80) return "var(--green)";
  if (score >= 60) return "var(--yellow)";
  return "var(--red)";
}

const METRIC_DEFS: { key: keyof SleData; label: string }[] = [
  { key: "capacity",        label: "Capacity" },
  { key: "throughput",      label: "Throughput" },
  { key: "coverage",        label: "Coverage" },
  { key: "time_to_connect", label: "Time to Connect" },
  { key: "roaming",         label: "Roaming" },
  { key: "ap_availability", label: "AP Availability" },
];

const CLASSIFIERS: { key: keyof NonNullable<SleMetricData["classifiers"]>; label: string }[] = [
  { key: "wifi_interference",     label: "WiFi Interference" },
  { key: "non_wifi_interference", label: "Non-WiFi Interference" },
  { key: "client_count",          label: "Client Count" },
  { key: "client_usage",          label: "Client Usage" },
];

interface Props {
  mode: "site" | "ap";
  id: string;
}

export default function SleSection({ mode, id }: Props) {
  const [duration, setDuration] = useState<Duration>("1h");

  const { data, isLoading } = useSWR<SleData>(
    `sle-${mode}-${id}-${duration}`,
    () => (mode === "site" ? fetchSiteSle(id, duration) : fetchApSle(id, duration)),
    { refreshInterval: 600_000 }
  );

  return (
    <section
      className="border rounded-lg p-5"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2
          className="text-sm font-display font-semibold tracking-wider"
          style={{ color: "var(--cyan)" }}
        >
          SLE — SERVICE LEVEL EXPERIENCE
        </h2>
        <div className="flex gap-2">
          {(["1h", "6h", "24h"] as Duration[]).map((d) => (
            <button
              key={d}
              onClick={() => setDuration(d)}
              className={clsx("px-3 py-1 rounded text-sm font-mono border transition-all")}
              style={
                duration === d
                  ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.1)" }
                  : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
              }
            >
              {d}
            </button>
          ))}
        </div>
      </div>

      {isLoading && (
        <p className="text-sm animate-pulse" style={{ color: "var(--text-muted)" }}>
          Loading SLE data...
        </p>
      )}

      {!isLoading && !data && (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          SLE データなし（API未対応またはデータ未収集）
        </p>
      )}

      {!isLoading && data && (
        <div className="space-y-4">
          {/* Metric Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            {METRIC_DEFS.map(({ key, label }) => {
              const m = data[key] as SleMetricData | undefined;
              return (
                <div
                  key={key}
                  className="border rounded-lg p-3 text-center"
                  style={{ borderColor: "var(--chart-grid)", backgroundColor: "var(--bg-hover)" }}
                >
                  <p className="text-xs font-mono mb-1 leading-tight" style={{ color: "var(--text-muted)" }}>
                    {label}
                  </p>
                  <p
                    className="text-2xl font-bold font-mono leading-none my-2"
                    style={{ color: scoreColor(m?.score) }}
                  >
                    {m?.score != null ? `${m.score}%` : "—"}
                  </p>
                  <p className="text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
                    {(m?.total_users ?? 0) > 0
                      ? `${m!.impact_users}/${m!.total_users} users`
                      : "No data"}
                  </p>
                  {key === "time_to_connect" && (m as SleMetricData)?.avg_sec != null && (
                    <p className="text-xs font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>
                      avg {(m as SleMetricData).avg_sec}s
                    </p>
                  )}
                </div>
              );
            })}
          </div>

          {/* Capacity Classifier Breakdown */}
          {data.capacity?.classifiers && (
            <div
              className="border rounded-lg p-4"
              style={{ borderColor: "var(--chart-grid)", backgroundColor: "var(--bg-hover)" }}
            >
              <p className="text-xs font-mono mb-3" style={{ color: "var(--text-muted)" }}>
                CAPACITY — Classifier Breakdown
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {CLASSIFIERS.map(({ key, label }) => {
                  const val = data.capacity.classifiers![key];
                  return (
                    <div key={key} className="space-y-1">
                      <div className="flex justify-between items-center text-xs font-mono">
                        <span style={{ color: "var(--text-secondary)" }}>{label}</span>
                        <span className="font-semibold" style={{ color: "var(--text-primary)" }}>
                          {val != null ? `${val}%` : "—"}
                        </span>
                      </div>
                      <div
                        className="h-2 rounded-full overflow-hidden"
                        style={{ backgroundColor: "var(--chart-grid)" }}
                      >
                        <div
                          className="h-full rounded-full transition-all duration-300"
                          style={{
                            width: `${Math.min(val ?? 0, 100)}%`,
                            backgroundColor: "var(--orange)",
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
