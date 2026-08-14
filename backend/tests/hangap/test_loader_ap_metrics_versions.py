"""指示 09: ap_metrics の座標列追加（36 列版）と旧 33 列版(ap_metrics_v1)の混在読み込み。"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

import _synth as S
import pandas as pd

from hangap.loader import load
from pseudonymizer.schemas import AP_METRICS_V1_COLUMNS

START = datetime(2026, 1, 1, 10, 0, 5)


def _write_v1_csv(path: Path, ts: datetime, ap_id: str, ap_name: str, mac: str) -> None:
    """座標列を持たない旧 33 列版の CSV を書く。"""
    row = {c: "" for c in AP_METRICS_V1_COLUMNS}
    row.update(
        timestamp=ts.strftime("%Y-%m-%d %H:%M:%S"),
        site_id=S.SITE_ID,
        site_name=S.SITE_NAME,
        ap_id=ap_id,
        ap_name=ap_name,
        model="AP-TEST",
        mac=mac,
        status="connected",
        num_clients=0,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(AP_METRICS_V1_COLUMNS))
        writer.writeheader()
        writer.writerow(row)


def test_v1_only_is_loaded_by_default_with_null_coordinates(tmp_path):
    """file_types を指定しなくても ap_metrics_v1 が読み込まれ、座標は NULL になる。"""
    _write_v1_csv(tmp_path / "old.csv", START, "test-ap-0001", "TEST-AP-01", "aabbccddee01")
    res = load(tmp_path)

    assert res.report.file_stats["ap_metrics_v1"].loaded is True
    assert len(res.metrics) == 1
    row = res.metrics.iloc[0]
    assert pd.isna(row["map_id"])
    assert pd.isna(row["x_m"])
    assert pd.isna(row["y_m"])


def test_v1_and_v2_mixed_in_same_directory(tmp_path):
    """33 列版と 36 列版が同じディレクトリに混在しても両方が ap_metrics として結合される。"""
    _write_v1_csv(tmp_path / "legacy.csv", START, "test-ap-0001", "TEST-AP-01", "aabbccddee01")
    S.write_metrics(
        tmp_path / "current.csv",
        [
            S.metrics_row(
                START + timedelta(hours=1),
                ap_id="test-ap-0002",
                ap_name="TEST-AP-02",
                mac="aabbccddee02",
                map_id="test-map-id-0001",
                x_m=12.5,
                y_m=7.25,
            )
        ],
    )
    res = load(tmp_path)

    assert res.report.file_stats["ap_metrics_v1"].files == 1
    assert res.report.file_stats["ap_metrics"].files == 1
    assert len(res.metrics) == 2

    by_ap = res.metrics.set_index("ap_id")
    legacy_row = by_ap.loc["test-ap-0001"]
    assert pd.isna(legacy_row["map_id"])
    assert pd.isna(legacy_row["x_m"])
    assert pd.isna(legacy_row["y_m"])

    current_row = by_ap.loc["test-ap-0002"]
    assert current_row["map_id"] == "test-map-id-0001"
    assert float(current_row["x_m"]) == 12.5
    assert float(current_row["y_m"]) == 7.25


def test_v1_excluded_when_file_types_limited_to_ap_metrics(tmp_path):
    """file_types で ap_metrics のみを指定すれば、v1 は件数のみで DataFrame には含まれない。"""
    _write_v1_csv(tmp_path / "legacy.csv", START, "test-ap-0001", "TEST-AP-01", "aabbccddee01")
    res = load(tmp_path, file_types=["ap_metrics"])

    assert res.metrics.empty
    assert res.report.file_stats["ap_metrics_v1"].loaded is False
    assert res.report.file_stats["ap_metrics_v1"].rows == 1
