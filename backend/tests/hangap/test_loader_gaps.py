"""欠測（ギャップ）検出のテスト。

単純結合で最も危険な失敗モード（欠測を跨いで「連続」と数える）を防げているかを見る。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import _synth as S

from hangap.loader import GAP_COLUMNS, load

START = datetime(2026, 1, 1, 10, 0, 5)


def _write_two_hours(tmp_path):
    """5 分間隔で 10 時台と 12 時台のファイルを置く（11 時台のファイルが欠けた状態）。"""
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_JST.csv", S.metrics_series(START, 300, 12))
    S.write_metrics(
        tmp_path / "ap_metrics_20260101_1200_JST.csv",
        S.metrics_series(START + timedelta(hours=2), 300, 12),
    )


def test_missing_hour_is_detected(tmp_path):
    """1 時間分のファイルが欠けたら、その位置にギャップが 1 件出る。"""
    _write_two_hours(tmp_path)
    res = load(tmp_path)

    assert list(res.gaps.columns) == list(GAP_COLUMNS)
    assert len(res.gaps) == 1
    gap = res.gaps.iloc[0]
    assert gap["gap_start"] == datetime(2026, 1, 1, 10, 55, 5)
    assert gap["gap_end"] == datetime(2026, 1, 1, 12, 0, 5)
    assert gap["gap_seconds"] == 3900.0
    assert gap["missing_samples"] == 12  # 11:00:05 〜 11:55:05 の 12 サンプル
    assert res.report.gaps.count == 1
    assert res.report.gaps.max_seconds == 3900.0
    assert res.report.gaps.total_missing_samples == 12


def test_gaps_do_not_modify_metrics(tmp_path):
    """gaps が返るだけで metrics の行は増減・改変されない。"""
    _write_two_hours(tmp_path)
    res = load(tmp_path)

    assert len(res.metrics) == 24  # 12 + 12。センチネル行は挿入されない
    assert res.metrics["timestamp"].notna().all()
    assert res.metrics["ap_id"].nunique() == 1
    # 欠測期間の行が捏造されていないこと
    hole = res.metrics[
        (res.metrics["timestamp"] > datetime(2026, 1, 1, 10, 55, 5))
        & (res.metrics["timestamp"] < datetime(2026, 1, 1, 12, 0, 5))
    ]
    assert hole.empty
    # 値も書き換わっていないこと
    assert (res.metrics["status"] == "connected").all()
    assert (res.metrics["mac"] == "aabbccddee01").all()


def test_jitter_under_default_gap_factor_is_not_a_gap(tmp_path):
    """既定 gap_factor=1.5 では、間隔の 1.2 倍の揺らぎはギャップにしない。"""
    offsets = [i * 300 for i in range(10)]
    offsets = offsets[:5] + [o + 60 for o in offsets[5:]]  # 1 箇所だけ 360 秒（1.2 倍）
    S.write_metrics(tmp_path / "m.csv", S.metrics_at(offsets, START))

    res = load(tmp_path)
    assert res.report.overall_interval_seconds == 300.0
    assert len(res.gaps) == 0

    tight = load(tmp_path, gap_factor=1.1)
    assert len(tight.gaps) == 1
    assert tight.gaps.iloc[0]["gap_seconds"] == 360.0
    assert tight.gaps.iloc[0]["missing_samples"] == 0


def test_gaps_are_per_ap(tmp_path):
    """ギャップは AP ごとに判定する（他 AP の欠測に引きずられない）。"""
    S.write_metrics(
        tmp_path / "m.csv",
        S.metrics_series(START, 300, 12, skip=(5,))
        + S.metrics_series(
            START, 300, 12, ap_id="test-ap-0002", ap_name="TEST-AP-02", mac="aabbccddee02"
        ),
    )
    res = load(tmp_path)

    assert len(res.gaps) == 1
    assert res.gaps.iloc[0]["ap_id"] == "test-ap-0001"
    assert res.gaps.iloc[0]["missing_samples"] == 1


def test_no_gaps_when_continuous(tmp_path):
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 30, 30))
    res = load(tmp_path)

    assert res.gaps.empty
    assert res.report.gaps.count == 0
    assert res.report.gaps.total_seconds == 0.0
