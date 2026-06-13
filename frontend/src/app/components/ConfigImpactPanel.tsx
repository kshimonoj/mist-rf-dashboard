"use client";

import useSWR from "swr";
import { ConfigImpact, fetchConfigImpact, ImpactVerdict } from "@/lib/api";

const VERDICT_STYLES: Record<ImpactVerdict, { label: string; color: string; bg: string }> = {
  improved: { label: "Improved", color: "var(--green)", bg: "rgba(0,255,136,0.08)" },
  degraded: { label: "Degraded", color: "var(--red)", bg: "rgba(255,68,68,0.08)" },
  neutral: { label: "Neutral", color: "var(--text-muted)", bg: "transparent" },
  insufficient_data: { label: "Insufficient data", color: "var(--text-muted)", bg: "transparent" },
};

export function VerdictBadge({ verdict }: { verdict: ImpactVerdict }) {
  const s = VERDICT_STYLES[verdict] ?? VERDICT_STYLES.neutral;
  return (
    <span
      className="px-2 py-0.5 rounded border text-xs font-mono whitespace-nowrap"
      style={{ borderColor: s.color, color: s.color, backgroundColor: s.bg }}
    >
      {s.label}
    </span>
  );
}

function MetricCompareCard({ metric }: { metric: ConfigImpact["metrics"][number] }) {
  const color =
    metric.judgment === "improved" ? "var(--green)"
    : metric.judgment === "degraded" ? "var(--red)"
    : "var(--text-primary)";
  const fmt = (v: number | null) => (v !== null ? `${v}${metric.unit}` : "-");
  const pct =
    metric.change_pct !== null
      ? ` (${metric.change_pct > 0 ? "+" : ""}${metric.change_pct}%)`
      : "";
  return (
    <div
      className="border rounded-lg p-3"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-hover)" }}
    >
      <p className="text-xs mb-1" style={{ color: "var(--text-muted)" }}>{metric.label}</p>
      <p className="text-sm font-mono font-bold" style={{ color }}>
        {fmt(metric.before)} → {fmt(metric.after)}{pct}
      </p>
    </div>
  );
}

export function ImpactPanel({ changeId }: { changeId: number }) {
  const { data, isLoading, error } = useSWR<ConfigImpact>(
    `config-impact-${changeId}`,
    () => fetchConfigImpact(changeId),
  );

  if (isLoading) {
    return (
      <div className="py-4 text-sm animate-pulse" style={{ color: "var(--cyan)" }}>
        影響分析中...
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="py-4 text-sm" style={{ color: "var(--red)" }}>
        影響分析の取得に失敗しました
      </div>
    );
  }

  return (
    <div className="py-3 space-y-3">
      <div className="flex items-center gap-3">
        <VerdictBadge verdict={data.verdict} />
        <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
          比較区間: 変更前{data.before_hours}h vs 後{data.after_hours}h
        </span>
      </div>
      {data.verdict === "insufficient_data" ? (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          変更後のデータが不足しているため判定できません（最低1h必要）
        </p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {data.metrics.map((m) => (
            <MetricCompareCard key={m.key} metric={m} />
          ))}
        </div>
      )}
    </div>
  );
}
