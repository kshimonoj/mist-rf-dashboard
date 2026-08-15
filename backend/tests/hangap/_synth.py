"""テスト用の合成ログ生成ヘルパ。

実データ・実データ由来の値は一切使わない。AP 名・MAC・ID は
一目で偽物と分かる値（``test-ap-0001`` / ``aabbccddee01`` 等）に固定する。
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from pseudonymizer.schemas import (
    AP_EVENTS_COLUMNS,
    AP_METRICS_COLUMNS,
    RF_NEIGHBORS_COLUMNS,
)

SITE_ID = "test-site-id-0001"
SITE_NAME = "TestSite"


def metrics_row(
    ts: datetime,
    ap_id: str = "test-ap-0001",
    ap_name: str = "TEST-AP-01",
    mac: str = "aabbccddee01",
    site_id: str = SITE_ID,
    site_name: str = SITE_NAME,
    num_clients: int = 0,
    **extra,
) -> dict[str, object]:
    row: dict[str, object] = {c: "" for c in AP_METRICS_COLUMNS}
    row.update(
        timestamp=ts.strftime("%Y-%m-%d %H:%M:%S"),
        site_id=site_id,
        site_name=site_name,
        ap_id=ap_id,
        ap_name=ap_name,
        model="AP-TEST",
        mac=mac,
        status="connected",
        num_clients=num_clients,
        radio_24_channel=1,
        radio_5_channel=36,
        radio_5_utilization=10,
    )
    row.update(extra)
    return row


def metrics_series(
    start: datetime,
    interval_seconds: int,
    count: int,
    *,
    skip: Sequence[int] = (),
    **kwargs,
) -> list[dict[str, object]]:
    """等間隔のメトリクス行を作る。``skip`` に入れた index は欠測にする。"""
    rows = []
    for i in range(count):
        if i in skip:
            continue
        rows.append(metrics_row(start + timedelta(seconds=interval_seconds * i), **kwargs))
    return rows


def metrics_at(offsets_seconds: Iterable[int], start: datetime, **kwargs) -> list[dict[str, object]]:
    """開始時刻からの秒オフセット列でメトリクス行を作る。"""
    return [metrics_row(start + timedelta(seconds=o), **kwargs) for o in offsets_seconds]


def event_row(
    ts: datetime,
    event_type: str = "AP_RESTARTED",
    ap_name: str = "TEST-AP-01",
    ap_mac: str = "aabbccddee01",
    site_name: str = SITE_NAME,
    **extra,
) -> dict[str, object]:
    row: dict[str, object] = {c: "" for c in AP_EVENTS_COLUMNS}
    row.update(
        event_timestamp=ts.strftime("%Y-%m-%d %H:%M:%S"),
        site_name=site_name,
        ap_name=ap_name,
        ap_mac=ap_mac,
        event_type=event_type,
    )
    row.update(extra)
    return row


def rf_neighbor_row(
    ts: datetime,
    ap_mac: str,
    neighbor_mac: str,
    rssi: float,
    *,
    band: str = "5",
    ap_name: str = "",
    neighbor_name: str = "",
    site_id: str = SITE_ID,
    site_name: str = SITE_NAME,
) -> dict[str, object]:
    """RRM 隣接の 1 方向分。対称化しないので A→B と B→A は別の行として作る。"""
    return {
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "site_id": site_id,
        "site_name": site_name,
        "band": band,
        "ap_mac": ap_mac,
        "ap_name": ap_name,
        "neighbor_mac": neighbor_mac,
        "neighbor_name": neighbor_name,
        "rssi": rssi,
    }


def write_rf_neighbors(path: Path, rows: Iterable[dict[str, object]]) -> Path:
    return write_csv(path, RF_NEIGHBORS_COLUMNS, rows)


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def write_metrics(path: Path, rows: Iterable[dict[str, object]]) -> Path:
    return write_csv(path, AP_METRICS_COLUMNS, rows)


def write_events(path: Path, rows: Iterable[dict[str, object]]) -> Path:
    return write_csv(path, AP_EVENTS_COLUMNS, rows)


def write_xlsx(path: Path, sheets: dict[str, tuple[Sequence[str], Sequence[dict[str, object]]]]) -> Path:
    """シート名 → (列, 行) の辞書から XLSX を書き出す。"""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, (columns, rows) in sheets.items():
        ws = wb.create_sheet(title=name)
        ws.append(list(columns))
        for row in rows:
            ws.append([row.get(c, "") for c in columns])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
