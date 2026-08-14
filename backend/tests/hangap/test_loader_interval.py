"""サンプリング間隔推定のテスト（設定値ではなくデータから推定する）。"""
from __future__ import annotations

import statistics
from datetime import datetime

import _synth as S

from hangap.loader import load

START = datetime(2026, 1, 1, 10, 0, 5)


def _interval_of(res, ap_id="test-ap-0001"):
    return {a.ap_id: a.interval_seconds for a in res.report.ap_intervals}[ap_id]


def test_estimate_30s(tmp_path):
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 30, 20))
    res = load(tmp_path)

    assert _interval_of(res) == 30.0
    assert res.report.overall_interval_seconds == 30.0
    assert len(res.report.interval_groups) == 1


def test_estimate_5min(tmp_path):
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 300, 20))
    res = load(tmp_path)

    assert _interval_of(res) == 300.0
    assert res.report.overall_interval_seconds == 300.0


def test_mode_beats_median_with_missing_samples(tmp_path):
    """欠測が多く、中央値では誤る並びでも最頻値なら正しく 30 秒と推定できる。"""
    offsets = [0, 30, 60, 150, 270, 420]  # 差分: 30, 30, 90, 120, 150
    diffs = [b - a for a, b in zip(offsets, offsets[1:])]
    assert statistics.median(diffs) == 90  # 中央値では誤る並びであることを固定
    S.write_metrics(tmp_path / "m.csv", S.metrics_at(offsets, START))

    res = load(tmp_path)
    assert _interval_of(res) == 30.0


def test_mixed_intervals_warn(tmp_path):
    """30 秒 AP と 5 分 AP が混在したら警告が出て、AP ごとの推定値が出る。"""
    S.write_metrics(
        tmp_path / "m.csv",
        S.metrics_series(START, 30, 20, ap_id="test-ap-0001", ap_name="TEST-AP-01")
        + S.metrics_series(
            START, 300, 20, ap_id="test-ap-0002", ap_name="TEST-AP-02", mac="aabbccddee02"
        ),
    )
    res = load(tmp_path)

    assert _interval_of(res, "test-ap-0001") == 30.0
    assert _interval_of(res, "test-ap-0002") == 300.0
    assert len(res.report.interval_groups) == 2
    assert any("ばらついています" in w for w in res.report.warnings)
    rendered = res.report.render()
    assert "TEST-AP-01" in rendered and "TEST-AP-02" in rendered


def test_single_sample_ap_is_not_estimated(tmp_path):
    """サンプル 1 件の AP は推定不能（None）だが、エラーにはしない。"""
    S.write_metrics(
        tmp_path / "m.csv",
        S.metrics_series(START, 30, 10)
        + S.metrics_at([0], START, ap_id="test-ap-0009", ap_name="TEST-AP-09", mac="aabbccddee09"),
    )
    res = load(tmp_path)

    assert _interval_of(res, "test-ap-0009") is None
    assert res.report.overall_interval_seconds == 30.0
