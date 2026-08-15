"""指示 10 パート A-4: ローダの rf_neighbors 対応。合成データのみを使う。"""
from __future__ import annotations

from datetime import datetime

import _synth as S
import pandas as pd

from hangap.loader import latest_rf_neighbors, load

DAY1 = datetime(2026, 1, 1, 4, 30, 0)
DAY2 = datetime(2026, 1, 2, 4, 30, 0)
AP_A = "aabbccddee01"
AP_B = "aabbccddee02"
AP_C = "aabbccddee03"


def test_absent_rf_neighbors_is_not_an_error(tmp_path):
    """rf_neighbors が 1 件も無いのは正常状態（既存ログには存在しない）。"""
    S.write_metrics(tmp_path / "ap_metrics.csv", S.metrics_series(DAY1, 60, 5))
    result = load(tmp_path)
    assert result.rf_neighbors.empty
    assert list(result.rf_neighbors.columns)[:4] == [
        "timestamp", "site_id", "site_name", "band",
    ]
    assert result.report.rf_neighbors_rows == 0
    assert result.report.rf_neighbors_latest is None
    # レポートの描画も落ちないこと
    assert "rf_neighbors" in result.report.render()


def test_rows_are_loaded_and_directions_are_preserved(tmp_path):
    S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", [
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -58.0),
        S.rf_neighbor_row(DAY1, AP_B, AP_A, -64.0),
    ])
    result = load(tmp_path)
    df = result.rf_neighbors
    assert len(df) == 2
    directed = {(r.ap_mac, r.neighbor_mac): r.rssi for r in df.itertuples()}
    assert directed[(AP_A, AP_B)] == -58.0
    assert directed[(AP_B, AP_A)] == -64.0
    assert result.report.rf_neighbors_rows == 2


def test_duplicate_key_is_removed(tmp_path):
    """重複判定キーは (site_id, band, ap_mac, neighbor_mac, timestamp)。"""
    rows = [
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -58.0),
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -58.0),  # 完全重複
    ]
    S.write_rf_neighbors(tmp_path / "part1.csv", rows)
    S.write_rf_neighbors(tmp_path / "part2.csv", rows)
    result = load(tmp_path)
    assert len(result.rf_neighbors) == 1
    assert result.report.file_stats["rf_neighbors"].duplicates_removed == 3


def test_different_band_is_not_a_duplicate(tmp_path):
    S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", [
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -58.0, band="5"),
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -50.0, band="24"),
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -66.0, band="6"),
    ])
    result = load(tmp_path)
    assert sorted(result.rf_neighbors["band"].tolist()) == ["24", "5", "6"]


def test_all_snapshots_are_kept_and_latest_is_reported(tmp_path):
    """複数日分を読んでも全件を保持し、最新の取得時刻をレポートに残すこと。"""
    S.write_rf_neighbors(tmp_path / "day1.csv", [
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -58.0),
        S.rf_neighbor_row(DAY1, AP_B, AP_A, -64.0),
    ])
    S.write_rf_neighbors(tmp_path / "day2.csv", [
        S.rf_neighbor_row(DAY2, AP_A, AP_B, -55.0),
        S.rf_neighbor_row(DAY2, AP_A, AP_C, -70.0),
        S.rf_neighbor_row(DAY2, AP_B, AP_A, -61.0),
    ])
    result = load(tmp_path)

    assert result.report.rf_neighbors_rows == 5  # 全件を保持する
    assert result.report.rf_neighbors_latest == pd.Timestamp(DAY2)
    assert result.report.rf_neighbors_snapshots == [
        (pd.Timestamp(DAY1), 2), (pd.Timestamp(DAY2), 3),
    ]

    latest = latest_rf_neighbors(result.rf_neighbors)
    assert len(latest) == 3
    assert set(latest["timestamp"].unique()) == {pd.Timestamp(DAY2)}
    # レポートに「どの時点を使ったか」が出ている
    rendered = result.report.render()
    assert "分析に使用" in rendered


def test_mac_is_normalized(tmp_path):
    S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", [
        S.rf_neighbor_row(DAY1, "AA:BB:CC:DD:EE:01", "AA-BB-CC-DD-EE-02", -58.0),
    ])
    df = load(tmp_path).rf_neighbors
    assert df.iloc[0]["ap_mac"] == AP_A
    assert df.iloc[0]["neighbor_mac"] == AP_B


def test_band_is_read_as_string(tmp_path):
    """band が 5.0 のような数値に化けないこと。"""
    S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", [
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -58.0, band="5"),
    ])
    assert load(tmp_path).rf_neighbors.iloc[0]["band"] == "5"


def test_metrics_and_rf_neighbors_load_together(tmp_path):
    S.write_metrics(tmp_path / "ap_metrics.csv", S.metrics_series(DAY1, 60, 5))
    S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", [
        S.rf_neighbor_row(DAY1, AP_A, AP_B, -58.0),
    ])
    result = load(tmp_path)
    assert not result.metrics.empty
    assert len(result.rf_neighbors) == 1
    assert result.report.unclassified == []
