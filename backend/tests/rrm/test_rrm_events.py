"""イベントの分類・no-op・レーダー突合のテスト。合成データのみを使う。

ここが固定したいのは次の 4 点。

1. ``pre_channel == channel`` はチャネル変更として数えず、no-op として別に数える
2. ``post-radar`` は RADAR とも RRM とも別の分類になる
3. 対応する ``AP_RRM_ACTION`` が無い ``AP_RADAR_DETECTED`` を取りこぼさない
4. 1 つの検知に ``AP_RRM_ACTION`` が複数近接しても検知を二重計上しない
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import _rrmsynth as S

from rrm import analysis, loader

START = datetime(2026, 1, 1, 10, 0, 0)
SAMPLES = [{"num_clients": 5, "util_24": 10, "util_5": 20, "util_6": 30}] * 24


def write_metrics(logs_dir: Path, aps=(S.AP1, S.AP2)) -> None:
    rows: list[dict[str, object]] = []
    for ap in aps:
        rows += S.series(START, SAMPLES, ap=ap)
    S.write_metrics(logs_dir / "ap_metrics_20260101_1000_TZT.csv", rows)


def run(logs_dir: Path, **kwargs) -> analysis.AnalysisResult:
    files = loader.collect_files(logs_dir)
    return analysis.run_analysis(files, analysis.AnalysisParams(**kwargs))


def test_pre_equals_post_is_counted_as_noop_not_as_a_change(tmp_path):
    logs = tmp_path / "logs"
    write_metrics(logs)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        # 定期 RRM が評価して現状維持と判断した（異常ではない）
        S.rrm_action(START + timedelta(minutes=7, seconds=30), pre_channel=36, channel=36, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=12, seconds=30), pre_channel=36, channel=44, ap=S.AP1),
    ])
    res = run(logs)
    meta = res.meta

    assert meta["change_count"] == 1
    assert meta["noop_count"] == 1
    assert meta["changes_by_class"]["RRM"] == 1
    assert meta["noop_by_class"]["RRM"] == 1
    # no-op の行も明細から消さない（RRM が動作していること自体が情報）
    assert len(res.rows) == 2
    assert sorted(res.rows["channel_changed"].tolist()) == [False, True]


def test_post_radar_is_its_own_classification(tmp_path):
    logs = tmp_path / "logs"
    write_metrics(logs)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=7), reason="radar-detected",
                     pre_channel=64, channel=36, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=8), reason="post-radar",
                     pre_channel=36, channel=44, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=9), reason="scheduled-site-rrm",
                     pre_channel=44, channel=48, ap=S.AP1),
    ])
    res = run(logs)

    assert res.meta["changes_by_class"] == {"RADAR": 1, "POST_RADAR": 1, "RRM": 1}
    by_reason = dict(zip(res.rows["reason"], res.rows["classification"]))
    assert by_reason["post-radar"] == "POST_RADAR"
    assert by_reason["radar-detected"] == "RADAR"
    assert by_reason["scheduled-site-rrm"] == "RRM"


def test_radar_without_a_matching_action_is_counted(tmp_path):
    """``AP_RRM_ACTION`` だけを数えるとレーダーを取りこぼすことを固定する。"""
    logs = tmp_path / "logs"
    write_metrics(logs)
    matched = START + timedelta(minutes=7)
    orphan = START + timedelta(minutes=20)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.radar_detected(matched, pre_channel=64, channel=36, ap=S.AP1),
        S.rrm_action(matched + timedelta(seconds=2), reason="radar-detected",
                     pre_channel=64, channel=36, ap=S.AP1),
        # 対応する ACTION が記録されていない検知
        S.radar_detected(orphan, pre_channel=52, channel=40, ap=S.AP2),
    ])
    res = run(logs)
    meta = res.meta

    assert meta["radar_detected"] == 2
    assert meta["radar_with_change"] == 2
    assert meta["radar_without_action"] == 1
    # ACTION の側は 1 件しかない（＝ ACTION だけでは検知を数えきれない）
    assert meta["changes_by_class"]["RADAR"] == 1


def test_multiple_actions_near_one_radar_do_not_double_count_the_detection(tmp_path):
    logs = tmp_path / "logs"
    write_metrics(logs)
    detected = START + timedelta(minutes=7)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.radar_detected(detected, pre_channel=64, channel=36, ap=S.AP1),
        S.rrm_action(detected + timedelta(seconds=1), reason="radar-detected",
                     pre_channel=64, channel=36, ap=S.AP1),
        S.rrm_action(detected + timedelta(seconds=2), reason="radar-detected",
                     pre_channel=36, channel=40, ap=S.AP1, band="24"),
        S.rrm_action(detected + timedelta(seconds=120), reason="radar-detected",
                     pre_channel=40, channel=44, ap=S.AP1, band="6"),
    ])
    res = run(logs)
    meta = res.meta

    assert meta["radar_detected"] == 1
    assert meta["radar_without_action"] == 0
    # ACTION は ACTION として 3 件数える。検知の側は 1 件のまま
    assert meta["changes_by_class"]["RADAR"] == 3


def test_config_changed_by_rrm_is_counted_but_not_analysed(tmp_path):
    logs = tmp_path / "logs"
    write_metrics(logs)
    when = START + timedelta(minutes=7)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(when, pre_channel=36, channel=44, ap=S.AP1),
        S.config_changed_by_rrm(when, ap=S.AP1),
        S.config_changed_by_rrm(when + timedelta(minutes=1), ap=S.AP2),
    ])
    res = run(logs)

    assert res.meta["config_changed_by_rrm_count"] == 2
    # 参考カウントであり、明細には入れない（AP_RRM_ACTION の 1 件だけが残る）
    assert res.meta["event_count"] == 1
    assert len(res.rows) == 1
    assert res.rows["reason"].tolist() == ["scheduled-site-rrm"]
