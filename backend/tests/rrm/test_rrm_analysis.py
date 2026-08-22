"""期間の扱いとサイト別集計のテスト。合成データのみを使う。

- 期間は **半開区間** ``[start, end)``。終了時刻ちょうどのイベントは含まない
- 複数サイトを指定したとき、サイト別集計が正しく分かれる
- 時間帯別（1 時間バケット）の集計が分類別に分かれる
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import _rrmsynth as S

from rrm import analysis, events as ev, loader

START = datetime(2026, 1, 1, 10, 0, 0)


def _samples(n: int) -> list[dict[str, object]]:
    return [{"num_clients": 5, "util_24": 10, "util_5": 20, "util_6": 30} for _ in range(n)]


def write_logs(logs_dir: Path) -> Path:
    """2 サイト。TestSite に AP1/AP2、OtherSite に AP3。"""
    rows = (
        S.series(START, _samples(48), ap=S.AP1)
        + S.series(START, _samples(48), ap=S.AP2)
        + S.series(START, _samples(48), ap=S.AP3,
                   site_id=S.OTHER_SITE_ID, site_name=S.OTHER_SITE_NAME)
    )
    S.write_metrics(logs_dir / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_events(logs_dir / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=7), pre_channel=36, channel=44, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=67), reason="radar-detected",
                     pre_channel=64, channel=36, ap=S.AP2),
        S.rrm_action(START + timedelta(minutes=77), reason="post-radar",
                     pre_channel=36, channel=40, ap=S.AP2),
        S.rrm_action(START + timedelta(minutes=87), pre_channel=36, channel=36, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=97), pre_channel=36, channel=44, ap=S.AP3,
                     site_name=S.OTHER_SITE_NAME),
    ])
    return logs_dir


def run(logs_dir: Path, **kwargs) -> analysis.AnalysisResult:
    return analysis.run_analysis(loader.collect_files(logs_dir), analysis.AnalysisParams(**kwargs))


def test_window_is_half_open(tmp_path):
    logs = write_logs(tmp_path / "logs")
    res = run(
        logs,
        window_start=analysis.parse_time("2026-01-01 10:07:00", "from"),
        # 11:07:00 ちょうどのイベント（+67 分）は **含まない**
        window_end=analysis.parse_time("2026-01-01 11:07:00", "to"),
    )
    stamps = res.rows["event_timestamp"].tolist()

    assert "2026-01-01 10:07:00" in stamps
    assert "2026-01-01 11:07:00" not in stamps
    assert res.meta["event_count"] == 1


def test_all_sites_by_default(tmp_path):
    logs = write_logs(tmp_path / "logs")
    res = run(logs)

    assert res.meta["event_count"] == 5
    assert sorted(s["site_name"] for s in res.meta["by_site"]) == [S.OTHER_SITE_NAME, S.SITE_NAME]


def test_multiple_sites_are_split_in_the_site_summary(tmp_path):
    logs = write_logs(tmp_path / "logs")
    res = run(logs, sites=(S.SITE_ID, S.OTHER_SITE_ID))
    by_site = {s["site_name"]: s for s in res.meta["by_site"]}

    assert by_site[S.SITE_NAME]["changes"] == 3
    assert by_site[S.SITE_NAME]["noop"] == 1
    assert by_site[S.SITE_NAME]["changes_RADAR"] == 1
    assert by_site[S.SITE_NAME]["changes_POST_RADAR"] == 1
    assert by_site[S.SITE_NAME]["changes_RRM"] == 1
    assert by_site[S.OTHER_SITE_NAME]["changes"] == 1
    assert by_site[S.OTHER_SITE_NAME]["noop"] == 0


def test_selecting_one_site_drops_the_other_sites_events(tmp_path):
    logs = write_logs(tmp_path / "logs")
    res = run(logs, sites=(S.SITE_ID,))

    assert res.meta["event_count"] == 4
    assert [s["site_name"] for s in res.meta["by_site"]] == [S.SITE_NAME]
    assert set(res.rows["site_name"]) == {S.SITE_NAME}


def test_unknown_site_is_an_error(tmp_path):
    logs = write_logs(tmp_path / "logs")
    with pytest.raises(loader.SiteNotFoundError):
        run(logs, sites=("no-such-site",))


def test_hourly_summary_is_bucketed_by_hour_and_split_by_classification(tmp_path):
    logs = write_logs(tmp_path / "logs")
    res = run(
        logs,
        window_start=analysis.parse_time("2026-01-01 10:00", "from"),
        window_end=analysis.parse_time("2026-01-01 12:00", "to"),
    )
    hourly = {item["bucket"]: item for item in res.meta["hourly"]}

    # 連続した 2 バケット（10 時台・11 時台）が必ず出る
    assert list(hourly) == ["2026-01-01 10:00:00", "2026-01-01 11:00:00"]
    assert hourly["2026-01-01 10:00:00"]["changes_RRM"] == 1
    assert hourly["2026-01-01 11:00:00"]["changes_RADAR"] == 1
    assert hourly["2026-01-01 11:00:00"]["changes_POST_RADAR"] == 1
    # no-op はチャネル変更として数えない
    assert hourly["2026-01-01 11:00:00"]["changes_RRM"] == 1
    assert hourly["2026-01-01 11:00:00"]["changes_total"] == 3


def test_ap_summary_is_sorted_by_change_count(tmp_path):
    logs = write_logs(tmp_path / "logs")
    res = run(logs)
    names = [item["ap_name"] for item in res.meta["by_ap"]]

    assert names[0] == "TEST-AP-02"  # 2 件で最多
    assert set(names) == {"TEST-AP-01", "TEST-AP-02", "TEST-AP-03"}


def test_empty_window_is_not_an_error(tmp_path):
    """期間内にイベントが 0 件なのは正常な結果（「ログが無い」とは別）。"""
    logs = write_logs(tmp_path / "logs")
    res = run(
        logs,
        window_start=analysis.parse_time("2026-01-02 00:00", "from"),
        window_end=analysis.parse_time("2026-01-02 01:00", "to"),
    )

    assert res.meta["event_count"] == 0
    assert res.meta["change_count"] == 0
    assert list(res.rows.columns) == list(analysis.RESULT_COLUMNS)
    assert [c["classification"] for c in res.meta["by_classification"]] == list(
        ev.CLASSIFICATIONS
    )
    assert any("1 件もありません" in w for w in res.warnings)


def test_no_events_at_all_is_an_error(tmp_path):
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv", S.series(START, _samples(6), ap=S.AP1)
    )
    with pytest.raises(loader.NoEventsError):
        run(logs)
