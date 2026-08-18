"""floorpeak テスト用の合成ログ生成ヘルパ。

実データ・実データ由来の値は一切使わない。AP 名・MAC・ID は一目で偽物と
分かる値（``test-ap-0001`` / ``aabbccddee01`` 等）に固定する。

``tests/hangap/_synth.py`` とは **別モジュール**（同名にすると、pytest が
テストディレクトリを sys.path に足す都合で先に import された方だけが使われる）。
hangap 側のテスト資産は書き換えない。
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from pseudonymizer.schemas import AP_METRICS_COLUMNS, FLOORMAP_SUMMARY_COLUMNS

SITE_ID = "test-site-id-0001"
SITE_NAME = "TestSite"
OTHER_SITE_ID = "test-site-id-0002"
OTHER_SITE_NAME = "OtherSite"

MAP_1F = "test-map-id-1f"
MAP_2F = "test-map-id-2f"
FLOOR_1F = "Test Bldg 1F"
FLOOR_2F = "Test Bldg 2F"


def metrics_row(
    ts: datetime,
    *,
    ap_id: str = "test-ap-0001",
    ap_name: str = "TEST-AP-01",
    mac: str = "aabbccddee01",
    model: str = "AP45",
    site_id: str = SITE_ID,
    site_name: str = SITE_NAME,
    num_clients: int = 0,
    status: str = "connected",
    map_id: str = MAP_1F,
    x_m: object = 1.0,
    y_m: object = 2.0,
    **extra,
) -> dict[str, object]:
    row: dict[str, object] = {c: "" for c in AP_METRICS_COLUMNS}
    row.update(
        timestamp=ts.strftime("%Y-%m-%d %H:%M:%S"),
        site_id=site_id,
        site_name=site_name,
        ap_id=ap_id,
        ap_name=ap_name,
        model=model,
        mac=mac,
        status=status,
        num_clients=num_clients,
        map_id=map_id,
        x_m=x_m,
        y_m=y_m,
        radio_24_channel=1,
        radio_5_channel=36,
    )
    row.update(extra)
    return row


def series(
    start: datetime,
    interval_seconds: int,
    values: Sequence[int],
    *,
    jitter: Sequence[int] | None = None,
    **kwargs,
) -> list[dict[str, object]]:
    """等間隔のメトリクス行を作る。``jitter`` は各サンプルの秒ずれ。"""
    rows: list[dict[str, object]] = []
    for i, value in enumerate(values):
        offset = interval_seconds * i + (jitter[i] if jitter else 0)
        rows.append(metrics_row(start + timedelta(seconds=offset), num_clients=value, **kwargs))
    return rows


def floormap_row(
    ts: datetime,
    *,
    site_name: str = SITE_NAME,
    map_name: str = FLOOR_1F,
    band: str = "5",
    channel: object = 36,
    ap_list: Sequence[str] = (),
    has_interference: str = "False",
) -> dict[str, object]:
    return {
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "site_name": site_name,
        "map_name": map_name,
        "band": band,
        "channel": channel,
        "ap_count": len(ap_list),
        "ap_list": ",".join(ap_list),
        "has_interference": has_interference,
    }


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


def write_floormap(directory: Path, ts: datetime, rows: Iterable[dict[str, object]]) -> Path:
    """``floormap_<YYYYMMDD>_<HHMM>_TZT_summary.csv`` として書き出す。"""
    name = f"floormap_{ts.strftime('%Y%m%d_%H%M')}_TZT_summary.csv"
    return write_csv(directory / name, FLOORMAP_SUMMARY_COLUMNS, rows)


def default_floormap(directory: Path, ts: datetime, **kwargs) -> Path:
    """1F に AP-01/AP-02、2F に AP-03 が載った標準的な floormap。"""
    rows = [
        floormap_row(ts, map_name=FLOOR_1F, band="5", channel=36, ap_list=["TEST-AP-01", "TEST-AP-02"], **kwargs),
        floormap_row(ts, map_name=FLOOR_1F, band="24", channel=1, ap_list=["TEST-AP-01"], **kwargs),
        floormap_row(ts, map_name=FLOOR_2F, band="5", channel=40, ap_list=["TEST-AP-03"], **kwargs),
    ]
    return write_floormap(directory, ts, rows)
