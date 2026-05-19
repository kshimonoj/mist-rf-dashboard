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

export const fetchSites = () => apiFetch<SiteInfo[]>("/api/sites");
export const fetchSite = (siteId: string) => apiFetch<SiteSummary>(`/api/sites/${siteId}`);
export const fetchSiteAps = (siteId: string) => apiFetch<ApInfo[]>(`/api/sites/${siteId}/aps`);
export const fetchApMetrics = (apId: string, hours: number) =>
  apiFetch<ApMetric[]>(`/api/aps/${apId}/metrics?hours=${hours}`);
export const fetchApRadioConfig = (apId: string, siteId?: string) =>
  apiFetch<ApRadioConfig>(`/api/aps/${apId}/radio-config${siteId ? `?site_id=${siteId}` : ""}`);

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
  body: Partial<Pick<Settings, "polling_interval_seconds" | "log_interval_minutes" | "log_retention_days" | "timezone" | "monitored_site_ids">>
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
