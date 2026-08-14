"""重複排除とタイムスタンプ保存のテスト。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import _synth as S

from hangap.loader import load

FIXTURES = Path(__file__).parent / "fixtures"


def test_metrics_dedupe_across_files():
    """同一 (ap_id, timestamp) が 2 ファイルに跨って存在しても 1 行になる。"""
    res = load([FIXTURES / "ap_metrics_part1.csv", FIXTURES / "ap_metrics_part2.csv"])

    assert len(res.metrics) == 6  # 4 + 4 のうち 2 行が重複
    assert res.report.file_stats["ap_metrics"].rows == 8
    assert res.report.file_stats["ap_metrics"].duplicates_removed == 2
    assert not res.metrics.duplicated(subset=["ap_id", "timestamp"]).any()
    # (ap_id, timestamp) 昇順
    assert res.metrics["timestamp"].is_monotonic_increasing


def test_metrics_dedupe_keeps_different_ap_at_same_time(tmp_path):
    """同一時刻でも AP が違えば別行として残る。"""
    ts = datetime(2026, 1, 1, 10, 0, 5)
    S.write_metrics(
        tmp_path / "m1.csv",
        [
            S.metrics_row(ts, ap_id="test-ap-0001", ap_name="TEST-AP-01"),
            S.metrics_row(ts, ap_id="test-ap-0002", ap_name="TEST-AP-02", mac="aabbccddee02"),
        ],
    )
    res = load(tmp_path)

    assert len(res.metrics) == 2
    assert res.report.file_stats["ap_metrics"].duplicates_removed == 0


def test_events_dedupe_is_all_columns():
    """同一時刻・同一 AP でも event_type が違えば両方残り、全列一致の行だけが消える。"""
    res = load([FIXTURES / "ap_events_part1.csv", FIXTURES / "ap_events_part2.csv"])

    assert res.report.file_stats["ap_events"].rows == 5
    assert res.report.file_stats["ap_events"].duplicates_removed == 1
    assert len(res.events) == 4

    first = datetime(2026, 1, 1, 10, 0, 10)
    same_time = res.events[res.events["event_timestamp"] == first]
    assert len(same_time) == 2  # AP_RESTARTED と AP_CONFIGURED の両方が残る
    assert set(same_time["event_type"]) == {"AP_RESTARTED", "AP_CONFIGURED"}
    assert res.events["event_timestamp"].is_monotonic_increasing


def test_events_dedupe_keeps_repeated_event_with_different_detail(tmp_path):
    """同一時刻・同種イベントでも 1 列でも値が違えば残す。"""
    ts = datetime(2026, 1, 1, 10, 0, 0)
    S.write_events(
        tmp_path / "e1.csv",
        [
            S.event_row(ts, "AP_RRM_ACTION", channel="36"),
            S.event_row(ts, "AP_RRM_ACTION", channel="44"),
            S.event_row(ts, "AP_RRM_ACTION", channel="44"),  # 完全重複
        ],
    )
    res = load(tmp_path)

    assert len(res.events) == 2
    assert res.report.file_stats["ap_events"].duplicates_removed == 1


def test_seconds_are_preserved():
    """17:19:05 のような秒が丸められずに保持される。"""
    res = load(FIXTURES / "ap_metrics_part1.csv")

    assert set(res.metrics["timestamp"].dt.second) == {5, 35}  # 秒が 0 に丸められていない
    assert res.metrics["timestamp"].min() == datetime(2026, 1, 1, 10, 0, 5)
    assert res.metrics["timestamp"].max() == datetime(2026, 1, 1, 10, 1, 35)

    ev = load(FIXTURES / "ap_events_part1.csv")
    assert (ev.events["event_timestamp"].dt.second == 10).all()


def test_timestamps_stay_naive(tmp_path):
    """タイムゾーン変換をしない（naive のまま）。"""
    S.write_metrics(
        tmp_path / "m_JST.csv",
        S.metrics_series(datetime(2026, 1, 1, 23, 0, 5), 30, 4),
    )
    res = load(tmp_path)

    assert res.metrics["timestamp"].dt.tz is None
    assert res.metrics["timestamp"].min() == datetime(2026, 1, 1, 23, 0, 5)


def test_tz_token_mix_is_warned_not_converted(tmp_path):
    """ファイル名の TZ トークンが混在したら警告のみ（変換はしない）。"""
    start = datetime(2026, 1, 1, 10, 0, 5)
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_JST.csv", S.metrics_series(start, 30, 4))
    S.write_metrics(
        tmp_path / "ap_metrics_20260101_1100_UTC.csv",
        S.metrics_series(start + timedelta(seconds=120), 30, 4),
    )
    res = load(tmp_path)

    assert res.report.tz_tokens == {"JST": 1, "UTC": 1}
    assert any("TZ トークン" in w for w in res.report.warnings)
    assert res.metrics["timestamp"].min() == start
