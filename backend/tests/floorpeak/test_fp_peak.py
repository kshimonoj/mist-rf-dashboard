"""ピーク時点の選定（floorpeak.peak / floorpeak.loader）。合成データのみを使う。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import _fpsynth as S
import pandas as pd
import pytest

from floorpeak import analysis, loader, peak as peak_mod

START = datetime(2026, 1, 1, 10, 0, 0)
INTERVAL = 300


def _load(tmp_path: Path, rows, **kwargs) -> loader.MetricsLoad:
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_TZT.csv", rows)
    return loader.load_metrics(loader.collect_files(tmp_path), site=S.SITE_ID, **kwargs)


def _ap(i: int) -> dict[str, str]:
    return {"ap_id": f"test-ap-{i:04d}", "ap_name": f"TEST-AP-{i:02d}", "mac": f"aabbccddee{i:02d}"}


# ---------------------------------------------------------------------------
# 1. バケット化しないと偽のピークを掴む
# ---------------------------------------------------------------------------


def test_bucketing_finds_the_real_peak_despite_jitter(tmp_path):
    """AP 間のジッタで散ったバケットが、同一秒に並んだバケットに負けないこと。

    生タイムスタンプで合計すると 10:05（3 台が同一秒に並ぶ）が最大になるが、
    実際に混んでいるのは 10:00（3 台が数秒ずれて 10 台ずつ）である。
    """
    # 各 AP は自分の周期（300 秒）を保ったまま、AP 同士が数秒ずれている。
    # 10:05 の回だけ 3 台が同一秒（10:05:06）に並ぶ。
    rows = []
    for i, jitter in enumerate([0, 3, 6], start=1):
        rows += [
            S.metrics_row(START + timedelta(seconds=jitter), num_clients=10, **_ap(i)),
            S.metrics_row(START + timedelta(seconds=INTERVAL + 6), num_clients=6, **_ap(i)),
        ]
    loaded = _load(tmp_path, rows)

    # 生タイムスタンプで合計すると 10:05 が勝つ（この分析が避けたい失敗）
    naive = loaded.metrics.groupby("timestamp")["num_clients"].sum()
    assert naive.idxmax() == pd.Timestamp(START + timedelta(seconds=INTERVAL + 6))
    assert naive.max() == 18

    result = peak_mod.find_peak(loaded.metrics, loaded.bucket_seconds)
    assert result.peak_bucket == pd.Timestamp(START)
    assert result.peak_total_clients == 30
    assert result.selected_by == peak_mod.SELECTED_AUTO


# ---------------------------------------------------------------------------
# 2. 同点は最も早いバケット
# ---------------------------------------------------------------------------


def test_tie_selects_the_earliest_bucket(tmp_path):
    rows = []
    for i in (1, 2):
        rows += S.series(START, INTERVAL, [4, 7, 7, 3], **_ap(i))
    loaded = _load(tmp_path, rows)
    result = peak_mod.find_peak(loaded.metrics, loaded.bucket_seconds)

    assert result.peak_total_clients == 14
    assert result.peak_bucket == pd.Timestamp(START + timedelta(seconds=INTERVAL))


# ---------------------------------------------------------------------------
# 3. 手動指定（最近傍バケット・ずれの警告）
# ---------------------------------------------------------------------------


def test_manual_at_selects_the_nearest_bucket(tmp_path):
    rows = S.series(START, INTERVAL, [1, 9, 2, 1], **_ap(1))
    loaded = _load(tmp_path, rows)

    at = pd.Timestamp(START + timedelta(seconds=INTERVAL * 2 + 40))
    result = peak_mod.find_peak(loaded.metrics, loaded.bucket_seconds, at=at)

    assert result.selected_by == peak_mod.SELECTED_MANUAL
    assert result.peak_bucket == pd.Timestamp(START + timedelta(seconds=INTERVAL * 2))
    # 端末数が最大の 10:05 ではなく、指定時点に近い 10:10 が選ばれる
    assert result.peak_total_clients == 2
    assert result.manual_offset_seconds == 40
    assert result.warnings == []


def test_manual_at_far_from_data_warns(tmp_path):
    rows = S.series(START, INTERVAL, [1, 9, 2, 1], **_ap(1))
    loaded = _load(tmp_path, rows)

    limit = loaded.bucket_seconds * peak_mod.MANUAL_OFFSET_WARN_FACTOR
    at = pd.Timestamp(START + timedelta(seconds=INTERVAL * 3 + int(limit) + 60))
    result = peak_mod.find_peak(loaded.metrics, loaded.bucket_seconds, at=at)

    assert result.peak_bucket == pd.Timestamp(START + timedelta(seconds=INTERVAL * 3))
    assert result.manual_offset_seconds > limit
    assert len(result.warnings) == 1
    assert "ずれています" in result.warnings[0]


def test_manual_at_just_inside_the_limit_does_not_warn(tmp_path):
    rows = S.series(START, INTERVAL, [1, 9, 2, 1], **_ap(1))
    loaded = _load(tmp_path, rows)

    limit = loaded.bucket_seconds * peak_mod.MANUAL_OFFSET_WARN_FACTOR
    at = pd.Timestamp(START + timedelta(seconds=INTERVAL * 3 + int(limit)))
    result = peak_mod.find_peak(loaded.metrics, loaded.bucket_seconds, at=at)

    assert result.manual_offset_seconds == limit
    assert result.warnings == []


# ---------------------------------------------------------------------------
# 8. 期間は半開区間 [start, end)
# ---------------------------------------------------------------------------


def test_window_is_half_open(tmp_path):
    """``window_end`` ちょうどのサンプルは入らない。"""
    rows = S.series(START, INTERVAL, [1, 2, 99], **_ap(1))
    end = pd.Timestamp(START + timedelta(seconds=INTERVAL * 2))

    loaded = _load(tmp_path, rows, window_end=end)
    assert loaded.metrics["timestamp"].max() == pd.Timestamp(START + timedelta(seconds=INTERVAL))
    assert (loaded.metrics["timestamp"] == end).sum() == 0

    result = peak_mod.find_peak(loaded.metrics, loaded.bucket_seconds)
    assert result.peak_total_clients == 2  # 99 のサンプルは窓の外


def test_window_start_is_inclusive(tmp_path):
    rows = S.series(START, INTERVAL, [5, 1, 1], **_ap(1))
    loaded = _load(tmp_path, rows, window_start=pd.Timestamp(START))
    assert loaded.metrics["timestamp"].min() == pd.Timestamp(START)
    assert peak_mod.find_peak(loaded.metrics, loaded.bucket_seconds).peak_total_clients == 5


# ---------------------------------------------------------------------------
# 9. サイト指定
# ---------------------------------------------------------------------------


def test_other_site_rows_are_dropped(tmp_path):
    rows = S.series(START, INTERVAL, [2, 3, 1], **_ap(1))
    rows += S.series(
        START, INTERVAL, [50, 60, 70],
        site_id=S.OTHER_SITE_ID, site_name=S.OTHER_SITE_NAME,
        ap_id="test-ap-9001", ap_name="OTHER-AP-01", mac="aabbccddee99",
    )
    loaded = _load(tmp_path, rows)

    assert set(loaded.metrics["site_id"]) == {S.SITE_ID}
    assert "OTHER-AP-01" not in set(loaded.metrics["ap_name"])
    result = peak_mod.find_peak(loaded.metrics, loaded.bucket_seconds)
    assert result.peak_total_clients == 3


def test_unknown_site_is_an_error(tmp_path):
    rows = S.series(START, INTERVAL, [1, 2, 3], **_ap(1))
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_TZT.csv", rows)
    with pytest.raises(loader.SiteNotFoundError):
        loader.load_metrics(loader.collect_files(tmp_path), site="no-such-site")


def test_empty_window_is_not_a_zero_result(tmp_path):
    """期間に 1 行も無いのは「ピークが無い」ではなくエラーにすること。"""
    rows = S.series(START, INTERVAL, [1, 2, 3], **_ap(1))
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_TZT.csv", rows)
    with pytest.raises(loader.NoMetricsError):
        loader.load_metrics(
            loader.collect_files(tmp_path), site=S.SITE_ID,
            window_start=pd.Timestamp("2026-02-01 00:00"),
        )


# ---------------------------------------------------------------------------
# バケット幅
# ---------------------------------------------------------------------------


def test_bucket_seconds_falls_back_and_warns(tmp_path):
    """サンプルが 1 件しか無くて間隔を推定できないときは既定値 + 警告。"""
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_TZT.csv",
                    [S.metrics_row(START, num_clients=3, **_ap(1))])
    loaded = loader.load_metrics(loader.collect_files(tmp_path), site=S.SITE_ID)

    assert loaded.bucket_seconds == loader.FALLBACK_BUCKET_SECONDS
    assert loaded.bucket_seconds_estimated is False
    assert any("バケット幅" in w for w in loaded.warnings)


def test_duplicate_rows_in_a_bucket_use_the_latest(tmp_path):
    """同一バケットに同じ AP の行が複数あれば最も遅い行を採る。"""
    rows = [
        S.metrics_row(START, num_clients=1, **_ap(1)),
        S.metrics_row(START + timedelta(seconds=30), num_clients=8, **_ap(1)),
        S.metrics_row(START + timedelta(seconds=INTERVAL), num_clients=1, **_ap(1)),
    ]
    loaded = _load(tmp_path, rows)
    result = peak_mod.find_peak(loaded.metrics, 300)

    assert result.peak_bucket == pd.Timestamp(START)
    assert result.peak_total_clients == 8
    assert len(result.ap_rows) == 1


def test_down_ap_contributes_zero_and_is_kept(tmp_path):
    """status が down の AP は 0 として合計に寄与するが、行としては残る。"""
    rows = [
        S.metrics_row(START, num_clients=5, **_ap(1)),
        S.metrics_row(START, num_clients="", status="disconnected", **_ap(2)),
    ]
    loaded = _load(tmp_path, rows)
    result = peak_mod.find_peak(loaded.metrics, 300)

    assert result.peak_total_clients == 5
    assert len(result.ap_rows) == 2


def test_at_ignores_the_window_and_warns(tmp_path):
    """時点指定は期間指定より優先し、無視したことを警告に残す。"""
    rows = S.series(START, INTERVAL, [1, 2, 3, 4], **_ap(1))
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_TZT.csv", rows)
    files = loader.collect_files(tmp_path)

    res = analysis.run_analysis(files, analysis.AnalysisParams(
        site=S.SITE_ID,
        window_start=pd.Timestamp(START),
        window_end=pd.Timestamp(START + timedelta(seconds=INTERVAL)),
        at=pd.Timestamp(START + timedelta(seconds=INTERVAL * 3)),
    ))
    assert res.meta["selected_by"] == "manual"
    assert res.meta["peak_time"] == "2026-01-01 10:15:00"
    assert any("期間の指定は無視" in w for w in res.warnings)
