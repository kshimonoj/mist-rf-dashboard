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
  text: string;
}

export const fetchApEvents = (apId: string, duration = "1d") =>
  apiFetch<{ events: ApEvent[] }>(`/api/aps/${apId}/events?duration=${duration}`);

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
