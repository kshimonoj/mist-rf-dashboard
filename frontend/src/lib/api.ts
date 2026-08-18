const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8008";

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

export interface SiteInfo {
  id: string;
  name: string;
  address: string;
  country_code: string;
  ap_count: number;
  online_count: number;
  offline_count: number;
}

export interface RadioBand {
  channel: number | null;
  utilization: number | null;
  noise_floor: number | null;
  tx_power: number | null;
}

export interface ApInfo {
  id: string;
  name: string;
  mac: string;
  model: string;
  ip: string;
  status: string;
  uptime: number | null;
  num_clients: number;
  radio_24: RadioBand;
  radio_5: RadioBand;
  radio_6: RadioBand;
}

export interface ApMetric {
  timestamp: string;
  num_clients: number;
  radio_24_channel: number | null;
  radio_24_bandwidth: number | null;
  radio_24_utilization: number | null;
  radio_24_util_tx: number | null;
  radio_24_util_rx_in_bss: number | null;
  radio_24_util_non_wifi: number | null;
  radio_24_noise_floor: number | null;
  radio_24_tx_power: number | null;
  radio_5_channel: number | null;
  radio_5_bandwidth: number | null;
  radio_5_utilization: number | null;
  radio_5_util_tx: number | null;
  radio_5_util_rx_in_bss: number | null;
  radio_5_util_non_wifi: number | null;
  radio_5_noise_floor: number | null;
  radio_5_tx_power: number | null;
  radio_6_channel: number | null;
  radio_6_bandwidth: number | null;
  radio_6_utilization: number | null;
  radio_6_util_tx: number | null;
  radio_6_util_rx_in_bss: number | null;
  radio_6_util_non_wifi: number | null;
  radio_6_noise_floor: number | null;
  radio_6_tx_power: number | null;
  status: string;
}

export interface RadioConfigChange {
  id: number;
  detected_at: string;
  band: string;
  changed_field: string;
  old_value: string | null;
  new_value: string | null;
  old_source: string | null;
  new_source: string | null;
}

export interface RadioConfigBand {
  channel: number | null;
  bandwidth: number | null;
  tx_power: number | null;
  disabled: boolean;
}

export interface ApRadioConfig {
  current: {
    ap_id: string;
    ap_name: string;
    site_id: string;
    config_source: string | null;
    config_source_24: string | null;
    config_source_5: string | null;
    config_source_6: string | null;
    deviceprofile_name: string | null;
    rftemplate_name: string | null;
    band_24: RadioConfigBand;
    band_5: RadioConfigBand;
    band_6: RadioConfigBand;
  } | null;
  changes: RadioConfigChange[];
}

export interface SiteSummary {
  id: string;
  name: string;
  address: string;
  country_code: string;
}

export interface ClientInfo {
  mac: string;
  hostname?: string | null;
  ip?: string | null;
  manufacture?: string | null;
  family?: string | null;
  model?: string | null;
  os?: string | null;
  ap_id?: string | null;
  ap_name?: string | null;
  ap_mac?: string | null;
  band?: string | null;
  channel?: number | null;
  proto?: string | null;
  ssid?: string | null;
  bssid?: string | null;
  rssi?: number | null;
  snr?: number | null;
  idle_time?: number | null;
  uptime?: number | null;
  tx_rate?: number | null;
  rx_rate?: number | null;
  tx_bytes?: number | null;
  rx_bytes?: number | null;
  tx_pkts?: number | null;
  rx_pkts?: number | null;
  tx_retries?: number | null;
  rx_retries?: number | null;
  tx_bps?: number | null;
  rx_bps?: number | null;
  vlan_id?: string | number | null;
  key_mgmt?: string | null;
  dual_band?: boolean | null;
  is_guest?: boolean | null;
  [key: string]: unknown;
}

export const fetchSites = () => apiFetch<SiteInfo[]>("/api/sites");
export const fetchSite = (siteId: string) => apiFetch<SiteSummary>(`/api/sites/${siteId}`);
export const fetchSiteAps = (siteId: string) => apiFetch<ApInfo[]>(`/api/sites/${siteId}/aps`);
export const fetchSiteClients = (siteId: string) =>
  apiFetch<ClientInfo[]>(`/api/sites/${siteId}/clients`);
export const fetchApMetrics = (apId: string, hours: number) =>
  apiFetch<ApMetric[]>(`/api/aps/${apId}/metrics?hours=${hours}`);
export const fetchApRadioConfig = (apId: string, siteId?: string) =>
  apiFetch<ApRadioConfig>(`/api/aps/${apId}/radio-config${siteId ? `?site_id=${siteId}` : ""}`);
export const fetchApClients = (apId: string) =>
  apiFetch<ClientInfo[]>(`/api/aps/${apId}/clients`);

export interface ClientMetric {
  timestamp: string;
  rssi: number | null;
  snr: number | null;
  tx_rate: number | null;
  rx_rate: number | null;
  tx_bps: number | null;
  rx_bps: number | null;
  tx_bytes: number | null;
  rx_bytes: number | null;
  idle_time: number | null;
  band: string | null;
  channel: number | null;
  ap_name: string | null;
}

export const fetchClientMetrics = (mac: string, hours: number, siteId?: string) => {
  const params = new URLSearchParams({ hours: String(hours) });
  if (siteId) params.set("site_id", siteId);
  return apiFetch<ClientMetric[]>(`/api/clients/${mac}/metrics?${params.toString()}`);
};

export interface ClientListItem {
  mac: string;
  hostname: string;
}

export const fetchClientList = (siteId?: string, apMac?: string) => {
  const params = new URLSearchParams();
  if (siteId) params.set("site_id", siteId);
  if (apMac) params.set("ap_mac", apMac);
  const qs = params.toString();
  return apiFetch<ClientListItem[]>(`/api/clients/list${qs ? `?${qs}` : ""}`);
};

export interface SleMetricData {
  score: number | null;
  impact_users: number;
  total_users: number;
  classifiers?: {
    wifi_interference: number | null;
    non_wifi_interference: number | null;
    client_count: number | null;
    client_usage: number | null;
  };
  avg_sec?: number | null;
}

export interface SleData {
  capacity: SleMetricData;
  throughput: SleMetricData;
  coverage: SleMetricData;
  time_to_connect: SleMetricData;
  roaming: SleMetricData;
  ap_availability: SleMetricData;
}

export const fetchSiteSle = (siteId: string, duration = "1h") =>
  apiFetch<SleData>(`/api/sites/${siteId}/sle?duration=${duration}`);

export const fetchApSle = (apId: string, duration = "1h") =>
  apiFetch<SleData>(`/api/aps/${apId}/sle?duration=${duration}`);

export interface LogFileInfo {
  filename: string;
  size_bytes: number;
  created_at: string;
}

export const fetchLogs = () => apiFetch<{ files: LogFileInfo[]; total_bytes: number }>("/api/logs");
export const getLogDownloadUrl = (filename: string) => `${API_BASE}/api/logs/${filename}`;
export const getLogFilteredDownloadUrl = (
  filename: string,
  opts: { siteId?: string; apMac?: string; clientMac?: string } = {},
) => {
  const params = new URLSearchParams();
  if (opts.siteId) params.set("site_id", opts.siteId);
  if (opts.apMac) params.set("ap_mac", opts.apMac);
  if (opts.clientMac) params.set("client_mac", opts.clientMac);
  const qs = params.toString();
  return `${API_BASE}/api/logs/${filename}/download${qs ? `?${qs}` : ""}`;
};
export const getLogsZipUrl = (filenames: string[]) =>
  `${API_BASE}/api/logs/download-zip?files=${filenames.join(",")}`;

export async function deleteLogs(filenames: string[]): Promise<{ deleted: number }> {
  const res = await fetch(`${API_BASE}/api/logs`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filenames }),
  });
  if (!res.ok) throw new Error(`Delete error ${res.status}`);
  return res.json();
}

export interface SnapshotInfo {
  id: number;
  filename: string;
  saved_at: string;
  triggered_by: string;
  site_count: number;
  ap_count: number;
  size_bytes: number;
}

export async function pollNow(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/poll-now`, { method: "POST" });
  if (!res.ok) throw new Error(`Poll error ${res.status}`);
}

export async function createSnapshot(): Promise<SnapshotInfo> {
  const res = await fetch(`${API_BASE}/api/snapshots`, { method: "POST" });
  if (!res.ok) throw new Error(`Snapshot error ${res.status}`);
  return res.json();
}

export const fetchSnapshots = () => apiFetch<SnapshotInfo[]>("/api/snapshots");
export const getSnapshotDownloadUrl = (filename: string, siteId?: string, apId?: string) => {
  const params = new URLSearchParams();
  if (siteId) params.set("site_id", siteId);
  if (apId) params.set("ap_id", apId);
  const qs = params.toString();
  return `${API_BASE}/api/snapshots/${filename}/download${qs ? `?${qs}` : ""}`;
};

export interface Settings {
  polling_interval_seconds: number;
  log_interval_minutes: number;
  log_retention_days: number;
  timezone: string;
  monitored_site_ids: string[];
  client_polling_interval_seconds: number;
  metrics_retention_days: number;
  long_history_enabled: boolean;
}

export interface SiteSimple {
  id: string;
  name: string;
}

export const fetchSettings = () => apiFetch<Settings>("/api/settings");
export const fetchAllSites = () => apiFetch<SiteSimple[]>("/api/sites/all");

// ── Snapshot DB ──────────────────────────────────────────────────────────────

export interface SnapshotDbMeta {
  slot: number;
  saved_at: string | null;
  ap_count: number | null;
  site_count: number | null;
  from_dt: string | null;
  to_dt: string | null;
  size_bytes: number | null;
}

export interface SnapshotSite {
  id: string;
  name: string;
  ap_count: number;
}

export const fetchSnapshotDbs = () => apiFetch<SnapshotDbMeta[]>("/api/snapshot-db");

export async function createSnapshotDb(slot?: number): Promise<SnapshotDbMeta> {
  const qs = slot ? `?slot=${slot}` : "";
  const res = await fetch(`${API_BASE}/api/snapshot-db${qs}`, { method: "POST" });
  if (!res.ok) {
    let detail: string | undefined;
    try { detail = (await res.json()).detail; } catch { /* ignore */ }
    throw new Error(detail ?? `Snapshot error ${res.status}`);
  }
  return res.json();
}

export const fetchSnapshotSites = (slot: number) =>
  apiFetch<SnapshotSite[]>(`/api/snapshot-db/${slot}/sites`);

export const fetchSnapshotSiteAps = (slot: number, siteId: string) =>
  apiFetch<ApInfo[]>(`/api/snapshot-db/${slot}/sites/${siteId}/aps`);

export const fetchSnapshotMetrics = (slot: number, apId: string, hours: number) =>
  apiFetch<ApMetric[]>(`/api/snapshot-db/${slot}/aps/${apId}/metrics?hours=${hours}`);

export const fetchSnapshotRadioConfig = (slot: number, apId: string) =>
  apiFetch<ApRadioConfig>(`/api/snapshot-db/${slot}/aps/${apId}/radio-config`);

export const getSnapshotDbDownloadUrl = (slot: number, tz = "Asia/Tokyo") =>
  `${API_BASE}/api/snapshot-db/${slot}/download?tz=${encodeURIComponent(tz)}`;

export const fetchSnapshotFloorMapSites = (slot: number) =>
  apiFetch<SiteSimple[]>(`/api/snapshot-db/${slot}/floor-map/sites`);

export const fetchSnapshotFloorMapMaps = (slot: number, siteId: string) =>
  apiFetch<FloorMapInfo[]>(`/api/snapshot-db/${slot}/floor-map/sites/${siteId}/maps`);

export const fetchSnapshotFloorAps = (slot: number, siteId: string) =>
  apiFetch<FloorAp[]>(`/api/snapshot-db/${slot}/floor-map/sites/${siteId}/aps`);

export async function uploadSnapshotDb(file: File, slot?: number): Promise<SnapshotDbMeta> {
  const fd = new FormData();
  fd.append("file", file);
  const qs = slot ? `?slot=${slot}` : "";
  const res = await fetch(`${API_BASE}/api/snapshot-db/upload${qs}`, { method: "POST", body: fd });
  if (!res.ok) {
    let detail: string | undefined;
    try { detail = (await res.json()).detail; } catch { /* ignore */ }
    throw new Error(detail ?? `Upload error ${res.status}`);
  }
  return res.json();
}

// ── Floor Map ─────────────────────────────────────────────────────────────────

export interface FloorMapInfo {
  id: string;
  name: string;
  width: number | null;
  height: number | null;
  ppm?: number | null;
}

export interface FloorRadioBand {
  channel: number | null;
  bandwidth: number | null;
  tx_power: number | null;
  noise_floor: number | null;
}

export interface FloorAp {
  id: string;
  name: string;
  mac: string;
  model: string;
  status: string;
  map_id: string | null;
  x: number | null;
  y: number | null;
  num_clients: number;
  radio_24: FloorRadioBand;
  radio_5: FloorRadioBand;
  radio_6: FloorRadioBand;
}

export interface FloorMapSaveRow {
  site_id: string;
  site_name: string;
  map_id: string | null;
  map_name: string;
  ap_name: string;
  mac: string;
  model: string;
  status: string;
  band_24_channel: number | null;
  band_24_bandwidth: number | null;
  band_24_power: number | null;
  band_24_noise_floor: number | null;
  band_5_channel: number | null;
  band_5_bandwidth: number | null;
  band_5_power: number | null;
  band_5_noise_floor: number | null;
  band_6_channel: number | null;
  band_6_bandwidth: number | null;
  band_6_power: number | null;
  band_6_noise_floor: number | null;
  num_clients: number;
  x_m: number | null;
  y_m: number | null;
}

export const fetchFloorMapSites = () => apiFetch<SiteSimple[]>("/api/floor-map/sites");

export async function saveFloorMapLog(
  rows: FloorMapSaveRow[]
): Promise<{ filename: string | null; record_count: number }> {
  const res = await fetch(`${API_BASE}/api/logs/floormap/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rows),
  });
  if (!res.ok) throw new Error(`FloorMap save error ${res.status}`);
  return res.json();
}
export const fetchFloorMaps = (siteId: string) =>
  apiFetch<FloorMapInfo[]>(`/api/floor-map/sites/${siteId}/maps`);
export const fetchFloorAps = (siteId: string) =>
  apiFetch<FloorAp[]>(`/api/floor-map/sites/${siteId}/aps`);
export const getFloorMapImageUrl = (siteId: string, mapId: string) =>
  `${API_BASE}/api/floor-map/sites/${siteId}/maps/${mapId}/image`;

export async function updateSettings(
  body: Partial<Pick<Settings, "polling_interval_seconds" | "log_interval_minutes" | "log_retention_days" | "timezone" | "monitored_site_ids" | "client_polling_interval_seconds" | "metrics_retention_days" | "long_history_enabled">>
): Promise<Settings> {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail: string | undefined;
    try { detail = (await res.json()).detail; } catch { /* ignore */ }
    throw new Error(detail ?? `Settings error ${res.status}`);
  }
  return res.json();
}

// ── Tags ───────────────────────────────────────────────────────────────────────

export interface ApTagEntry {
  ap_id: string;
  site_id: string | null;
  ap_name: string | null;
  tags: string[];
}

export interface ClientTagEntry {
  mac: string;
  site_id: string | null;
  hostname: string | null;
  tags: string[];
}

export const fetchApTags = () => apiFetch<ApTagEntry[]>("/api/tags/aps");
export const fetchClientTags = () => apiFetch<ClientTagEntry[]>("/api/tags/clients");
export const fetchAllTags = () => apiFetch<string[]>("/api/tags");
export const fetchTagAps = (tag: string) =>
  apiFetch<ApInfo[]>(`/api/tags/${encodeURIComponent(tag)}/aps`);
export const fetchTagClients = (tag: string) =>
  apiFetch<ClientInfo[]>(`/api/tags/${encodeURIComponent(tag)}/clients`);

export async function putApTag(apId: string, tags: string): Promise<{ tags: string[] }> {
  const res = await fetch(`${API_BASE}/api/tags/aps/${apId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
  if (!res.ok) throw new Error(`Tag save error ${res.status}`);
  return res.json();
}

export async function putClientTag(mac: string, tags: string): Promise<{ tags: string[] }> {
  const res = await fetch(`${API_BASE}/api/tags/clients/${mac}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
  if (!res.ok) throw new Error(`Tag save error ${res.status}`);
  return res.json();
}

// ── Insights ──────────────────────────────────────────────────────────────────

export type InsightCategory =
  | "sticky_client"
  | "band24_stuck"
  | "high_retry"
  | "co_channel"
  | "flapping";

export type InsightSeverity = "critical" | "warning";

export interface InsightIssue {
  id: number;
  first_detected_at: string;
  last_detected_at: string;
  resolved_at: string | null;
  status: "active" | "resolved";
  category: InsightCategory;
  severity: InsightSeverity;
  site_id: string;
  site_name: string | null;
  target_type: "ap" | "client" | "ap_pair";
  target_id: string;
  target_name: string | null;
  detail: string | null;
  recommendation: string | null;
  metrics_json: string | null;
}

export interface InsightRecommendation {
  ap_id: string | null;
  ap_name: string | null;
  site_id: string | null;
  site_name: string | null;
  actions: string[];
}

export interface InsightsResponse {
  analyzed_at: string | null;
  summary: Record<InsightCategory, number>;
  recommendations: InsightRecommendation[];
  issues: InsightIssue[];
}

export const fetchInsights = (view?: "history") =>
  apiFetch<InsightsResponse>(`/api/insights${view ? `?view=${view}` : ""}`);

export async function analyzeInsights(): Promise<InsightsResponse> {
  const res = await fetch(`${API_BASE}/api/insights/analyze`, { method: "POST" });
  if (!res.ok) throw new Error(`Insights analyze error ${res.status}`);
  return res.json();
}

export type ImpactJudgment = "improved" | "degraded" | "neutral" | "no_data";
export type ImpactVerdict = "improved" | "degraded" | "neutral" | "insufficient_data";

export interface ImpactMetric {
  key: string;
  label: string;
  unit: string;
  before: number | null;
  after: number | null;
  change_pct: number | null;
  judgment: ImpactJudgment;
}

export interface ConfigImpact {
  change_id: number;
  ap_id: string;
  ap_name: string | null;
  site_id: string | null;
  band: string;
  changed_field: string;
  old_value: string | null;
  new_value: string | null;
  detected_at: string;
  before_hours: number;
  after_hours: number;
  verdict: ImpactVerdict;
  metrics: ImpactMetric[];
}

export const fetchConfigImpact = (changeId: number) =>
  apiFetch<ConfigImpact>(`/api/insights/config-impact?change_id=${changeId}`);

export interface RecentConfigChange {
  id: number;
  ap_id: string;
  ap_name: string | null;
  site_id: string | null;
  band: string;
  changed_field: string;
  old_value: string | null;
  new_value: string | null;
  detected_at: string;
}

export const fetchRecentConfigChanges = (days = 7) =>
  apiFetch<RecentConfigChange[]>(`/api/insights/config-changes?days=${days}`);

export interface RoamingEvent {
  timestamp: string;
  from_ap_id: string;
  from_ap_name: string | null;
  to_ap_id: string;
  to_ap_name: string | null;
  rssi_before: number | null;
  rssi_after: number | null;
  band_before: string | null;
  band_after: string | null;
}

export const fetchClientRoaming = (mac: string, hours = 72, siteId?: string) => {
  const params = new URLSearchParams({ hours: String(hours) });
  if (siteId) params.set("site_id", siteId);
  return apiFetch<RoamingEvent[]>(`/api/clients/${mac}/roaming?${params.toString()}`);
};

export interface ApEvent {
  timestamp: string | null;
  type: string;
  reason: string | null;
  band: string | null;
  channel: number | null;
  pre_channel: number | null;
  bandwidth: number | null;
  pre_bandwidth: number | null;
  is_restart: boolean;
}

export const fetchApEvents = (apId: string, hours = 24) =>
  apiFetch<{ events: ApEvent[] }>(`/api/aps/${apId}/events?hours=${hours}`);

export interface ApEventsBackfillResult {
  sites_processed: number;
  new_events: number;
  skipped_existing: number;
  csv_file: string | null;
  errors: { site_name: string; error: string }[];
}

export async function backfillApEvents(days = 7): Promise<ApEventsBackfillResult> {
  const res = await fetch(`${API_BASE}/api/ap-events/backfill?days=${days}`, { method: "POST" });
  if (!res.ok) throw new Error(`Backfill error ${res.status}`);
  return res.json();
}

// ── Credentials (Environments) ────────────────────────────────────────────────

export interface CredentialItem {
  id: number;
  name: string;
  mist_api_token: string; // 先頭10文字のみ（マスク済み）
  mist_org_id: string;
  mist_base_url: string;
  is_active: boolean;
  created_at: string | null;
}

export interface CredentialsResponse {
  items: CredentialItem[];
  secret_required: boolean;
}

export const fetchCredentials = () => apiFetch<CredentialsResponse>("/api/credentials");

async function credentialsRequest<T>(
  path: string,
  method: string,
  body?: unknown,
  settingsKey?: string
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (settingsKey) headers["X-Settings-Key"] = settingsKey;
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    let detail: string | undefined;
    try { detail = (await res.json()).detail; } catch { /* ignore */ }
    throw new Error(detail ?? `Credentials error ${res.status}`);
  }
  return res.json();
}

export interface CredentialInput {
  name: string;
  mist_api_token: string;
  mist_org_id: string;
  mist_base_url: string;
}

export const createCredential = (body: CredentialInput, settingsKey?: string) =>
  credentialsRequest<CredentialItem>("/api/credentials", "POST", body, settingsKey);

export const updateCredential = (
  id: number,
  body: Partial<CredentialInput>,
  settingsKey?: string
) => credentialsRequest<CredentialItem>(`/api/credentials/${id}`, "PUT", body, settingsKey);

export const deleteCredential = (id: number, settingsKey?: string) =>
  credentialsRequest<{ status: string; deleted: number }>(
    `/api/credentials/${id}`, "DELETE", undefined, settingsKey
  );

export const activateCredential = (
  id: number,
  body: { clear_logs: boolean; clear_snapshots: boolean },
  settingsKey?: string
) =>
  credentialsRequest<{ status: string; activated: string }>(
    `/api/credentials/${id}/activate`, "POST", body, settingsKey
  );

// ── Hang AP analysis (/api/hangap) ────────────────────────────────────────────
// 結果の整形（列・順序・書式）は API が返すものをそのまま使う。ここで再計算・
// 再整形すると CLI / API / UI で結果が食い違う。

export type HangapStatus = "running" | "done" | "failed";
export type HangapPhase = "loading" | "neighbors" | "detecting" | "writing";

export interface HangapFileStat {
  file_type: string;
  files: number;
  rows: number;
  duplicates_removed: number;
  loaded: number;
}

export interface HangapSitePeriod {
  site_id: string;
  site_name: string;
  rows: number;
  ap_count: number;
  first: string | null;
  last: string | null;
}

export interface HangapLoaderInfo {
  files_scanned: number;
  gap_factor: number;
  file_stats: HangapFileStat[];
  unclassified: number;
  sampling_interval_seconds: number | null;
  interval_groups: { interval_seconds: number; ap_count: number }[];
  gaps: {
    count: number;
    total_seconds: number;
    max_seconds: number;
    total_missing_samples: number;
  };
  metrics_period: (string | null)[] | null;
  events_period: (string | null)[] | null;
  metrics_rows: number;
  events_rows: number;
  ap_count: number;
  rf_neighbors_rows: number;
  rf_neighbors_latest: string | null;
  site_periods: HangapSitePeriod[];
  report_text: string;
}

export interface HangapSummary {
  detected_intervals: number;
  /** 回復状況の内訳（キーはバックエンドの STATUS_ORDER。フロントで定義し直さない） */
  recovery_status: Record<string, number>;
  /** 周辺AP判定の内訳（キーはバックエンドの VERDICT_ORDER） */
  neighbor_verdict: Record<string, number>;
  exodus_suspected: number;
  event_matched_intervals: number;
  condition_text: string;
  result_summary_text: string;
  loader: HangapLoaderInfo;
}

export interface HangapJob {
  job_id: string;
  status: HangapStatus;
  phase: HangapPhase;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  summary: HangapSummary | null;
  warnings: string[];
}

/** 結果セルの値。時刻はログ由来の naive な文字列（UTC 変換はしない）。 */
export type HangapCell = string | number | boolean | null;

/** 列ごとの絞り込みの入力方法。**フロントで列を分類し直さない**（API が返すものを使う） */
export type HangapColumnKind = "text" | "enum" | "number" | "time" | "bool";

/**
 * 結果テーブルの 1 ページ。実行中ジョブの結果（jobs/{id}/result）と
 * 保存済み結果（results/{name}/rows）で**同じ形**が返る。
 */
export interface HangapResultPage {
  /** 実行中ジョブの結果なら job_id、保存済み結果なら null */
  job_id: string | null;
  /** 保存済み結果なら保存名、実行中ジョブの結果なら null */
  name: string | null;
  total: number;
  offset: number;
  limit: number;
  columns: string[];
  column_kinds: Record<string, HangapColumnKind>;
  /** 値の選択で絞り込む列 → 選択肢（バックエンドの STATUS_ORDER / VERDICT_ORDER） */
  enum_choices: Record<string, string[]>;
  rows: Record<string, HangapCell>[];
}

/**
 * 列ごとの絞り込み条件。**絞り込みはサーバ側で適用する**（ページングと併用するため、
 * 表示中のページだけをクライアントで絞ってはいけない）。
 */
export type HangapFilter =
  | { kind: "text"; text: string }
  | { kind: "enum"; values: string[] }
  | { kind: "number"; min: string; max: string }
  | { kind: "time"; from: string; to: string }
  | { kind: "bool"; value: boolean | null };

/** 列名 → 条件。複数列は AND で結合される（サーバ側の仕様）。 */
export type HangapFilters = Record<string, HangapFilter>;

/** 値が入っていて実際に絞り込みが効く条件か */
export function isHangapFilterActive(f: HangapFilter | undefined): boolean {
  if (!f) return false;
  switch (f.kind) {
    case "text": return f.text.trim() !== "";
    case "enum": return f.values.length > 0;
    case "number": return f.min.trim() !== "" || f.max.trim() !== "";
    case "time": return f.from.trim() !== "" || f.to.trim() !== "";
    case "bool": return f.value !== null;
  }
}

/** 条件を API の `filter=列名:演算子:値` に直す（空の値は送らない） */
export function hangapFilterSpecs(filters: HangapFilters): string[] {
  const specs: string[] = [];
  for (const [column, f] of Object.entries(filters)) {
    const push = (op: string, value: string) => {
      const v = value.trim();
      if (v !== "") specs.push(`${column}:${op}:${v}`);
    };
    switch (f.kind) {
      case "text": push("contains", f.text); break;
      // 同じ列の複数指定は OR（サーバ側で束ねる）
      case "enum": for (const v of f.values) push("in", v); break;
      case "number": push("min", f.min); push("max", f.max); break;
      case "time": push("from", f.from); push("to", f.to); break;
      case "bool":
        if (f.value !== null) specs.push(`${column}:is:${f.value ? "true" : "false"}`);
        break;
    }
  }
  return specs;
}

/**
 * 分析対象の選択肢になるサイト。**`data/logs` に実際に含まれるサイト**であり、
 * `/api/sites`（現在の監視対象）とは別物。環境を切り替えると、監視していない
 * サイトのログが残ることがある。
 */
export interface HangapLogSite {
  site_id: string;
  site_name: string;
  ap_count: number;
  rows: number;
  files: number;
  /** ログ中の時刻表記そのまま（タイムゾーンなし） */
  first: string | null;
  last: string | null;
}

export interface HangapLogSites {
  sites: HangapLogSite[];
  files_scanned: number;
  metrics_files: number;
  scanned_at: string | null;
  /** キャッシュを返したか（再取得ボタンの結果を判断するため） */
  cached: boolean;
}

export async function fetchHangapLogSites(refresh = false): Promise<HangapLogSites> {
  const res = await fetch(`${API_BASE}/api/hangap/sites${refresh ? "?refresh=true" : ""}`);
  if (!res.ok) throw new Error((await hangapDetail(res)) ?? `Hang AP sites error ${res.status}`);
  return res.json();
}

/** 分析条件。未指定（undefined）の項目は送らず、バックエンドの既定値に任せる。 */
export interface HangapAnalyzeBody {
  from?: string;
  to?: string;
  /** 対象サイトの site_id。**省略するとすべてのサイトが対象**（明示的に選んだときだけ送る） */
  sites?: string[];
  min_zero_samples?: number;
  event_window_minutes?: number;
  exodus_threshold?: number;
  gap_factor?: number;
  neighbor_count?: number;
  max_distance_m?: number;
  neighbor_client_threshold?: number;
  truncated_warn_ratio?: number;
}

export interface HangapStartResult {
  job_id: string;
  /** 409（別の分析が実行中）。その実行中ジョブの job_id を返す */
  conflict: boolean;
  message?: string;
}

async function hangapDetail(res: Response): Promise<string | undefined> {
  try {
    const detail = (await res.json()).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") return detail.message;
  } catch {
    /* ignore */
  }
  return undefined;
}

export async function startHangapAnalysis(body: HangapAnalyzeBody): Promise<HangapStartResult> {
  const res = await fetch(`${API_BASE}/api/hangap/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 409) {
    let detail: { message?: string; job_id?: string } = {};
    try { detail = (await res.json()).detail ?? {}; } catch { /* ignore */ }
    return {
      job_id: detail.job_id ?? "",
      conflict: true,
      message: detail.message ?? "別の分析が実行中です。",
    };
  }
  if (!res.ok) throw new Error((await hangapDetail(res)) ?? `Hang AP analyze error ${res.status}`);
  return { job_id: (await res.json()).job_id, conflict: false };
}

/** ジョブの状態。破棄済み・TTL 切れ（404）は null を返す。 */
export async function fetchHangapJob(jobId: string): Promise<HangapJob | null> {
  const res = await fetch(`${API_BASE}/api/hangap/jobs/${jobId}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error((await hangapDetail(res)) ?? `Hang AP job error ${res.status}`);
  return res.json();
}

/** 結果テーブルの取得条件（実行中ジョブ / 保存済み結果で共通） */
export interface HangapRowsQuery {
  offset?: number;
  limit?: number;
  sort?: string;
  order?: "asc" | "desc";
  filters?: HangapFilters;
}

function hangapRowsQuery(opts: HangapRowsQuery): string {
  const qs = new URLSearchParams();
  if (opts.offset) qs.set("offset", String(opts.offset));
  if (opts.limit) qs.set("limit", String(opts.limit));
  if (opts.sort) {
    qs.set("sort", opts.sort);
    qs.set("order", opts.order ?? "asc");
  }
  for (const spec of hangapFilterSpecs(opts.filters ?? {})) qs.append("filter", spec);
  return qs.toString();
}

export async function fetchHangapResult(
  jobId: string,
  opts: HangapRowsQuery = {}
): Promise<HangapResultPage> {
  const res = await fetch(
    `${API_BASE}/api/hangap/jobs/${jobId}/result?${hangapRowsQuery(opts)}`
  );
  if (!res.ok) throw new Error((await hangapDetail(res)) ?? `Hang AP result error ${res.status}`);
  return res.json();
}

/** ダウンロードは常に全列（API の出力をそのまま渡す） */
export const getHangapDownloadUrl = (jobId: string, format: "xlsx" | "csv") =>
  `${API_BASE}/api/hangap/jobs/${jobId}/download?format=${format}`;

// ── 保存済みの分析結果（data/hangap_results） ─────────────────────────────────
// 分析が done で完了すると自動で保存される（保存ボタンは無い）。結果テーブルは
// results/{name}/rows で読み戻して画面に再表示できる（再分析はしない）。

/** 1 組（xlsx / csv / json）の概要。値は保存時の json をそのまま返したもの。 */
export interface HangapSavedResult {
  /** hangap_result_YYYYMMDD_HHMMSS。ダウンロード・削除のキー */
  name: string;
  saved_at: string | null;
  detected_intervals: number;
  recovery_status: Record<string, number>;
  neighbor_verdict: Record<string, number>;
  exodus_suspected: number;
  event_matched_intervals: number;
  condition_text: string;
  warning_count: number;
  warnings: string[];
  metrics_period: (string | null)[] | null;
  events_period: (string | null)[] | null;
  ap_count: number;
  files_scanned: number;
  /** 拡張子ごとのバイト数（保存が途中で落ちた組では欠けることがある） */
  files: Partial<Record<"xlsx" | "csv" | "json", number>>;
  total_bytes: number;
}

/** 新しい順。 */
export async function fetchHangapSavedResults(): Promise<HangapSavedResult[]> {
  const res = await fetch(`${API_BASE}/api/hangap/results`);
  if (!res.ok) throw new Error((await hangapDetail(res)) ?? `Hang AP results error ${res.status}`);
  return (await res.json()).results;
}

/**
 * 保存済み結果の行。`fetchHangapResult` と同じ形が返るので、表示は同じ
 * コンポーネントで行う（**別実装を作らないこと**）。ダウンロードは常に全行・全列で、
 * ここでの絞り込みの影響を受けない。
 */
export async function fetchHangapSavedRows(
  name: string,
  opts: HangapRowsQuery = {}
): Promise<HangapResultPage> {
  const res = await fetch(
    `${API_BASE}/api/hangap/results/${encodeURIComponent(name)}/rows?${hangapRowsQuery(opts)}`
  );
  if (!res.ok) throw new Error((await hangapDetail(res)) ?? `Hang AP saved rows error ${res.status}`);
  return res.json();
}

export const getHangapSavedDownloadUrl = (name: string, format: "xlsx" | "csv") =>
  `${API_BASE}/api/hangap/results/${encodeURIComponent(name)}/download?format=${format}`;

/** 1 組（xlsx/csv/json）をまとめて削除する。 */
export async function deleteHangapSavedResult(name: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/hangap/results/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error((await hangapDetail(res)) ?? `Hang AP delete error ${res.status}`);
}

// ── 仮名化ダウンロード（/api/pseudonymize） ───────────────────────────────────
// 仮名化版のファイルはサーバに作り置きしない。ダウンロードのたびにその場で変換する。
// 同じ AP は常に同じ仮名になる（サーバ側でソルトとマッピングを永続化している）。
//
// **仮名化であって匿名化ではない。** AP 台数・接続端末数の規模やイベントの発生
// パターンは残るため、再識別のリスクはゼロにならない。

/** 一度に仮名化できるファイル数の上限（サーバ側の既定。/limits で取り直せる） */
export const PSEUDONYMIZE_MAX_FILES = 50;

/** 仮名化であって匿名化ではない旨の注意書き（導線の近くに必ず出す） */
export const PSEUDONYMIZE_NOTICE =
  "仮名化であって匿名化ではありません。AP名・サイト名・MAC・IP・時刻は置き換わりますが、" +
  "AP台数や接続端末数の規模、イベントの発生パターンは残るため、再識別のリスクはゼロになりません。";

export const getPseudonymizedLogsUrl = (filenames: string[]) =>
  `${API_BASE}/api/pseudonymize/logs?files=${encodeURIComponent(filenames.join(","))}`;

/** csv のみ。xlsx はタイトル・分析条件の自由記述が仮名化できないため対象外。 */
export const getPseudonymizedResultUrl = (name: string) =>
  `${API_BASE}/api/pseudonymize/results/${encodeURIComponent(name)}?format=csv`;

function filenameFromResponse(res: Response, fallback: string): string {
  // サーバはタイムシフト後の名前を付ける（ファイル名に実日付が残ると台無しになる）。
  // CORS で Content-Disposition を expose しているので読める。
  const cd = res.headers.get("Content-Disposition") ?? "";
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      /* 壊れていたら下の filename= を使う */
    }
  }
  const plain = /filename="([^"]+)"/i.exec(cd);
  return plain ? plain[1] : fallback;
}

/**
 * 仮名化ダウンロードを実行する。leak check の発火などはサーバが本文を返さずに
 * エラーにするので、`<a download>` ではなく fetch で受けて呼び出し側に投げる。
 */
export async function downloadPseudonymized(url: string, fallbackName: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) {
    let message = `仮名化ダウンロードに失敗しました（${res.status}）`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* JSON でなければ既定のメッセージ */
    }
    throw new Error(message);
  }
  const blob = await res.blob();
  const name = filenameFromResponse(res, fallbackName);
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
  return name;
}


// ── 仮名化の復元（/api/pseudonymize/restore） ─────────────────────────────────
// 加工・統合したあとのファイルは利用者の手元にあるので、復元はアップロードで行う。
// アップロードされたファイルはサーバの一時ディレクトリで処理し、処理後に削除される。
//
// **復元後のファイルは実名を含む。** 導線の近くに必ずこの注意書きを出す。

/** 復元後のファイルが実名を含む旨の注意書き（導線の近くに必ず出す） */
export const RESTORE_NOTICE =
  "復元後のファイルは実名（AP名・サイト名・MAC・IP・実時刻）を含みます。" +
  "仮名化前と同じ扱いが必要です。共有先・保存先に注意してください。";

/** 復元できない値がある旨の注意（vlan_id は仮名が裸の整数のため戻せない） */
export const RESTORE_LIMITS_NOTICE =
  "戻せるのはマッピングに記録された値だけです。加工で生まれた集計値や新しいラベルはそのまま通ります。" +
  "vlan_id は仮名が数値のため復元できません。";

/** アップロードの既定上限（サーバ側の値。/limits で取り直せる） */
export const RESTORE_MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

export interface RestoreResidual {
  kind: string;
  column: string;
  sheet: string;
  count: number;
  rows: number[];
}

export interface RestoreFileReport {
  source_name: string;
  filename: string;
  counts: Record<string, number>;
  total_replacements: number;
  residuals: RestoreResidual[];
  residual_total: number;
}

export interface RestoreReport {
  files: RestoreFileReport[];
  counts: Record<string, number>;
  residual_total: number;
}

export interface PseudonymizeLimits {
  max_files: number;
  restore_max_files: number;
  restore_max_upload_bytes: number;
  restore_extensions: string[];
}

export const fetchPseudonymizeLimits = (): Promise<PseudonymizeLimits> =>
  apiFetch<PseudonymizeLimits>("/api/pseudonymize/limits");

/** 置換件数のキー → 表示名（バックエンドの COUNT_LABELS と対にする） */
export const RESTORE_COUNT_LABELS: Record<string, string> = {
  AP_NAME: "AP名",
  AP_MAC: "AP MAC",
  AP_ID: "AP ID",
  SITE_NAME: "サイト名",
  SITE_ID: "サイト ID",
  MAP_NAME: "フロア名",
  MAP_ID: "フロア ID",
  CLIENT_MAC: "クライアント MAC",
  HOSTNAME: "ホスト名",
  SSID: "SSID",
  IP: "IP",
  TIMESTAMP: "時刻",
  TIMESTAMP_COMPACT: "時刻（YYYYMMDD_HHMM）",
  FILENAME: "ファイル名の日付",
};

/** 残存警告の種類 → 表示名 */
export const RESTORE_RESIDUAL_LABELS: Record<string, string> = {
  AP_NAME: "AP名",
  SITE_NAME: "サイト名",
  MAP_NAME: "フロア名",
  HOSTNAME: "ホスト名",
  SSID: "SSID",
  MAC: "MAC",
  UUID: "UUID",
};

function decodeRestoreReport(res: Response): RestoreReport | null {
  // レポートはヘッダーに base64(UTF-8 JSON) で載ってくる（本文はファイルそのもの）
  const raw = res.headers.get("X-Restore-Report");
  if (!raw) return null;
  try {
    const bytes = Uint8Array.from(atob(raw), (ch) => ch.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes)) as RestoreReport;
  } catch {
    return null;
  }
}

/**
 * 仮名化されたファイルをアップロードして復元し、ダウンロードさせる。
 * 戻り値は (保存したファイル名, 復元レポート)。
 */
export async function restorePseudonymized(
  files: File[],
  noTime: boolean,
): Promise<{ filename: string; report: RestoreReport | null }> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const res = await fetch(
    `${API_BASE}/api/pseudonymize/restore?no_time=${noTime ? "true" : "false"}`,
    { method: "POST", body: form },
  );
  if (!res.ok) {
    let message = `復元に失敗しました（${res.status}）`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* JSON でなければ既定のメッセージ */
    }
    throw new Error(message);
  }
  const report = decodeRestoreReport(res);
  const blob = await res.blob();
  const filename = filenameFromResponse(
    res,
    files.length === 1 ? `restored_${files[0].name}` : "restored_files.zip",
  );
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
  return { filename, report };
}
