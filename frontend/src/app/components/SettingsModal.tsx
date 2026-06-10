"use client";

import { X, Settings, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { fetchSettings, fetchAllSites, updateSettings, pollNow, fetchCredentials, updateCredentials } from "@/lib/api";
import { useTimezone } from "@/app/providers";

const MIST_REGIONS = [
  { label: "Global 01", url: "https://api.mist.com/api/v1" },
  { label: "Global 02 (GC1)", url: "https://api.gc1.mist.com/api/v1" },
  { label: "Global 03 / APAC (AC2)", url: "https://api.ac2.mist.com/api/v1" },
  { label: "Global 04 (GC2)", url: "https://api.gc2.mist.com/api/v1" },
  { label: "Global 05 (GC4)", url: "https://api.gc4.mist.com/api/v1" },
  { label: "EMEA 01 (EU)", url: "https://api.eu.mist.com/api/v1" },
  { label: "EMEA 02 (GC3)", url: "https://api.gc3.mist.com/api/v1" },
  { label: "EMEA 03 (AC6)", url: "https://api.ac6.mist.com/api/v1" },
  { label: "EMEA 04 (GC6)", url: "https://api.gc6.mist.com/api/v1" },
  { label: "APAC 01 (AC5)", url: "https://api.ac5.mist.com/api/v1" },
  { label: "APAC 02 (GC5)", url: "https://api.gc5.mist.com/api/v1" },
  { label: "APAC 03 (GC7)", url: "https://api.gc7.mist.com/api/v1" },
] as const;

function urlToRegion(url: string): string {
  const match = MIST_REGIONS.find((r) => r.url === url);
  return match ? match.url : "custom";
}

export default function SettingsButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all"
        style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
      >
        <Settings className="w-4 h-4" />
        Settings
      </button>
      {open && <SettingsModal onClose={() => setOpen(false)} />}
    </>
  );
}

function SettingsModal({ onClose }: { onClose: () => void }) {
  const { setTimezone: setGlobalTimezone } = useTimezone();
  const { data, mutate } = useSWR("settings", fetchSettings);
  const { data: allSites } = useSWR("all-sites", fetchAllSites);
  const { data: credData, mutate: mutateCredentials } = useSWR("credentials", fetchCredentials);

  const [pollInterval, setPollInterval] = useState<number | "">(data?.polling_interval_seconds ?? "");
  const [logInterval, setLogInterval] = useState<number | "">(data?.log_interval_minutes ?? "");
  const [retentionDays, setRetentionDays] = useState<number | "">(data?.log_retention_days ?? "");
  const [clientPollMinutes, setClientPollMinutes] = useState<number | "">(
    data ? Math.round(data.client_polling_interval_seconds / 60) : ""
  );
  const [timezone, setTimezone] = useState<string>(data?.timezone ?? "");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [siteSearch, setSiteSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const sitesInitialized = useRef(false);

  // Credentials state
  const [credToken, setCredToken] = useState("");
  const [credOrgId, setCredOrgId] = useState("");
  const [credRegion, setCredRegion] = useState("https://api.mist.com/api/v1");
  const [credCustomUrl, setCredCustomUrl] = useState("");
  const [credSettingsKey, setCredSettingsKey] = useState("");
  const [credSaving, setCredSaving] = useState(false);
  const [credToast, setCredToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const credInitialized = useRef(false);

  useEffect(() => {
    if (credData && !credInitialized.current) {
      credInitialized.current = true;
      setCredOrgId(credData.mist_org_id);
      const regionUrl = urlToRegion(credData.mist_base_url);
      if (regionUrl === "custom") {
        setCredRegion("custom");
        setCredCustomUrl(credData.mist_base_url);
      } else {
        setCredRegion(regionUrl);
      }
    }
  }, [credData]);

  useEffect(() => {
    if (data) {
      if (pollInterval === "") setPollInterval(data.polling_interval_seconds);
      if (logInterval === "") setLogInterval(data.log_interval_minutes);
      if (retentionDays === "") setRetentionDays(data.log_retention_days);
      if (clientPollMinutes === "") setClientPollMinutes(Math.round(data.client_polling_interval_seconds / 60));
      if (timezone === "") setTimezone(data.timezone ?? "Asia/Tokyo");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // Initialize selectedIds from settings once allSites is loaded
  useEffect(() => {
    if (data && allSites && !sitesInitialized.current) {
      sitesInitialized.current = true;
      if (data.monitored_site_ids.length === 0) {
        setSelectedIds(new Set(allSites.map((s) => s.id)));
      } else {
        setSelectedIds(new Set(data.monitored_site_ids));
      }
    }
  }, [data, allSites]);

  const pollValid = pollInterval !== "" && Number(pollInterval) >= 30 && Number(pollInterval) <= 3600;
  const logValid = logInterval !== "" && Number(logInterval) >= 1 && Number(logInterval) <= 1440;
  const retentionValid = retentionDays !== "" && Number(retentionDays) >= 1 && Number(retentionDays) <= 365;
  const clientPollValid = clientPollMinutes !== "" && Number(clientPollMinutes) >= 5;
  const tzValid = timezone.trim().length > 0;
  const canApply = pollValid && logValid && retentionValid && clientPollValid && tzValid;

  const filteredSites = allSites?.filter((s) => {
    const q = siteSearch.toLowerCase();
    return s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q);
  }) ?? [];

  const allSelected = !!allSites && selectedIds.size === allSites.length;
  const noneSelected = selectedIds.size === 0;

  const toggleSite = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => setSelectedIds(new Set(allSites?.map((s) => s.id) ?? []));
  const deselectAll = () => setSelectedIds(new Set());

  const handleSaveCredentials = async () => {
    setCredSaving(true);
    try {
      const baseUrl = credRegion === "custom" ? credCustomUrl.trim() : credRegion;
      await updateCredentials(
        {
          ...(credToken ? { mist_api_token: credToken } : {}),
          mist_org_id: credOrgId.trim(),
          mist_base_url: baseUrl,
        },
        credSettingsKey || undefined
      );
      await mutateCredentials();
      setCredToken("");
      setCredSettingsKey("");
      setCredToast({ msg: "設定を保存しました。次回ポーリング時から反映されます。", ok: true });
      setTimeout(() => setCredToast(null), 4000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "保存に失敗しました";
      setCredToast({ msg, ok: false });
      setTimeout(() => setCredToast(null), 5000);
    } finally {
      setCredSaving(false);
    }
  };

  const handleApply = async () => {
    if (!canApply) return;
    const clientPollSeconds = Number(clientPollMinutes) * 60;
    if (clientPollSeconds < 300) {
      setToast({ msg: "Client Polling Interval は最小5分です", ok: false });
      setTimeout(() => setToast(null), 5000);
      return;
    }
    setSaving(true);
    try {
      // 全選択 or 全未選択 → [] (全サイト対象)
      const monitoredIds = allSelected || noneSelected ? [] : Array.from(selectedIds);

      const result = await updateSettings({
        polling_interval_seconds: Number(pollInterval),
        log_interval_minutes: Number(logInterval),
        log_retention_days: Number(retentionDays),
        client_polling_interval_seconds: clientPollSeconds,
        timezone: timezone.trim(),
        monitored_site_ids: monitoredIds,
      });
      setGlobalTimezone(result.timezone);
      await mutate();

      // ホーム画面のサイト一覧を即時更新してポーリング再実行
      globalMutate("sites");
      pollNow().catch(() => {});

      setToast({ msg: "設定を反映しました", ok: true });
      setTimeout(() => setToast(null), 3000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "設定の変更に失敗しました";
      const isTimezoneError = msg.includes("タイムゾーン");
      setToast({
        msg: isTimezoneError ? `${msg}。例: Asia/Tokyo, UTC, America/New_York` : msg,
        ok: false,
      });
      setTimeout(() => setToast(null), 5000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.6)" }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="w-full max-w-lg rounded-xl shadow-2xl border flex flex-col"
        style={{
          backgroundColor: "var(--bg-card)",
          borderColor: "var(--border-cyan)",
          maxHeight: "90vh",
        }}
      >
        <div
          className="flex items-center justify-between p-4 border-b flex-shrink-0"
          style={{ borderColor: "var(--border-cyan)" }}
        >
          <h2 className="font-display font-semibold tracking-wider text-sm" style={{ color: "var(--cyan)" }}>
            SETTINGS
          </h2>
          <button onClick={onClose} style={{ color: "var(--text-muted)" }}>
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-6 overflow-y-auto">
          {/* API Credentials */}
          <div
            className="rounded-lg p-4 space-y-4"
            style={{ backgroundColor: "var(--bg-hover)", border: "1px solid var(--border-cyan)" }}
          >
            <h3 className="text-xs font-mono font-semibold tracking-widest" style={{ color: "var(--cyan)" }}>
              API CREDENTIALS
            </h3>

            <div>
              <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
                API Token
              </label>
              <input
                type="password"
                value={credToken}
                onChange={(e) => setCredToken(e.target.value)}
                placeholder={credData?.mist_api_token || "変更しない場合は空欄"}
                className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              />
              {credData?.mist_api_token && (
                <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                  現在: {credData.mist_api_token}（空欄のまま保存すると既存トークンを維持）
                </p>
              )}
            </div>

            <div>
              <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
                Org ID
              </label>
              <input
                type="text"
                value={credOrgId}
                onChange={(e) => setCredOrgId(e.target.value)}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              />
            </div>

            <div>
              <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
                Region
              </label>
              <select
                value={credRegion}
                onChange={(e) => setCredRegion(e.target.value)}
                className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)", backgroundColor: "var(--bg-card)" }}
              >
                {MIST_REGIONS.map((r) => (
                  <option key={r.url} value={r.url} style={{ backgroundColor: "var(--bg-card)" }}>
                    {r.label}
                  </option>
                ))}
                <option value="custom" style={{ backgroundColor: "var(--bg-card)" }}>
                  カスタム
                </option>
              </select>
              {credRegion === "custom" && (
                <input
                  type="text"
                  value={credCustomUrl}
                  onChange={(e) => setCredCustomUrl(e.target.value)}
                  placeholder="https://api.example.mist.com/api/v1"
                  className="w-full mt-2 px-3 py-2 rounded border bg-transparent text-sm font-mono"
                  style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
                />
              )}
              {credRegion !== "custom" && (
                <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                  {credRegion}
                </p>
              )}
            </div>

            {credData?.secret_required && (
              <div>
                <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
                  Admin Key <span style={{ color: "var(--text-muted)" }}>(SETTINGS_SECRET)</span>
                </label>
                <input
                  type="password"
                  value={credSettingsKey}
                  onChange={(e) => setCredSettingsKey(e.target.value)}
                  placeholder="サーバー設定の SETTINGS_SECRET 値"
                  className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
                  style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
                />
                <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                  このサーバーは管理者キーが必要です
                </p>
              </div>
            )}

            <div className="flex items-center justify-between">
              <div className="flex-1">
                {credToast && (
                  <span
                    className="text-xs font-mono"
                    style={{ color: credToast.ok ? "var(--green)" : "var(--red)" }}
                  >
                    {credToast.msg}
                  </span>
                )}
              </div>
              <button
                onClick={handleSaveCredentials}
                disabled={credSaving}
                className="px-4 py-2 rounded-lg text-sm font-mono transition-all disabled:opacity-40"
                style={{
                  backgroundColor: "rgba(0,212,255,0.15)",
                  borderWidth: 1,
                  borderColor: "var(--border-cyan)",
                  color: "var(--cyan)",
                }}
              >
                {credSaving ? "Saving..." : "Save Credentials"}
              </button>
            </div>
          </div>

          {/* Polling Interval */}
          <div>
            <label className="block text-sm font-mono mb-2" style={{ color: "var(--text-secondary)" }}>
              Polling Interval
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={30}
                max={3600}
                value={pollInterval}
                onChange={(e) => setPollInterval(e.target.value === "" ? "" : Number(e.target.value))}
                placeholder={String(data?.polling_interval_seconds ?? 300)}
                className="w-28 px-3 py-2 rounded border bg-transparent text-sm font-mono"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              />
              <span className="text-sm" style={{ color: "var(--text-muted)" }}>秒（30〜3600）</span>
            </div>
            {data && (
              <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                現在: {data.polling_interval_seconds} 秒
              </p>
            )}
          </div>

          {/* Log Auto-Save Interval */}
          <div>
            <label className="block text-sm font-mono mb-2" style={{ color: "var(--text-secondary)" }}>
              Log Auto-Save Interval
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={1}
                max={1440}
                value={logInterval}
                onChange={(e) => setLogInterval(e.target.value === "" ? "" : Number(e.target.value))}
                placeholder={String(data?.log_interval_minutes ?? 60)}
                className="w-28 px-3 py-2 rounded border bg-transparent text-sm font-mono"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              />
              <span className="text-sm" style={{ color: "var(--text-muted)" }}>分（1〜1440）</span>
            </div>
            {data && (
              <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                現在: {data.log_interval_minutes} 分
              </p>
            )}
          </div>

          {/* Log Retention */}
          <div>
            <label className="block text-sm font-mono mb-2" style={{ color: "var(--text-secondary)" }}>
              Log Retention Days
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={1}
                max={365}
                value={retentionDays}
                onChange={(e) => setRetentionDays(e.target.value === "" ? "" : Number(e.target.value))}
                placeholder={String(data?.log_retention_days ?? 30)}
                className="w-28 px-3 py-2 rounded border bg-transparent text-sm font-mono"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              />
              <span className="text-sm" style={{ color: "var(--text-muted)" }}>日（1〜365）</span>
            </div>
            {data && (
              <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                現在: {data.log_retention_days} 日 / 上限 500 MB
              </p>
            )}
          </div>

          {/* Client Polling Interval */}
          <div>
            <label className="block text-sm font-mono mb-2" style={{ color: "var(--text-secondary)" }}>
              Client Polling Interval
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={5}
                value={clientPollMinutes}
                onChange={(e) => setClientPollMinutes(e.target.value === "" ? "" : Number(e.target.value))}
                placeholder={String(data ? Math.round(data.client_polling_interval_seconds / 60) : 10)}
                className="w-28 px-3 py-2 rounded border bg-transparent text-sm font-mono"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              />
              <span className="text-sm" style={{ color: "var(--text-muted)" }}>分（最小5）</span>
            </div>
            {data && (
              <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                現在: {Math.round(data.client_polling_interval_seconds / 60)} 分（{data.client_polling_interval_seconds} 秒）
              </p>
            )}
          </div>

          {/* Timezone */}
          <div>
            <label className="block text-sm font-mono mb-2" style={{ color: "var(--text-secondary)" }}>
              Timezone
            </label>
            <input
              type="text"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              placeholder="Asia/Tokyo"
              className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
              style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
            />
            {data && (
              <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                現在: {data.timezone} — 例: Asia/Tokyo, UTC, America/New_York
              </p>
            )}
          </div>

          {/* Monitored Sites */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-mono" style={{ color: "var(--text-secondary)" }}>
                Monitored Sites
              </label>
              <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                {noneSelected || allSelected
                  ? "All Sites"
                  : `${selectedIds.size} / ${allSites?.length ?? 0} selected`}
              </span>
            </div>

            {/* Search */}
            <div className="relative mb-2">
              <Search
                className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5"
                style={{ color: "var(--text-muted)" }}
              />
              <input
                type="text"
                value={siteSearch}
                onChange={(e) => setSiteSearch(e.target.value)}
                placeholder="サイト名で絞り込み..."
                className="w-full pl-8 pr-3 py-1.5 rounded border bg-transparent text-xs font-mono"
                style={{ borderColor: "var(--border-cyan)", color: "var(--text-primary)" }}
              />
            </div>

            {/* Select All / Deselect All */}
            <div className="flex gap-3 mb-2">
              <button
                onClick={selectAll}
                className="text-xs font-mono px-2 py-1 rounded border transition-all"
                style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
              >
                全選択
              </button>
              <button
                onClick={deselectAll}
                className="text-xs font-mono px-2 py-1 rounded border transition-all"
                style={{ borderColor: "var(--chart-grid)", color: "var(--text-muted)" }}
              >
                全解除
              </button>
              {noneSelected && (
                <span className="text-xs font-mono self-center" style={{ color: "var(--text-muted)" }}>
                  ※ 全解除 = All Sites として扱います
                </span>
              )}
            </div>

            {/* Site checkboxes */}
            <div
              className="border rounded-lg overflow-y-auto"
              style={{
                borderColor: "var(--border-cyan)",
                backgroundColor: "var(--bg-hover)",
                maxHeight: "180px",
              }}
            >
              {!allSites ? (
                <p className="text-xs font-mono p-3 text-center" style={{ color: "var(--text-muted)" }}>
                  Loading...
                </p>
              ) : filteredSites.length === 0 ? (
                <p className="text-xs font-mono p-3 text-center" style={{ color: "var(--text-muted)" }}>
                  該当なし
                </p>
              ) : (
                filteredSites.map((site) => (
                  <label
                    key={site.id}
                    className="flex items-center gap-2.5 px-3 py-2 cursor-pointer transition-colors hover:bg-opacity-20"
                    style={{ borderBottom: "1px solid var(--chart-grid)" }}
                  >
                    <input
                      type="checkbox"
                      checked={selectedIds.has(site.id)}
                      onChange={() => toggleSite(site.id)}
                      className="w-3.5 h-3.5 cursor-pointer accent-cyan-400 flex-shrink-0"
                    />
                    <span className="text-xs font-mono truncate" style={{ color: "var(--text-primary)" }}>
                      {site.name}
                    </span>
                    <span className="text-xs font-mono ml-auto flex-shrink-0" style={{ color: "var(--text-muted)" }}>
                      {site.id.slice(0, 8)}…
                    </span>
                  </label>
                ))
              )}
            </div>
          </div>

          <div className="flex justify-end">
            <button
              onClick={handleApply}
              disabled={saving || !canApply}
              className="px-5 py-2 rounded-lg text-sm font-mono transition-all disabled:opacity-40"
              style={{
                backgroundColor: "rgba(0,212,255,0.15)",
                borderWidth: 1,
                borderColor: "var(--border-cyan)",
                color: "var(--cyan)",
              }}
            >
              {saving ? "Applying..." : "Apply"}
            </button>
          </div>
        </div>

        {toast && (
          <div
            className="mx-5 mb-4 px-4 py-2 rounded border text-sm font-mono flex-shrink-0"
            style={{
              borderColor: toast.ok ? "var(--green)" : "var(--red)",
              color: toast.ok ? "var(--green)" : "var(--red)",
              backgroundColor: toast.ok ? "rgba(0,255,128,0.05)" : "rgba(255,68,68,0.05)",
            }}
          >
            {toast.msg}
          </div>
        )}
      </div>
    </div>
  );
}
