"use client";

import useSWR from "swr";
import { ApEvent, fetchApEvents } from "@/lib/api";
import { toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";

// DFS関連（RADAR）・再起動系イベントは赤バッジで強調
const ALERT_PATTERN = /RADAR|DFS|RESTART|REBOOT/i;

function EventTypeBadge({ type }: { type: string }) {
  const isAlert = ALERT_PATTERN.test(type);
  return (
    <span
      className="px-2 py-0.5 rounded border text-xs font-mono whitespace-nowrap"
      style={
        isAlert
          ? { borderColor: "var(--red)", color: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }
          : { borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }
      }
    >
      {type}
    </span>
  );
}

export default function ApEventsSection({ apId }: { apId: string }) {
  const { timezone } = useTimezone();
  const { data, isLoading } = useSWR<{ events: ApEvent[] }>(
    `ap-events-${apId}`,
    () => fetchApEvents(apId, "1d"),
  );
  const events = data?.events ?? [];

  return (
    <section
      className="border rounded-lg p-5"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
    >
      <h2 className="text-sm font-display font-semibold mb-4 tracking-wider" style={{ color: "var(--cyan)" }}>
        EVENTS (24h)
      </h2>
      {isLoading ? (
        <p className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading events...</p>
      ) : events.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>イベントなし（24h）</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm font-mono border-collapse">
            <thead>
              <tr className="border-b" style={{ borderColor: "var(--border-cyan)" }}>
                {["Time", "Event Type", "Detail"].map((h) => (
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
                    <EventTypeBadge type={e.type} />
                  </td>
                  <td className="py-2.5 px-3" style={{ color: "var(--text-primary)" }}>
                    {e.text || "-"}
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
