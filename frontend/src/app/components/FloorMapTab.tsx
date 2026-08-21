"use client";

import { EyeOff } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import {
  fetchFloorMapSites,
  fetchFloorMaps,
  fetchFloorAps,
  fetchSnapshotFloorMapSites,
  fetchSnapshotFloorMapMaps,
  fetchSnapshotFloorAps,
  getFloorMapImageUrl,
  FloorMapInfo,
  FloorAp,
  FloorMapSaveRow,
  SiteSimple,
} from "@/lib/api";
import { useMask } from "@/app/providers";
import { FLOOR_MAP_BLOCKED_TITLE } from "@/lib/mask";

export interface FloorMapTabHandle {
  getRows: () => FloorMapSaveRow[] | null;
}

// ── Color utilities ────────────────────────────────────────────────────────────

const CHANNEL_COLORS = [
  "#4E79A7",  // 青
  "#F28E2B",  // オレンジ
  "#59A14F",  // 緑
  "#E15759",  // 赤
  "#76B7B2",  // シアン
  "#B07AA1",  // 紫
  "#F1CE63",  // 黄
  "#FF9DA7",  // ピンク
  "#9467BD",  // 青紫
  "#8CD17D",  // 薄緑
  "#17BECF",  // 水色
  "#AEC7E8",  // 淡青
];

function buildChannelColorMap(channels: number[]): Record<number, string> {
  const sorted = Array.from(new Set(channels)).sort((a, b) => a - b);
  const map: Record<number, string> = {};
  sorted.forEach((ch, i) => { map[ch] = CHANNEL_COLORS[i % CHANNEL_COLORS.length]; });
  return map;
}

type BandKey = "radio_24" | "radio_5" | "radio_6";
const BAND_OPTIONS: { key: BandKey; label: string; band: "24" | "5" | "6" }[] = [
  { key: "radio_24", label: "2.4 GHz", band: "24" },
  { key: "radio_5",  label: "5 GHz",   band: "5"  },
  { key: "radio_6",  label: "6 GHz",   band: "6"  },
];

// ── Tooltip ───────────────────────────────────────────────────────────────────

function ApTooltip({ ap }: { ap: FloorAp }) {
  const bands = [
    { label: "2.4G", radio: ap.radio_24 },
    { label: "5G",   radio: ap.radio_5  },
    { label: "6G",   radio: ap.radio_6  },
  ];

  return (
    <div
      className="absolute z-50 pointer-events-none w-56 text-xs rounded-lg p-3 shadow-xl border"
      style={{
        left: "18px",
        top: "-8px",
        backgroundColor: "var(--bg-card)",
        borderColor: "var(--border-cyan)",
        color: "var(--text-secondary)",
      }}
    >
      <p className="font-bold mb-1" style={{ color: "var(--cyan)" }}>
        {ap.name || ap.id}
      </p>
      <p style={{ color: "var(--text-muted)" }}>{ap.model}</p>
      <p>
        Clients:{" "}
        <span style={{ color: "var(--text-primary)" }}>{ap.num_clients}</span>
      </p>
      <div className="mt-2 space-y-1">
        {bands.map(({ label, radio }) => {
          if (!radio.channel) return null;
          return (
            <div key={label} className="flex gap-2">
              <span
                className="w-7 shrink-0 font-bold"
                style={{ color: "var(--text-muted)" }}
              >
                {label}
              </span>
              <span>
                Ch{radio.channel}
                {radio.bandwidth ? `/${radio.bandwidth}MHz` : ""}
              </span>
              {radio.tx_power != null && (
                <span style={{ color: "var(--cyan)" }}>{radio.tx_power}dBm</span>
              )}
              {radio.noise_floor != null && (
                <span style={{ color: "var(--text-muted)" }}>
                  NF:{radio.noise_floor}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── AP Marker ─────────────────────────────────────────────────────────────────

interface ApMarkerProps {
  ap: FloorAp;
  map: FloorMapInfo;
  activeBand: BandKey;
  channelColorMap: Record<number, string>;
}

function ApMarker({ ap, map, activeBand, channelColorMap }: ApMarkerProps) {
  const [show, setShow] = useState(false);

  if (ap.x == null || ap.y == null || !map.width || !map.height) return null;

  const left = (ap.x / map.width) * 100;
  const top = (ap.y / map.height) * 100;
  const channel = ap[activeBand]?.channel;
  const color = channel ? (channelColorMap[channel] ?? "#475569") : "#475569";
  const isOffline = ap.status !== "connected";

  return (
    <div
      className="absolute"
      style={{
        left: `${left}%`,
        top: `${top}%`,
        transform: "translate(-50%, -50%)",
        zIndex: 10,
      }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {/* Outer ring */}
      <div
        className="w-5 h-5 rounded-full flex items-center justify-center"
        style={{
          backgroundColor: color,
          opacity: isOffline ? 0.35 : 0.85,
          boxShadow: isOffline ? "none" : `0 0 6px ${color}99`,
          border: "2px solid rgba(255,255,255,0.6)",
        }}
      >
        {channel && (
          <span
            className="text-[7px] font-bold leading-none select-none"
            style={{ color: "#000", opacity: 0.8 }}
          >
            {channel}
          </span>
        )}
      </div>
      {show && <ApTooltip ap={ap} />}
    </div>
  );
}

// ── Interference Summary ───────────────────────────────────────────────────────

function InterferenceTable({ aps }: { aps: FloorAp[] }) {
  const sections = BAND_OPTIONS.map(({ key, label, band }) => {
    const chMap = new Map<number, string[]>();
    for (const ap of aps) {
      const ch = ap[key]?.channel;
      if (!ch) continue;
      if (!chMap.has(ch)) chMap.set(ch, []);
      chMap.get(ch)!.push(ap.name || ap.id);
    }
    const rows = Array.from(chMap.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([ch, names]) => ({ ch, names, interfering: names.length >= 2 }));
    const colorMap = buildChannelColorMap(rows.map((r) => r.ch));
    return { label, band, rows, colorMap };
  }).filter((s) => s.rows.length > 0);

  if (sections.length === 0) return null;

  return (
    <div className="mt-6">
      <h3
        className="text-sm font-bold mb-3 tracking-wider uppercase"
        style={{ color: "var(--text-muted)" }}
      >
        同一チャネル干渉サマリー
      </h3>
      <div className="space-y-4">
        {sections.map(({ label, band, rows, colorMap }) => (
          <div key={label}>
            <p
              className="text-xs font-bold mb-1 tracking-wider"
              style={{ color: "var(--cyan)" }}
            >
              {label}
            </p>
            <table className="w-full text-xs font-mono border-collapse">
              <thead>
                <tr
                  className="border-b"
                  style={{ borderColor: "var(--border-cyan)" }}
                >
                  {["Channel", "AP数", "AP一覧", "干渉"].map((h) => (
                    <th
                      key={h}
                      className="text-left py-1.5 px-2 font-normal"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(({ ch, names, interfering }) => (
                  <tr
                    key={ch}
                    className="border-b"
                    style={{ borderColor: "var(--chart-grid)" }}
                  >
                    <td className="py-1.5 px-2">
                      <span
                        className="inline-flex items-center gap-1.5"
                      >
                        <span
                          className="w-2.5 h-2.5 rounded-full inline-block shrink-0"
                          style={{ backgroundColor: colorMap[ch] ?? "#475569" }}
                        />
                        <span style={{ color: "var(--text-primary)" }}>
                          {ch}
                        </span>
                      </span>
                    </td>
                    <td
                      className="py-1.5 px-2"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {names.length}
                    </td>
                    <td
                      className="py-1.5 px-2"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      {names.join(", ")}
                    </td>
                    <td className="py-1.5 px-2">
                      {interfering ? (
                        <span
                          className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                          style={{
                            backgroundColor: "rgba(255,68,68,0.15)",
                            color: "var(--red)",
                          }}
                        >
                          干渉
                        </span>
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface FloorMapTabProps {
  snapshotSlot?: number;
}

const FloorMapTab = forwardRef<FloorMapTabHandle, FloorMapTabProps>(
function FloorMapTab({ snapshotSlot } = {}, ref) {
  const { masked } = useMask();
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [selectedMapId, setSelectedMapId] = useState("");
  const [activeBandKey, setActiveBandKey] = useState<BandKey>("radio_5");
  const [imgError, setImgError] = useState(false);
  const prevSiteId = useRef("");

  const isSnapshot = snapshotSlot != null;
  const prefix = isSnapshot ? `snap${snapshotSlot}` : "live";

  const { data: sites } = useSWR<SiteSimple[]>(
    `${prefix}-floor-map-sites`,
    isSnapshot
      ? () => fetchSnapshotFloorMapSites(snapshotSlot!)
      : fetchFloorMapSites
  );

  const { data: maps } = useSWR<FloorMapInfo[]>(
    selectedSiteId ? `${prefix}-floor-map-maps-${selectedSiteId}` : null,
    isSnapshot
      ? () => fetchSnapshotFloorMapMaps(snapshotSlot!, selectedSiteId)
      : () => fetchFloorMaps(selectedSiteId)
  );

  const { data: aps, isLoading: apsLoading } = useSWR<FloorAp[]>(
    selectedSiteId ? `${prefix}-floor-map-aps-${selectedSiteId}` : null,
    isSnapshot
      ? () => fetchSnapshotFloorAps(snapshotSlot!, selectedSiteId)
      : () => fetchFloorAps(selectedSiteId)
  );

  // Expose getRows() to parent via ref (live mode only)
  useImperativeHandle(ref, () => ({
    getRows: (): FloorMapSaveRow[] | null => {
      if (isSnapshot || !aps || aps.length === 0 || !selectedSiteId) return null;
      const siteName = sites?.find((s) => s.id === selectedSiteId)?.name ?? "";
      const mapIndex = new Map((maps ?? []).map((m) => [m.id, m]));
      return aps.map((ap) => {
        const mapInfo = ap.map_id ? mapIndex.get(ap.map_id) : undefined;
        const ppm = mapInfo?.ppm ?? null;
        return {
          site_id: selectedSiteId,
          site_name: siteName,
          map_id: ap.map_id ?? null,
          map_name: mapInfo?.name ?? "",
          ap_name: ap.name,
          mac: ap.mac ?? "",
          model: ap.model,
          status: ap.status,
          band_24_channel: ap.radio_24.channel,
          band_24_bandwidth: ap.radio_24.bandwidth,
          band_24_power: ap.radio_24.tx_power,
          band_24_noise_floor: ap.radio_24.noise_floor,
          band_5_channel: ap.radio_5.channel,
          band_5_bandwidth: ap.radio_5.bandwidth,
          band_5_power: ap.radio_5.tx_power,
          band_5_noise_floor: ap.radio_5.noise_floor,
          band_6_channel: ap.radio_6.channel,
          band_6_bandwidth: ap.radio_6.bandwidth,
          band_6_power: ap.radio_6.tx_power,
          band_6_noise_floor: ap.radio_6.noise_floor,
          num_clients: ap.num_clients,
          x_m: ap.x != null && ppm ? Math.round((ap.x / ppm) * 100) / 100 : null,
          y_m: ap.y != null && ppm ? Math.round((ap.y / ppm) * 100) / 100 : null,
        };
      });
    },
  }), [isSnapshot, aps, selectedSiteId, sites, maps]);

  // Auto-select first site
  useEffect(() => {
    if (sites && sites.length > 0 && !selectedSiteId) {
      setSelectedSiteId(sites[0].id);
    }
  }, [sites, selectedSiteId]);

  // Auto-select first map; reset image error on site change
  useEffect(() => {
    if (prevSiteId.current !== selectedSiteId) {
      prevSiteId.current = selectedSiteId;
      setSelectedMapId("");
      setImgError(false);
    }
    if (maps && maps.length > 0 && !selectedMapId) {
      setSelectedMapId(maps[0].id);
      setImgError(false);
    }
  }, [maps, selectedSiteId, selectedMapId]);

  const selectedMap = maps?.find((m) => m.id === selectedMapId) ?? null;
  const mapAps = aps?.filter((ap) => ap.map_id === selectedMapId) ?? [];

  const channelColorMap = useMemo(() => {
    const channels = mapAps
      .map((ap) => ap[activeBandKey]?.channel)
      .filter((ch): ch is number => ch != null);
    return buildChannelColorMap(channels);
  }, [mapAps, activeBandKey]);

  const imageUrl =
    selectedSiteId && selectedMapId
      ? getFloorMapImageUrl(selectedSiteId, selectedMapId)
      : null;

  const showGrid = !imageUrl || imgError;

  // 背景画像は <img src> で直接参照しており JSON を経由しないため、lib/api.ts の
  // フィールド名変換が原理的に効かない（部屋名・棟名が画像に焼き込まれている）。
  // 一律で内容を表示しない（URL 直叩き・ブックマークからの直接遷移でも効く）。
  if (masked) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border h-40 gap-2"
        style={{
          borderColor: "var(--border-cyan)",
          backgroundColor: "var(--bg-card)",
          color: "var(--text-muted)",
        }}
        role="status"
      >
        <EyeOff className="w-4 h-4 shrink-0" />
        <p className="text-sm">{FLOOR_MAP_BLOCKED_TITLE}</p>
      </div>
    );
  }

  return (
    <div>
      {/* Controls */}
      <div className="flex flex-wrap items-end gap-4 mb-6">
        <div>
          <label
            className="block text-xs mb-1"
            style={{ color: "var(--text-muted)" }}
          >
            サイト
          </label>
          <select
            value={selectedSiteId}
            onChange={(e) => {
              setSelectedSiteId(e.target.value);
              setSelectedMapId("");
              setImgError(false);
            }}
            className="px-3 py-1.5 rounded border text-sm"
            style={{
              borderColor: "var(--border-cyan)",
              backgroundColor: "var(--bg-card)",
              color: "var(--text-primary)",
            }}
          >
            <option value="">-- 選択 --</option>
            {sites?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            className="block text-xs mb-1"
            style={{ color: "var(--text-muted)" }}
          >
            フロア / マップ
          </label>
          <select
            value={selectedMapId}
            onChange={(e) => {
              setSelectedMapId(e.target.value);
              setImgError(false);
            }}
            disabled={!maps || maps.length === 0}
            className="px-3 py-1.5 rounded border text-sm"
            style={{
              borderColor: "var(--border-cyan)",
              backgroundColor: "var(--bg-card)",
              color: "var(--text-primary)",
            }}
          >
            <option value="">-- 選択 --</option>
            {maps?.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>

        {/* Band selector */}
        <div>
          <label
            className="block text-xs mb-1"
            style={{ color: "var(--text-muted)" }}
          >
            バンド表示
          </label>
          <div className="flex rounded overflow-hidden border" style={{ borderColor: "var(--border-cyan)" }}>
            {BAND_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                onClick={() => setActiveBandKey(opt.key)}
                className="px-3 py-1.5 text-xs transition-colors"
                style={{
                  backgroundColor:
                    activeBandKey === opt.key ? "var(--cyan)" : "var(--bg-card)",
                  color:
                    activeBandKey === opt.key ? "#000" : "var(--text-muted)",
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {selectedSiteId && (
          <p className="text-xs self-end pb-1.5" style={{ color: "var(--text-muted)" }}>
            {apsLoading ? "AP読み込み中..." : `マップ上: ${mapAps.length} AP`}
          </p>
        )}
      </div>

      {/* Map viewport */}
      {selectedMapId ? (
        <div
          className="relative rounded-lg overflow-hidden border"
          style={{ borderColor: "var(--border-cyan)" }}
        >
          {showGrid ? (
            <div
              className="grid-bg w-full"
              style={{
                height: "500px",
                backgroundColor: "var(--bg-card)",
              }}
            />
          ) : (
            <img
              key={imageUrl}
              src={imageUrl!}
              alt="Floor map"
              className="w-full h-auto block"
              onError={() => setImgError(true)}
            />
          )}

          {/* AP overlays */}
          {selectedMap &&
            mapAps.map((ap) => (
              <ApMarker
                key={ap.id}
                ap={ap}
                map={selectedMap}
                activeBand={activeBandKey}
                channelColorMap={channelColorMap}
              />
            ))}

          {/* Legend: channel colors in use */}
          {mapAps.length > 0 && (
            <div
              className="absolute bottom-2 right-2 rounded border px-2 py-1.5 text-xs space-y-0.5"
              style={{
                backgroundColor: "rgba(13,18,32,0.85)",
                borderColor: "var(--border-cyan)",
              }}
            >
              {Array.from(new Set(
                mapAps
                  .map((ap) => ap[activeBandKey]?.channel)
                  .filter((ch): ch is number => ch != null)
              ))
                .sort((a, b) => a - b)
                .map((ch) => (
                  <div key={ch} className="flex items-center gap-1.5">
                    <span
                      className="w-2.5 h-2.5 rounded-full inline-block"
                      style={{
                        backgroundColor: channelColorMap[ch] ?? "#475569",
                      }}
                    />
                    <span style={{ color: "var(--text-secondary)" }}>
                      Ch {ch}
                    </span>
                  </div>
                ))}
            </div>
          )}
        </div>
      ) : (
        selectedSiteId && (
          <div
            className="flex items-center justify-center rounded-lg border h-40"
            style={{
              borderColor: "var(--border-cyan)",
              backgroundColor: "var(--bg-card)",
              color: "var(--text-muted)",
            }}
          >
            <p className="text-sm">フロアマップを選択してください</p>
          </div>
        )
      )}

      {!selectedSiteId && (
        <div
          className="flex items-center justify-center rounded-lg border h-40"
          style={{
            borderColor: "var(--border-cyan)",
            backgroundColor: "var(--bg-card)",
            color: "var(--text-muted)",
          }}
        >
          <p className="text-sm">サイトを選択してください</p>
        </div>
      )}

      {/* Interference summary */}
      {mapAps.length > 0 && <InterferenceTable aps={mapAps} />}
    </div>
  );
});

FloorMapTab.displayName = "FloorMapTab";
export default FloorMapTab;
