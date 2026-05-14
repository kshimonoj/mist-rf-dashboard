"use client";

import { useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend,
} from "recharts";
import { RadioConfigChange, ApRadioConfig } from "@/lib/api";
import { toLocalString, toLocalTimeShort, toLocalDateTimeShort } from "@/lib/time";
import { useTimezone } from "@/app/providers";

export const CHANGE_FIELD_COLORS: Record<string, string> = {
  config_source: "#ff8c00",
  channel: "#00d4ff",
  bandwidth: "#7c3aed",
  tx_power: "#ffd700",
};

export function getSourceColor(source: string): string {
  if (source === "Device (Profile Override)") return "#ff8c00";
  if (source.startsWith("Device Profile:")) return "#7c3aed";
  if (source === "Device") return "#00d4ff";
  if (source.startsWith("Site (")) return "#3b82f6";
  return "#94a3b8";
}

export function getSourceLabel(source: string): string {
  if (source === "Device (Profile Override)") return "Device Override";
  if (source.startsWith("Device Profile:")) return "Device Profile";
  if (source === "Device") return "Device";
  if (source.startsWith("Site (")) return "Site";
  return "Org";
}

export function SourceBadge({ level }: { level: string }) {
  const color = getSourceColor(level);
  const label = getSourceLabel(level);
  return (
    <span
      className="px-2 py-0.5 rounded text-xs font-mono border"
      title={level}
      style={{ color, borderColor: color, backgroundColor: `${color}15` }}
    >
      {label}
    </span>
  );
}

export function MetricCard({ label, value, unit }: { label: string; value: unknown; unit?: string }) {
  return (
    <div className="border rounded-lg p-4" style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}>
      <p className="text-sm mb-1" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="text-xl font-bold font-mono" style={{ color: "var(--cyan)" }}>
        {value !== null && value !== undefined ? `${value}${unit ?? ""}` : "-"}
      </p>
    </div>
  );
}

export function CurrentConfigSnapshot({ radioConfig }: { radioConfig: ApRadioConfig["current"] }) {
  if (!radioConfig) {
    return <p className="text-sm" style={{ color: "var(--text-muted)" }}>設定データ未取得</p>;
  }
  const bands = [
    { label: "2.4 GHz", data: radioConfig.band_24, source: radioConfig.config_source_24 },
    { label: "5 GHz", data: radioConfig.band_5, source: radioConfig.config_source_5 },
    { label: "6 GHz", data: radioConfig.band_6, source: radioConfig.config_source_6 },
  ];
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <p className="text-sm font-mono" style={{ color: "var(--text-muted)" }}>
          Current Configuration (no change history)
        </p>
        {radioConfig.config_source && <SourceBadge level={radioConfig.config_source} />}
        {radioConfig.deviceprofile_name && (
          <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
            DP: {radioConfig.deviceprofile_name}
          </span>
        )}
        {radioConfig.rftemplate_name && (
          <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
            RF: {radioConfig.rftemplate_name}
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {bands.map(({ label, data: b, source }) => (
          <div key={label} className="border rounded-lg p-4 space-y-3"
            style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-hover)" }}>
            <div className="flex items-center justify-between">
              <p className="text-sm font-mono font-semibold" style={{ color: "var(--cyan)" }}>{label}</p>
              {source && <SourceBadge level={source} />}
            </div>
            <div className="grid grid-cols-2 gap-2 text-sm font-mono">
              <div><p style={{ color: "var(--text-muted)" }}>Channel</p><p style={{ color: "var(--text-primary)" }}>{b?.channel ?? "-"}</p></div>
              <div><p style={{ color: "var(--text-muted)" }}>Bandwidth</p><p style={{ color: "var(--text-primary)" }}>{b?.bandwidth != null ? `${b.bandwidth} MHz` : "-"}</p></div>
              <div><p style={{ color: "var(--text-muted)" }}>Tx Power</p><p style={{ color: "var(--text-primary)" }}>{b?.tx_power != null ? `${b.tx_power} dBm` : "-"}</p></div>
              <div>
                <p style={{ color: "var(--text-muted)" }}>Status</p>
                <p style={{ color: b?.disabled ? "var(--red)" : "var(--green)" }}>{b?.disabled ? "DISABLED" : "ENABLED"}</p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function RadioBandPanel({ data, configSource }: {
  data: Record<string, unknown> | null;
  configSource?: string | null;
}) {
  if (!data) return <p className="text-sm" style={{ color: "var(--text-muted)" }}>データなし</p>;
  return (
    <div className="space-y-3">
      {configSource && (
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>Config Source:</span>
          <SourceBadge level={configSource} />
        </div>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard label="Channel" value={data.channel as number | null} />
        <MetricCard label="Bandwidth" value={data.bandwidth as number | null} unit=" MHz" />
        <MetricCard label="Tx Power" value={data.tx_power as number | null} unit=" dBm" />
        <div className="border rounded-lg p-4" style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}>
          <p className="text-sm mb-1" style={{ color: "var(--text-muted)" }}>Status</p>
          <p className="text-xl font-bold font-mono" style={{ color: data.disabled ? "var(--red)" : "var(--green)" }}>
            {data.disabled ? "DISABLED" : "ENABLED"}
          </p>
        </div>
      </div>
    </div>
  );
}

export function CustomTooltip({ active, payload, label, changes }: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
  changes?: RadioConfigChange[];
}) {
  const { timezone } = useTimezone();
  if (!active || !payload?.length) return null;
  const matchingChanges = changes?.filter((c) => c.detected_at === label) ?? [];
  return (
    <div className="border rounded-lg p-3 text-xs font-mono"
      style={{ backgroundColor: "var(--bg-card)", borderColor: "var(--border-cyan)" }}>
      <p className="mb-2" style={{ color: "var(--text-secondary)" }}>
        {label ? toLocalString(label, timezone) : ""}
      </p>
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }}>{p.name}: {p.value ?? "-"}</p>
      ))}
      {matchingChanges.map((c) => (
        <div key={c.id} className="mt-2 border-t pt-2" style={{ borderColor: "var(--chart-grid)" }}>
          <p style={{ color: CHANGE_FIELD_COLORS[c.changed_field] ?? "var(--yellow)" }}>
            {c.band} {c.changed_field}: {c.old_value ?? "-"} → {c.new_value ?? "-"}
          </p>
          {(c.old_source || c.new_source) && (
            <p style={{ color: "var(--text-muted)" }}>
              Source: {c.old_source ?? "-"} → {c.new_source ?? "-"}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export function ChartSection({ title, data, children, changes, hours, yTicks }: {
  title: string;
  data: Record<string, unknown>[];
  children: React.ReactNode;
  changes: RadioConfigChange[];
  hours: number;
  yTicks?: number[];
}) {
  const { timezone } = useTimezone();
  const tickFmt = (v: string) =>
    hours >= 48 ? toLocalDateTimeShort(v, timezone) : toLocalTimeShort(v, timezone);

  const dedupedChanges = useMemo(() => {
    const seen = new Set<string>();
    return changes.filter((c) => {
      if (seen.has(c.detected_at)) return false;
      seen.add(c.detected_at);
      return true;
    });
  }, [changes]);

  return (
    <div>
      <p className="text-sm mb-2" style={{ color: "var(--text-muted)" }}>{title}</p>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
          <XAxis dataKey="timestamp" tickFormatter={tickFmt}
            tick={{ fill: "var(--text-muted)", fontSize: 10 }} axisLine={{ stroke: "var(--chart-grid)" }} />
          <YAxis tick={{ fill: "var(--text-muted)", fontSize: 10 }} axisLine={{ stroke: "var(--chart-grid)" }}
            ticks={yTicks} domain={yTicks ? [0, yTicks[yTicks.length - 1]] : undefined} />
          <Tooltip content={<CustomTooltip changes={changes} />} />
          <Legend wrapperStyle={{ fontSize: 10, color: "var(--text-secondary)" }} />
          {children}
          {dedupedChanges.map((c) => (
            <ReferenceLine key={c.id} x={c.detected_at}
              stroke={CHANGE_FIELD_COLORS[c.changed_field] ?? "#fff"} strokeDasharray="4 2" />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
