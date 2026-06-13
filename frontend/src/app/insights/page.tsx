"use client";

import { ArrowLeft, ArrowUpDown, Home, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  analyzeInsights, fetchConfigImpact, fetchInsights, fetchRecentConfigChanges,
  ConfigImpact, InsightCategory, InsightIssue, InsightsResponse, InsightSeverity,
  RecentConfigChange,
} from "@/lib/api";
import ThemeToggle from "@/app/components/ThemeToggle";
import { VerdictBadge } from "@/app/components/ConfigImpactPanel";
import { toLocalDateTimeShort, toLocalString } from "@/lib/time";
import { useTimezone } from "@/app/providers";

const CATEGORY_LABELS: Record<InsightCategory, string> = {
  sticky_client: "Sticky Clients",
  band24_stuck: "2.4GHz滞留",
  high_retry: "High Retry",
  co_channel: "Co-channel",
  flapping: "Flapping",
};

const CATEGORIES = Object.keys(CATEGORY_LABELS) as InsightCategory[];

type SeverityFilter = "all" | InsightSeverity;
type SortKey = "severity" | "category" | "site";

const SEVERITY_ORDER: Record<string, number> = { critical: 0, warning: 1 };

function SeverityBadge({ severity }: { severity: InsightSeverity }) {
  const isCritical = severity === "critical";
  return (
    <span
      className="px-2 py-0.5 rounded border text-xs font-mono"
      style={
        isCritical
          ? { borderColor: "var(--red)", color: "var(--red)", backgroundColor: "rgba(255,68,68,0.08)" }
          : { borderColor: "var(--yellow)", color: "var(--yellow)", backgroundColor: "rgba(255,215,0,0.08)" }
      }
    >
      {isCritical ? "Critical" : "Warning"}
    </span>
  );
}

function StatusBadge({ issue }: { issue: InsightIssue }) {
  if (issue.status === "active") {
    const isCritical = issue.severity === "critical";
    const color = isCritical ? "var(--red)" : "var(--yellow)";
    const bg = isCritical ? "rgba(255,68,68,0.08)" : "rgba(255,215,0,0.08)";
    return (
      <span
        className="px-2 py-0.5 rounded border text-xs font-mono"
        style={{ borderColor: color, color, backgroundColor: bg }}
      >
        Active
      </span>
    );
  }
  return (
    <span
      className="px-2 py-0.5 rounded border text-xs font-mono"
      style={{ borderColor: "var(--text-muted)", color: "var(--text-muted)" }}
    >
      Resolved
    </span>
  );
}

function SummaryCard({
  category, issues, selected, onClick,
}: {
  category: InsightCategory;
  issues: InsightIssue[];
  selected: boolean;
  onClick: () => void;
}) {
  const items = issues.filter((i) => i.category === category);
  const criticalCount = items.filter((i) => i.severity === "critical").length;
  const color =
    criticalCount > 0 ? "var(--red)" : items.length > 0 ? "var(--yellow)" : "var(--green)";

  return (
    <button
      onClick={onClick}
      className="border rounded-lg p-4 text-left transition-all cursor-pointer"
      style={{
        borderColor: selected ? "var(--cyan)" : "var(--border-cyan)",
        borderWidth: selected ? 2 : 1,
        backgroundColor: "var(--bg-card)",
      }}
    >
      <p className="text-xs mb-2" style={{ color: "var(--text-muted)" }}>
        {CATEGORY_LABELS[category]}
      </p>
      <p className="text-3xl font-bold" style={{ color }}>
        {items.length}
      </p>
      <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
        {criticalCount > 0 ? `critical ${criticalCount}` : items.length > 0 ? "warning" : "OK"}
      </p>
    </button>
  );
}

function TargetLinks({ issue }: { issue: InsightIssue }) {
  const linkStyle = { color: "var(--cyan)" };
  if (issue.target_type === "ap_pair") {
    const apIds = issue.target_id.split("|");
    const apNames = (issue.target_name ?? "").split(" ↔ ");
    return (
      <span className="inline-flex items-center gap-1 flex-wrap">
        {apIds.map((apId, i) => (
          <span key={apId}>
            <Link href={`/sites/${issue.site_id}/aps/${apId}`} className="hover:underline" style={linkStyle}>
              {apNames[i] || apId}
            </Link>
            {i < apIds.length - 1 && <span style={{ color: "var(--text-muted)" }}> ↔ </span>}
          </span>
        ))}
      </span>
    );
  }
  const href =
    issue.target_type === "ap"
      ? `/sites/${issue.site_id}/aps/${issue.target_id}`
      : `/sites/${issue.site_id}/clients/${issue.target_id}`;
  return (
    <Link href={href} className="hover:underline" style={linkStyle}>
      {issue.target_name || issue.target_id}
    </Link>
  );
}

function RecommendationsSection({ data }: { data: InsightsResponse }) {
  const recs = data.recommendations ?? [];
  if (recs.length === 0) return null;
  return (
    <section className="mb-8">
      <h2 className="text-sm font-display font-semibold mb-3 tracking-wider" style={{ color: "var(--cyan)" }}>
        RECOMMENDATIONS
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {recs.map((rec) => (
          <div
            key={rec.ap_id ?? `site:${rec.site_id}`}
            className="border rounded-lg p-4"
            style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
          >
            <div className="flex items-center justify-between mb-2">
              {rec.ap_id && rec.site_id ? (
                <Link
                  href={`/sites/${rec.site_id}/aps/${rec.ap_id}`}
                  className="text-sm font-mono font-semibold hover:underline"
                  style={{ color: "var(--cyan)" }}
                >
                  {rec.ap_name || rec.ap_id}
                </Link>
              ) : (
                <span className="text-sm font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                  {rec.ap_name}
                </span>
              )}
              {rec.site_name && (
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>{rec.site_name}</span>
              )}
            </div>
            <ul className="space-y-1.5">
              {rec.actions.map((action) => (
                <li key={action} className="text-xs flex gap-2" style={{ color: "var(--text-secondary)" }}>
                  <span style={{ color: "var(--yellow)" }}>▸</span>
                  <span>{action}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function ConfigChangeImpactRow({ change }: { change: RecentConfigChange }) {
  const { timezone } = useTimezone();
  const { data: impact, isLoading } = useSWR<ConfigImpact>(
    `config-impact-${change.id}`,
    () => fetchConfigImpact(change.id),
  );
  return (
    <tr className="border-b" style={{ borderColor: "var(--chart-grid)" }}>
      <td className="py-2.5 px-3 whitespace-nowrap">
        {change.site_id ? (
          <Link
            href={`/sites/${change.site_id}/aps/${change.ap_id}`}
            className="hover:underline"
            style={{ color: "var(--cyan)" }}
          >
            {change.ap_name || change.ap_id}
          </Link>
        ) : (
          <span style={{ color: "var(--text-primary)" }}>{change.ap_name || change.ap_id}</span>
        )}
      </td>
      <td className="py-2.5 px-3 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
        {change.band}
      </td>
      <td className="py-2.5 px-3" style={{ color: "var(--text-primary)" }}>
        {change.changed_field}: {change.old_value ?? "-"} → {change.new_value ?? "-"}
      </td>
      <td className="py-2.5 px-3 whitespace-nowrap text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
        {toLocalString(change.detected_at, timezone)}
      </td>
      <td className="py-2.5 px-3 whitespace-nowrap">
        {isLoading ? (
          <span className="text-xs animate-pulse" style={{ color: "var(--cyan)" }}>分析中...</span>
        ) : impact ? (
          <VerdictBadge verdict={impact.verdict} />
        ) : (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>—</span>
        )}
      </td>
    </tr>
  );
}

function ConfigChangeImpactSection() {
  const { data: changes, isLoading } = useSWR<RecentConfigChange[]>(
    "recent-config-changes",
    () => fetchRecentConfigChanges(7),
  );
  return (
    <section className="mt-8">
      <h2 className="text-sm font-display font-semibold mb-3 tracking-wider" style={{ color: "var(--cyan)" }}>
        CONFIG CHANGE IMPACT
      </h2>
      <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
        直近7日間の設定変更と Before/After 影響分析（比較区間: 変更前6h vs 後6h）
      </p>
      {isLoading ? (
        <div className="text-sm animate-pulse py-6" style={{ color: "var(--cyan)" }}>Loading...</div>
      ) : !changes || changes.length === 0 ? (
        <div
          className="border rounded-lg py-10 text-center text-sm"
          style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)", color: "var(--text-muted)" }}
        >
          直近7日間の設定変更はありません
        </div>
      ) : (
        <div
          className="border rounded-lg overflow-x-auto"
          style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left" style={{ borderColor: "var(--chart-grid)" }}>
                {["AP", "Band", "Change", "Detected At", "Impact"].map((h) => (
                  <th key={h} className="py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {changes.map((c) => (
                <ConfigChangeImpactRow key={c.id} change={c} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function InsightsPage() {
  const { timezone } = useTimezone();
  const { data, isLoading, mutate } = useSWR<InsightsResponse>("insights", () => fetchInsights());
  const [issueTab, setIssueTab] = useState<"active" | "history">("active");
  const { data: historyData, isLoading: historyLoading, mutate: mutateHistory } = useSWR<InsightsResponse>(
    issueTab === "history" ? "insights-history" : null,
    () => fetchInsights("history"),
  );
  const [analyzing, setAnalyzing] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<InsightCategory | null>(null);
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("severity");
  const [sortAsc, setSortAsc] = useState(true);

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      const result = await analyzeInsights();
      mutate(result, { revalidate: false });
      mutateHistory();
    } catch (e) {
      alert(`分析に失敗しました: ${e instanceof Error ? e.message : e}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const issues = useMemo(() => {
    let rows = (issueTab === "history" ? historyData?.issues : data?.issues) ?? [];
    if (categoryFilter) rows = rows.filter((i) => i.category === categoryFilter);
    if (severityFilter !== "all") rows = rows.filter((i) => i.severity === severityFilter);
    if (issueTab === "history") return rows; // API の last_detected_at 降順を維持
    const dir = sortAsc ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (sortKey === "severity") {
        return ((SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)) * dir;
      }
      if (sortKey === "category") {
        return a.category.localeCompare(b.category) * dir;
      }
      return (a.site_name ?? a.site_id).localeCompare(b.site_name ?? b.site_id) * dir;
    });
  }, [data, historyData, issueTab, categoryFilter, severityFilter, sortKey, sortAsc]);

  const headerBtnStyle = { borderColor: "var(--border-cyan)", color: "var(--cyan)" };

  return (
    <main className="min-h-screen p-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}>
            <Home className="w-4 h-4" />
            Home
          </Link>
          <span style={{ color: "var(--chart-grid)" }}>|</span>
          <Link href="/" className="flex items-center gap-1.5 text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}>
            <ArrowLeft className="w-4 h-4" />
            Back
          </Link>
          <div className="ml-1 flex items-center gap-2">
            <Search className="w-5 h-5" style={{ color: "var(--cyan)" }} />
            <h1 className="font-display font-bold text-2xl" style={{ color: "var(--text-primary)" }}>
              Insights
            </h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="text-right mr-2">
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>Last analyzed</p>
            <p className="text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
              {data?.analyzed_at ? toLocalString(data.analyzed_at, timezone) : "—"}
            </p>
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              分析対象: 直近1時間のデータ
            </p>
          </div>
          <button
            onClick={handleAnalyze}
            disabled={analyzing}
            className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all disabled:opacity-60"
            style={headerBtnStyle}
          >
            <RefreshCw className={`w-4 h-4 ${analyzing ? "animate-spin" : ""}`} />
            {analyzing ? "Analyzing..." : "Analyze Now"}
          </button>
          <ThemeToggle />
        </div>
      </header>

      {isLoading && (
        <div className="flex justify-center py-20">
          <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>
            Loading insights...
          </div>
        </div>
      )}

      {!isLoading && data && (
        <>
          {/* サマリーカード（クリックでカテゴリフィルタ） */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-8">
            {CATEGORIES.map((cat) => (
              <SummaryCard
                key={cat}
                category={cat}
                issues={data.issues}
                selected={categoryFilter === cat}
                onClick={() => setCategoryFilter((c) => (c === cat ? null : cat))}
              />
            ))}
          </div>

          {/* レコメンデーション（0件なら非表示） */}
          <RecommendationsSection data={data} />

          {/* Active / History タブ */}
          <div className="flex gap-1 mb-4 border-b" style={{ borderColor: "var(--chart-grid)" }}>
            {(["active", "history"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setIssueTab(t)}
                className="px-4 py-2 text-sm transition-colors -mb-px border-b-2"
                style={{
                  borderColor: issueTab === t ? "var(--cyan)" : "transparent",
                  color: issueTab === t ? "var(--cyan)" : "var(--text-muted)",
                }}
              >
                {t === "active" ? "Active" : "History"}
              </button>
            ))}
          </div>

          {/* フィルター行 */}
          <div className="flex items-center gap-3 mb-4">
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>Severity:</span>
            {(["all", "critical", "warning"] as SeverityFilter[]).map((s) => (
              <button
                key={s}
                onClick={() => setSeverityFilter(s)}
                className="px-3 py-1 rounded border text-xs transition-all"
                style={
                  severityFilter === s
                    ? { borderColor: "var(--cyan)", color: "var(--cyan)", backgroundColor: "rgba(0,212,255,0.08)" }
                    : { borderColor: "var(--chart-grid)", color: "var(--text-muted)" }
                }
              >
                {s === "all" ? "All" : s === "critical" ? "Critical" : "Warning"}
              </button>
            ))}
            {categoryFilter && (
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                カテゴリ: {CATEGORY_LABELS[categoryFilter]}
                <button
                  onClick={() => setCategoryFilter(null)}
                  className="ml-2 underline"
                  style={{ color: "var(--cyan)" }}
                >
                  解除
                </button>
              </span>
            )}
            <span className="ml-auto text-xs" style={{ color: "var(--text-muted)" }}>
              {issues.length} 件
            </span>
          </div>

          {/* Issue 一覧 */}
          {issueTab === "history" && historyLoading ? (
            <div className="flex justify-center py-16">
              <div className="text-sm animate-pulse" style={{ color: "var(--cyan)" }}>Loading history...</div>
            </div>
          ) : issues.length === 0 ? (
            <div
              className="border rounded-lg py-16 text-center text-sm"
              style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)", color: "var(--text-secondary)" }}
            >
              {issueTab === "history" ? "履歴はありません" : "問題は検出されていません ✅"}
            </div>
          ) : (
            <div
              className="border rounded-lg overflow-x-auto"
              style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
            >
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left" style={{ borderColor: "var(--chart-grid)" }}>
                    {([
                      { key: "severity" as SortKey, label: "Severity" },
                      { key: "category" as SortKey, label: "Category" },
                    ]).map(({ key, label }) => (
                      <th
                        key={key}
                        className="py-3 px-3 font-normal cursor-pointer select-none whitespace-nowrap"
                        style={{ color: "var(--text-muted)" }}
                        onClick={() => toggleSort(key)}
                      >
                        <span className="inline-flex items-center gap-1">
                          {label}
                          <ArrowUpDown
                            className="w-3 h-3"
                            style={{ color: sortKey === key ? "var(--cyan)" : "var(--text-muted)" }}
                          />
                        </span>
                      </th>
                    ))}
                    <th className="py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                      Target
                    </th>
                    <th
                      className="py-3 px-3 font-normal cursor-pointer select-none whitespace-nowrap"
                      style={{ color: "var(--text-muted)" }}
                      onClick={() => toggleSort("site")}
                    >
                      <span className="inline-flex items-center gap-1">
                        Site
                        <ArrowUpDown
                          className="w-3 h-3"
                          style={{ color: sortKey === "site" ? "var(--cyan)" : "var(--text-muted)" }}
                        />
                      </span>
                    </th>
                    <th className="py-3 px-3 font-normal" style={{ color: "var(--text-muted)" }}>Detail</th>
                    {issueTab === "active" ? (
                      <>
                        <th className="py-3 px-3 font-normal" style={{ color: "var(--text-muted)" }}>Recommendation</th>
                        <th className="py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                          Detected At
                        </th>
                        <th className="py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                          Since
                        </th>
                      </>
                    ) : (
                      <>
                        <th className="py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                          Status
                        </th>
                        <th className="py-3 px-3 font-normal whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                          Period
                        </th>
                      </>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {issues.map((issue) => (
                    <tr
                      key={issue.id}
                      className="border-b transition-colors"
                      style={{ borderColor: "var(--chart-grid)" }}
                    >
                      <td className="py-3 px-3 whitespace-nowrap">
                        <SeverityBadge severity={issue.severity} />
                      </td>
                      <td className="py-3 px-3 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                        {CATEGORY_LABELS[issue.category] ?? issue.category}
                      </td>
                      <td className="py-3 px-3 whitespace-nowrap">
                        <TargetLinks issue={issue} />
                      </td>
                      <td className="py-3 px-3 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                        {issue.site_name || issue.site_id}
                      </td>
                      <td className="py-3 px-3" style={{ color: "var(--text-primary)" }}>
                        {issue.detail}
                      </td>
                      {issueTab === "active" ? (
                        <>
                          <td className="py-3 px-3 text-xs" style={{ color: "var(--text-muted)" }}>
                            {issue.recommendation}
                          </td>
                          <td className="py-3 px-3 whitespace-nowrap text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
                            {toLocalString(issue.last_detected_at, timezone)}
                          </td>
                          <td className="py-3 px-3 whitespace-nowrap text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
                            {toLocalDateTimeShort(issue.first_detected_at, timezone)}から継続
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="py-3 px-3 whitespace-nowrap">
                            <StatusBadge issue={issue} />
                          </td>
                          <td className="py-3 px-3 whitespace-nowrap text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
                            {toLocalDateTimeShort(issue.first_detected_at, timezone)} 〜{" "}
                            {issue.resolved_at ? toLocalDateTimeShort(issue.resolved_at, timezone) : "現在"}
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 設定変更の影響分析 */}
          <ConfigChangeImpactSection />
        </>
      )}
    </main>
  );
}
