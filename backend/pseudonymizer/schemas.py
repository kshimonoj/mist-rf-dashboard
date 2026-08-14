"""ファイル種別定義・列ホワイトリスト・変換型辞書。

設計方針:
- 変換ルール（列名 → 変換型）は **グローバルな辞書 1 つ**（``COLUMN_RULES``）だけを持つ。
- ファイル種別は「通す列のホワイトリスト」（``FileType.columns``）だけを持つ。
- ``mac`` のように種別で意味が変わる列だけ ``FileType.overrides`` で解決する。
"""
from __future__ import annotations

import re
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
    "bssid": T.AP_MAC,
    "hostname": T.HOSTNAME,
    "ip": T.IP,
    "ssid": T.SSID,
    "map_name": T.MAP_NAME,
    "ap_list": T.AP_NAME_LIST,
    "vlan_id": T.VLAN,
    # --- そのまま通す列 ---
    # ap_metrics
    "status": T.PASSTHROUGH,
    "num_clients": T.PASSTHROUGH,
    "model": T.PASSTHROUGH,
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
    """CSV ファイル種別の定義。"""

    key: str
    pattern: re.Pattern[str]
    columns: tuple[str, ...]
    overrides: dict[str, TransformType] = field(default_factory=dict)

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


AP_METRICS_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_id", "site_name", "ap_id", "ap_name", "model", "mac",
    "status", "num_clients", *RADIO_COLUMNS,
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


FILE_TYPES: tuple[FileType, ...] = (
    FileType(
        key="ap_metrics",
        pattern=re.compile(r"^ap_metrics_.*\.csv$"),
        columns=AP_METRICS_COLUMNS,
        overrides={"mac": T.AP_MAC},
    ),
    FileType(
        key="ap_events",
        # ap_events_backfill_*.csv も同じ列構成なのでこのパターンに含まれる
        pattern=re.compile(r"^ap_events_.*\.csv$"),
        columns=AP_EVENTS_COLUMNS,
    ),
    FileType(
        key="client_metrics",
        pattern=re.compile(r"^client_metrics_.*\.csv$"),
        columns=CLIENT_METRICS_COLUMNS,
        overrides={"mac": T.CLIENT_MAC},
    ),
    FileType(
        key="sle_metrics",
        pattern=re.compile(r"^sle_metrics_.*\.csv$"),
        columns=SLE_METRICS_COLUMNS,
    ),
    FileType(
        key="floormap_summary",
        pattern=re.compile(r"^floormap_.*_summary\.csv$"),
        columns=FLOORMAP_SUMMARY_COLUMNS,
    ),
)

FILE_TYPES_BY_KEY: dict[str, FileType] = {ft.key: ft for ft in FILE_TYPES}

# 種別ごとの想定列数（定義ミスを import 時に検出するため）
EXPECTED_COLUMN_COUNTS: dict[str, int] = {
    "ap_metrics": 33,
    "ap_events": 11,
    "client_metrics": 36,
    "sle_metrics": 28,
    "floormap_summary": 8,
}


def _self_check() -> None:
    """定義の整合性を import 時に検証する（開発時の取りこぼし防止）。"""
    for ft in FILE_TYPES:
        expected = EXPECTED_COLUMN_COUNTS[ft.key]
        if len(ft.columns) != expected:
            raise RuntimeError(
                f"FileType {ft.key}: column count {len(ft.columns)} != expected {expected}"
            )
        if len(set(ft.columns)) != len(ft.columns):
            raise RuntimeError(f"FileType {ft.key}: duplicated column in whitelist")
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


_self_check()


def detect_file_type(filename: str) -> FileType | None:
    """ファイル名から種別を判定する。判定できなければ None。"""
    for ft in FILE_TYPES:
        if ft.pattern.match(filename):
            return ft
    return None


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
