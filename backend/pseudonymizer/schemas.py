"""ファイル種別定義・列ホワイトリスト・変換型辞書。

設計方針:
- 変換ルール（列名 → 変換型）は **グローバルな辞書 1 つ**（``COLUMN_RULES``）だけを持つ。
- ファイル種別は「通す列のホワイトリスト」（``FileType.columns``）だけを持つ。
- ``mac`` のように種別で意味が変わる列だけ ``FileType.overrides`` で解決する。
- ファイル種別の判定は **CSV ヘッダー行の列集合**で行う（ファイル名は使わない）。
  列集合が既知の種別と完全一致（列順は無視、過不足は不一致）した場合にその種別を採用する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TransformType(str, Enum):
    """列に適用する変換の種類。"""

    SITE_ID = "SITE_ID"
    SITE_NAME = "SITE_NAME"
    AP_ID = "AP_ID"
    AP_NAME = "AP_NAME"
    AP_MAC = "AP_MAC"
    CLIENT_MAC = "CLIENT_MAC"
    HOSTNAME = "HOSTNAME"
    IP = "IP"
    SSID = "SSID"
    MAP_NAME = "MAP_NAME"
    MAP_ID = "MAP_ID"
    AP_NAME_LIST = "AP_NAME_LIST"
    VLAN = "VLAN"
    TIMESTAMP = "TIMESTAMP"
    PASSTHROUGH = "PASSTHROUGH"


T = TransformType

# ---------------------------------------------------------------------------
# グローバル変換ルール辞書（列名 → 変換型）
# ここに無い列名は「未知の列」として --unknown-column の指定に従って処理される。
# ``mac`` は種別で意味が変わるため意図的にここへ置かない（AMBIGUOUS_COLUMNS 参照）。
# ---------------------------------------------------------------------------
COLUMN_RULES: dict[str, TransformType] = {
    # --- 仮名化対象 ---
    "timestamp": T.TIMESTAMP,
    "event_timestamp": T.TIMESTAMP,
    "site_id": T.SITE_ID,
    "site_name": T.SITE_NAME,
    "ap_id": T.AP_ID,
    "ap_name": T.AP_NAME,
    "ap_mac": T.AP_MAC,
    # rf_neighbors の被観測側。ap_mac / ap_name と**同じ名前空間**で採番する
    # （別名前空間にすると隣接グラフが壊れて分析不能になる）
    "neighbor_mac": T.AP_MAC,
    "neighbor_name": T.AP_NAME,
    "bssid": T.AP_MAC,
    "hostname": T.HOSTNAME,
    "ip": T.IP,
    "ssid": T.SSID,
    "map_name": T.MAP_NAME,
    "map_id": T.MAP_ID,
    "ap_list": T.AP_NAME_LIST,
    "vlan_id": T.VLAN,
    # --- そのまま通す列 ---
    # ap_metrics
    "status": T.PASSTHROUGH,
    "num_clients": T.PASSTHROUGH,
    "model": T.PASSTHROUGH,
    # floormap_ap_detail（フロア図原点からの相対座標。単体では場所を特定できないため通す）
    "x_m": T.PASSTHROUGH,
    "y_m": T.PASSTHROUGH,
    "band_24_channel": T.PASSTHROUGH,
    "band_24_bandwidth": T.PASSTHROUGH,
    "band_24_power": T.PASSTHROUGH,
    "band_24_noise_floor": T.PASSTHROUGH,
    "band_5_channel": T.PASSTHROUGH,
    "band_5_bandwidth": T.PASSTHROUGH,
    "band_5_power": T.PASSTHROUGH,
    "band_5_noise_floor": T.PASSTHROUGH,
    "band_6_channel": T.PASSTHROUGH,
    "band_6_bandwidth": T.PASSTHROUGH,
    "band_6_power": T.PASSTHROUGH,
    "band_6_noise_floor": T.PASSTHROUGH,
    # ap_events
    "event_type": T.PASSTHROUGH,
    "reason": T.PASSTHROUGH,
    "band": T.PASSTHROUGH,
    "channel": T.PASSTHROUGH,
    "pre_channel": T.PASSTHROUGH,
    "bandwidth": T.PASSTHROUGH,
    "pre_bandwidth": T.PASSTHROUGH,
    # client_metrics
    "manufacture": T.PASSTHROUGH,
    "family": T.PASSTHROUGH,
    "os": T.PASSTHROUGH,
    "proto": T.PASSTHROUGH,
    "rssi": T.PASSTHROUGH,
    "snr": T.PASSTHROUGH,
    "idle_time": T.PASSTHROUGH,
    "uptime": T.PASSTHROUGH,
    "tx_rate": T.PASSTHROUGH,
    "rx_rate": T.PASSTHROUGH,
    "tx_bytes": T.PASSTHROUGH,
    "rx_bytes": T.PASSTHROUGH,
    "tx_pkts": T.PASSTHROUGH,
    "rx_pkts": T.PASSTHROUGH,
    "tx_retries": T.PASSTHROUGH,
    "rx_retries": T.PASSTHROUGH,
    "tx_bps": T.PASSTHROUGH,
    "rx_bps": T.PASSTHROUGH,
    "key_mgmt": T.PASSTHROUGH,
    "dual_band": T.PASSTHROUGH,
    "is_guest": T.PASSTHROUGH,
    # floormap_summary
    "ap_count": T.PASSTHROUGH,
    "has_interference": T.PASSTHROUGH,
}

# ap_metrics の radio_* 24 列（すべて PASSTHROUGH）
_RADIO_SUFFIXES = (
    "channel", "bandwidth", "tx_power", "utilization",
    "util_tx", "util_rx_in_bss", "util_non_wifi", "noise_floor",
)
RADIO_COLUMNS: tuple[str, ...] = tuple(
    f"radio_{band}_{suffix}" for band in ("24", "5", "6") for suffix in _RADIO_SUFFIXES
)
for _c in RADIO_COLUMNS:
    COLUMN_RULES[_c] = T.PASSTHROUGH

# sle_metrics のスコア・カウント系 23 列（すべて PASSTHROUGH）
SLE_SCORE_COLUMNS: tuple[str, ...] = (
    "capacity_score",
    "capacity_wifi_interference", "capacity_non_wifi_interference",
    "capacity_client_count", "capacity_client_usage",
    "capacity_impact_users", "capacity_total_users",
    "throughput_score", "throughput_impact_users", "throughput_total_users",
    "coverage_score", "coverage_impact_users", "coverage_total_users",
    "time_to_connect_score", "time_to_connect_avg_sec",
    "ttc_impact_users", "ttc_total_users",
    "roaming_score", "roaming_impact_users", "roaming_total_users",
    "ap_availability_score", "ap_availability_impact_users", "ap_availability_total_users",
)
for _c in SLE_SCORE_COLUMNS:
    COLUMN_RULES[_c] = T.PASSTHROUGH

# 種別によって意味が変わるため、グローバル辞書では解決できない列。
# 各 FileType の overrides で必ず解決すること。
AMBIGUOUS_COLUMNS: frozenset[str] = frozenset({"mac"})

# AP 同一性のリンクに使わない列。
# bssid は AP の MAC ではなく無線／SSID ごとの識別子なので、AP 本体と同一視しない。
AP_LINK_EXCLUDE: frozenset[str] = frozenset({"bssid"})

# AP の名前・MAC・ID を束ねる変換型（同一 AP なら番号を揃える対象）
AP_IDENTITY_TYPES: tuple[TransformType, ...] = (T.AP_ID, T.AP_NAME, T.AP_MAC)


@dataclass(frozen=True)
class FileType:
    """CSV ファイル種別の定義。

    判定はヘッダーの列集合（``columns`` の frozenset）の完全一致で行う。
    ファイル名は判定に使わない。

    ``ap_link_groups`` は「同一行の中で**同じ AP** を指す列」の組を明示する。
    既定（空）は「AP 識別列すべてが同じ AP を指す」とみなす。rf_neighbors のように
    1 行に 2 台の AP が並ぶ種別では、組を分けて指定しないと別の AP が同一視されてしまう。
    """

    key: str
    columns: tuple[str, ...]
    overrides: dict[str, TransformType] = field(default_factory=dict)
    ap_link_groups: tuple[tuple[str, ...], ...] = ()

    def rule_for(self, column: str) -> TransformType | None:
        """列名に対する変換型を返す。ホワイトリスト外なら None。"""
        if column not in self.columns:
            return None
        if column in self.overrides:
            return self.overrides[column]
        return COLUMN_RULES[column]

    @property
    def whitelist(self) -> frozenset[str]:
        return frozenset(self.columns)


#: 33 列版（座標列追加前。過去ログとの互換のため別種別として残す）
AP_METRICS_V1_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_id", "site_name", "ap_id", "ap_name", "model", "mac",
    "status", "num_clients", *RADIO_COLUMNS,
)

#: 36 列版（末尾にフロアマップ座標を追加）
AP_METRICS_COLUMNS: tuple[str, ...] = (
    *AP_METRICS_V1_COLUMNS, "map_id", "x_m", "y_m",
)

AP_EVENTS_COLUMNS: tuple[str, ...] = (
    "event_timestamp", "site_name", "ap_name", "ap_mac", "event_type", "reason",
    "band", "channel", "pre_channel", "bandwidth", "pre_bandwidth",
)

CLIENT_METRICS_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_id", "site_name", "ap_id", "ap_name", "ap_mac",
    "mac", "hostname", "ip", "manufacture", "family", "model", "os",
    "band", "channel", "proto", "ssid", "bssid", "rssi", "snr",
    "idle_time", "uptime", "tx_rate", "rx_rate", "tx_bytes", "rx_bytes",
    "tx_pkts", "rx_pkts", "tx_retries", "rx_retries", "tx_bps", "rx_bps",
    "vlan_id", "key_mgmt", "dual_band", "is_guest",
)

SLE_METRICS_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_id", "site_name", "ap_id", "ap_name", *SLE_SCORE_COLUMNS,
)

FLOORMAP_SUMMARY_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_name", "map_name", "band", "channel",
    "ap_count", "ap_list", "has_interference",
)

#: RRM 隣接（RF 的な隣接）。非対称性を保つため方向ごとに 1 行
RF_NEIGHBORS_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_id", "site_name", "band",
    "ap_mac", "ap_name", "neighbor_mac", "neighbor_name", "rssi",
)

# floormap の生 AP データ（フロア図上の AP ごとのスナップショット）
FLOORMAP_AP_DETAIL_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_id", "site_name", "map_id", "map_name", "ap_name", "mac",
    "model", "status",
    "band_24_channel", "band_24_bandwidth", "band_24_power", "band_24_noise_floor",
    "band_5_channel", "band_5_bandwidth", "band_5_power", "band_5_noise_floor",
    "band_6_channel", "band_6_bandwidth", "band_6_power", "band_6_noise_floor",
    "num_clients", "x_m", "y_m",
)


FILE_TYPES: tuple[FileType, ...] = (
    FileType(
        key="ap_metrics",
        columns=AP_METRICS_COLUMNS,
        overrides={"mac": T.AP_MAC},
    ),
    FileType(
        key="ap_metrics_v1",
        # 座標列追加前（33 列）の過去ログ。ヘッダー完全一致判定のため別種別として残す
        columns=AP_METRICS_V1_COLUMNS,
        overrides={"mac": T.AP_MAC},
    ),
    FileType(
        key="ap_events",
        # ap_events_backfill_*.csv も同じ列構成なので、この種別に吸収される
        columns=AP_EVENTS_COLUMNS,
    ),
    FileType(
        key="client_metrics",
        columns=CLIENT_METRICS_COLUMNS,
        overrides={"mac": T.CLIENT_MAC},
    ),
    FileType(
        key="sle_metrics",
        columns=SLE_METRICS_COLUMNS,
    ),
    FileType(
        key="floormap_summary",
        # floormap_*_manual_summary.csv も同じ列構成なので、この種別に吸収される
        columns=FLOORMAP_SUMMARY_COLUMNS,
    ),
    FileType(
        key="floormap_ap_detail",
        columns=FLOORMAP_AP_DETAIL_COLUMNS,
        overrides={"mac": T.AP_MAC},
    ),
    FileType(
        key="rf_neighbors",
        columns=RF_NEIGHBORS_COLUMNS,
        # 1 行に観測側と被観測側の 2 台が並ぶ。組を分けないと別の AP が同一視される
        ap_link_groups=(("ap_mac", "ap_name"), ("neighbor_mac", "neighbor_name")),
    ),
)

FILE_TYPES_BY_KEY: dict[str, FileType] = {ft.key: ft for ft in FILE_TYPES}

# 種別ごとの想定列数（定義ミスを import 時に検出するため）
EXPECTED_COLUMN_COUNTS: dict[str, int] = {
    "ap_metrics": 36,
    "ap_metrics_v1": 33,
    "ap_events": 11,
    "client_metrics": 36,
    "sle_metrics": 28,
    "floormap_summary": 8,
    "floormap_ap_detail": 24,
    "rf_neighbors": 9,
}


def _self_check() -> None:
    """定義の整合性を import 時に検証する（開発時の取りこぼし防止）。"""
    seen_whitelists: dict[frozenset[str], str] = {}
    for ft in FILE_TYPES:
        expected = EXPECTED_COLUMN_COUNTS[ft.key]
        if len(ft.columns) != expected:
            raise RuntimeError(
                f"FileType {ft.key}: column count {len(ft.columns)} != expected {expected}"
            )
        if len(set(ft.columns)) != len(ft.columns):
            raise RuntimeError(f"FileType {ft.key}: duplicated column in whitelist")
        if ft.whitelist in seen_whitelists:
            raise RuntimeError(
                f"FileType {ft.key} and {seen_whitelists[ft.whitelist]} "
                "have the identical column set; header-based detection cannot "
                "distinguish them"
            )
        seen_whitelists[ft.whitelist] = ft.key
        for col in ft.columns:
            if col in ft.overrides:
                continue
            if col in AMBIGUOUS_COLUMNS:
                raise RuntimeError(
                    f"FileType {ft.key}: ambiguous column '{col}' needs an override"
                )
            if col not in COLUMN_RULES:
                raise RuntimeError(
                    f"FileType {ft.key}: column '{col}' has no rule in COLUMN_RULES"
                )
        for col in ft.overrides:
            if col not in ft.columns:
                raise RuntimeError(f"FileType {ft.key}: override for unknown column '{col}'")
        if ft.ap_link_groups:
            grouped: set[str] = set()
            for group in ft.ap_link_groups:
                for col in group:
                    if col not in ft.columns:
                        raise RuntimeError(
                            f"FileType {ft.key}: ap_link_groups has unknown column '{col}'"
                        )
                    if col in grouped:
                        raise RuntimeError(
                            f"FileType {ft.key}: column '{col}' appears in multiple ap_link_groups"
                        )
                    grouped.add(col)
            identity_cols = {
                c for c in ft.columns
                if c not in AP_LINK_EXCLUDE and ft.rule_for(c) in AP_IDENTITY_TYPES
            }
            missing = identity_cols - grouped
            if missing:
                raise RuntimeError(
                    f"FileType {ft.key}: AP identity columns missing from ap_link_groups: "
                    f"{sorted(missing)}"
                )


_self_check()


def detect_file_type(header: list[str] | tuple[str, ...]) -> FileType | None:
    """ヘッダーの列集合から種別を判定する。完全一致した種別が無ければ None。

    列順は無視するが、列数の過不足（重複列を含む）は不一致として扱う。
    """
    unique = frozenset(header)
    if len(unique) != len(header):
        return None
    for ft in FILE_TYPES:
        if ft.whitelist == unique:
            return ft
    return None


def detect_file_type_allowing_unknown(
    header: list[str] | tuple[str, ...],
) -> tuple[FileType | None, tuple[str, ...]]:
    """完全一致しない場合に、既知の種別のホワイトリストが header の部分集合になっている
    種別を 1 つだけ探す（``--unknown-column`` の drop/keep モード向けのフォールバック）。

    戻り値は (種別 または None, 未知列のタプル)。
    候補が 0 個または複数見つかった場合は (None, ()) を返す（判定不能として扱う）。
    """
    exact = detect_file_type(header)
    if exact is not None:
        return exact, ()
    header_set = frozenset(header)
    if len(header_set) != len(header):
        return None, ()
    candidates = [ft for ft in FILE_TYPES if ft.whitelist <= header_set]
    if len(candidates) != 1:
        return None, ()
    ft = candidates[0]
    unknown = tuple(c for c in header if c not in ft.whitelist)
    return ft, unknown


def ap_link_columns(ft: FileType) -> tuple[str, ...]:
    """同一 AP を指すとみなしてリンクする列名（同一行内での対応付けに使う）。"""
    cols = []
    for col in ft.columns:
        if col in AP_LINK_EXCLUDE:
            continue
        rule = ft.rule_for(col)
        if rule in AP_IDENTITY_TYPES:
            cols.append(col)
    return tuple(cols)


def ap_link_column_groups(ft: FileType) -> tuple[tuple[str, ...], ...]:
    """同一行内で「同じ AP を指す列」のグループ一覧を返す。

    ``ap_link_groups`` が未指定の種別では、AP 識別列すべてを 1 グループとして扱う
    （1 行に 1 台の AP しか現れない従来の種別の挙動）。
    """
    if not ft.ap_link_groups:
        return (ap_link_columns(ft),)
    return tuple(
        tuple(c for c in group if c not in AP_LINK_EXCLUDE and ft.rule_for(c) in AP_IDENTITY_TYPES)
        for group in ft.ap_link_groups
    )
