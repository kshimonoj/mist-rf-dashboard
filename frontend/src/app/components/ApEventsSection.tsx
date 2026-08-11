"use client";

import { useState } from "react";
import useSWR from "swr";
import { ApEvent, fetchApEvents } from "@/lib/api";
import { toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";

type HourRange = 24 | 168 | 720;

const BAND_LABELS: Record<string, string> = { "24": "2.4G", "5": "5G", "6": "6G" };

function EventTypeBadge({ type, isRestart }: { type: string; isRestart: boolean }) {
  if (isRestart) {
    return (
      <span
        className="px-2 py-0.5 rounded border text-xs font-mono whitespace-nowrap"
        style={{ borderColor: "var(--red)", color: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }}
      >
        {type}
      </span>
    );
  }
  if (type === "AP_RADAR_DETECTED") {
    return (
      <span
        className="px-2 py-0.5 rounded border text-xs font-mono whitespace-nowrap"
        style={{ borderColor: "var(--orange, #f59e0b)", color: "var(--orange, #f59e0b)", backgroundColor: "rgba(245,158,11,0.08)" }}
      >
        {type}
      </span>
    );
  }
  return (
    <span
      className="px-2 py-0.5 rounded border text-xs font-mono whitespace-nowrap"
      style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
    >
      {type}
    </span>
  );
}

function eventDetail(e: ApEvent): string {
  const parts: string[] = [];
  if (e.band) parts.push(BAND_LABELS[e.band] ?? e.band);
  if (e.pre_channel != null && e.channel != null && e.pre_channel !== e.channel) {
    parts.push(`Ch ${e.pre_channel}→${e.channel}`);
  } else if (e.channel != null) {
    parts.push(`Ch ${e.channel}`);
  }
  if (e.pre_bandwidth != null && e.bandwidth != null && e.pre_bandwidth !== e.bandwidth) {
    parts.push(`${e.pre_bandwidth}→${e.bandwidth}MHz`);
  }
  return parts.join(" ") || "-";
}

export default function ApEventsSection({ apId }: { apId: string }) {
  const { timezone } = useTimezone();
  const [hours, setHours] = useState<HourRange>(24);
  const { data, isLoading } = useSWR<{ events: ApEvent[] }>(
    `ap-events-${apId}-${hours}`,
    () => fetchApEvents(apId, hours),
  );
  const events = data?.events ?? [];

  const ranges: { value: HourRange; label: string }[] = [
    { value: 24, label: "24h" },
    { value: 168, label: "7d" },
    { value: 720, label: "30d" },
  ];

  return (
    <section
      className="border rounded-lg p-5"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-display font-semibold tracking-wider" style={{ color: "var(--cyan)" }}>
          EVENTS
        </h2>
        <div className="flex gap-2">
          {ranges.map((r) => (
            <button
              key={r.value}
              onClick={() => setHours(r.value)}
              className="px-3 py-1 rounded text-sm font-mono border transition-all"
              style={
                hours === r.value
                  ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.1)" }
                  : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
              }
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <p className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading events...</p>
      ) : events.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>イベントはありません</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm font-mono border-collapse">
            <thead>
              <tr className="border-b" style={{ borderColor: "var(--border-cyan)" }}>
                {["Time", "Type", "Reason", "Detail"].map((h) => (
                  <th key={h} className="text-left py-3 px-3 font-normal" style={{ color: "var(--text-muted)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={`${e.timestamp}-${i}`} className="border-b" style={{ borderColor: "var(--chart-grid)" }}>
                  <td className="py-2.5 px-3 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                    {e.timestamp ? toLocalString(e.timestamp, timezone) : "-"}
                  </td>
                  <td className="py-2.5 px-3">
                    <EventTypeBadge type={e.type} isRestart={e.is_restart} />
                  </td>
                  <td className="py-2.5 px-3" style={{ color: "var(--text-primary)" }}>
                    {e.reason || "-"}
                  </td>
                  <td className="py-2.5 px-3" style={{ color: "var(--text-secondary)" }}>
                    {eventDetail(e)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
