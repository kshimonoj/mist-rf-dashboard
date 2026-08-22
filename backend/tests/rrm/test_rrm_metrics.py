"""前後サンプルの突合・照合不可・汚染・出力列のテスト。合成データのみを使う。

固定したいのは次の点。

- 前後サンプルが推定間隔の 3 倍以上離れていたら ``too_far`` になり、**差分が空**になる
- 前後区間に別のチャネル変更イベントがある行に ``contaminated`` が立ち、
  **その行が除外されない**
- ``impact_clients`` は ``clients_before`` と一致する
- ``clients_*`` の 3 列は値がすべてゼロでも出力に存在する
- 利用率は 2.4 / 5 / 6GHz すべてを出す（イベントの ``band`` に関わらず）
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import _rrmsynth as S
import pandas as pd

from rrm import analysis, loader
from rrm import metrics as met

START = datetime(2026, 1, 1, 10, 0, 0)


def _samples(n: int, **values) -> list[dict[str, object]]:
    base = {"num_clients": 5, "util_24": 10, "util_5": 20, "util_6": 30}
    base.update(values)
    return [dict(base) for _ in range(n)]


def run(logs_dir: Path, **kwargs) -> analysis.AnalysisResult:
    return analysis.run_analysis(loader.collect_files(logs_dir), analysis.AnalysisParams(**kwargs))


def _row(res: analysis.AnalysisResult, index: int = 0) -> pd.Series:
    return res.rows.iloc[index]


def test_before_and_after_samples_are_the_nearest_ones(tmp_path):
    logs = tmp_path / "logs"
    rows = S.series(START, _samples(6), ap=S.AP1)
    # 直後のサンプルだけ端末数を変えて、平均ではなく 1 サンプルを取っていることを見る
    rows[2]["num_clients"] = 11
    S.write_metrics(logs / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=7), pre_channel=36, channel=44, ap=S.AP1),
    ])
    row = _row(run(logs))

    assert row["match_status"] == met.MATCH_OK
    assert row["before_timestamp"] == "2026-01-01 10:05:00"
    assert row["after_timestamp"] == "2026-01-01 10:10:00"
    assert row["clients_before"] == 5
    assert row["clients_after"] == 11
    assert row["clients_delta"] == 6


def test_samples_three_intervals_away_are_too_far_and_deltas_are_empty(tmp_path):
    """推定間隔の 3 倍以上離れていたら差分を出さない（照合不可として理由を残す）。"""
    logs = tmp_path / "logs"
    times = [
        START, START + timedelta(minutes=5), START + timedelta(minutes=10),
        # 50 分の欠測（間隔の推定は 300 秒のまま。最頻値を取るため）
        START + timedelta(minutes=60), START + timedelta(minutes=65),
        START + timedelta(minutes=70),
    ]
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.at_times(times, _samples(6), ap=S.AP1),
    )
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=30), pre_channel=36, channel=44, ap=S.AP1),
    ])
    res = run(logs)
    row = _row(res)

    assert res.meta["interval_seconds"] == 300.0
    assert row["match_status"] == met.MATCH_TOO_FAR
    # 差分は空。**なぜ照合できなかったかを読めるよう、前後の時刻と生値は残す**
    assert pd.isna(row["clients_delta"])
    for prefix in ("util_24", "util_5", "util_6"):
        assert pd.isna(row[f"{prefix}_delta"])
    assert row["before_timestamp"] == "2026-01-01 10:10:00"
    assert row["after_timestamp"] == "2026-01-01 11:00:00"
    assert res.meta["match_status_counts"][met.MATCH_TOO_FAR] == 1
    assert res.meta["unmatched_count"] == 1


def test_exactly_three_intervals_is_already_too_far(tmp_path):
    """境界（ちょうど 3 倍 = 900 秒）も照合不可にする。"""
    logs = tmp_path / "logs"
    times = [
        START, START + timedelta(minutes=5), START + timedelta(minutes=10),
        START + timedelta(minutes=25), START + timedelta(minutes=30),
        START + timedelta(minutes=35),
    ]
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.at_times(times, _samples(6), ap=S.AP1),
    )
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        # 直前サンプル 10:10 とちょうど 900 秒差
        S.rrm_action(START + timedelta(minutes=25), pre_channel=36, channel=44, ap=S.AP1),
    ])
    assert _row(run(logs))["match_status"] == met.MATCH_TOO_FAR


def test_missing_before_or_after_sample_is_reported(tmp_path):
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.series(START, _samples(6), ap=S.AP1),
    )
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START - timedelta(minutes=1), pre_channel=36, channel=44, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=40), pre_channel=36, channel=44, ap=S.AP1),
        # ap_metrics にサンプルが無い AP
        S.rrm_action(START + timedelta(minutes=7), pre_channel=36, channel=44, ap=S.AP3),
    ])
    res = run(logs)
    statuses = dict(zip(res.rows["ap_name"] + "@" + res.rows["event_timestamp"], res.rows["match_status"]))

    assert statuses["TEST-AP-01@2026-01-01 09:59:00"] == met.MATCH_NO_BEFORE
    assert statuses["TEST-AP-01@2026-01-01 10:40:00"] == met.MATCH_NO_AFTER
    assert statuses["TEST-AP-03@2026-01-01 10:07:00"] == met.MATCH_NO_AP
    # 照合できなくても行は残す
    assert len(res.rows) == 3


def test_contaminated_rows_are_flagged_and_not_removed(tmp_path):
    logs = tmp_path / "logs"
    rows = S.series(START, _samples(6), ap=S.AP1) + S.series(START, _samples(6), ap=S.AP2)
    S.write_metrics(logs / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        # AP1 の 2 件は同じ [10:05, 10:10] 区間に入るので相互に汚染する
        S.rrm_action(START + timedelta(minutes=6), pre_channel=36, channel=44, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=7), pre_channel=44, channel=48, ap=S.AP1, band="24"),
        # AP2 は 1 件だけなので汚染しない
        S.rrm_action(START + timedelta(minutes=6), pre_channel=36, channel=44, ap=S.AP2),
    ])
    res = run(logs)
    flags = dict(zip(res.rows["ap_name"] + "@" + res.rows["event_timestamp"], res.rows["contaminated"]))

    assert flags["TEST-AP-01@2026-01-01 10:06:00"] is True or flags["TEST-AP-01@2026-01-01 10:06:00"]
    assert bool(flags["TEST-AP-01@2026-01-01 10:07:00"]) is True
    assert bool(flags["TEST-AP-02@2026-01-01 10:06:00"]) is False
    # 汚染した行も除外しない
    assert len(res.rows) == 3
    assert res.meta["contaminated_count"] == 2


def test_radar_event_in_the_window_also_contaminates(tmp_path):
    """汚染判定は ``AP_RADAR_DETECTED`` も見る（バンドは問わない）。"""
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.series(START, _samples(6), ap=S.AP1),
    )
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=6), pre_channel=36, channel=44, ap=S.AP1),
        S.radar_detected(START + timedelta(minutes=8), pre_channel=52, channel=36, ap=S.AP1),
    ])
    res = run(logs)

    assert len(res.rows) == 1
    assert bool(res.rows.iloc[0]["contaminated"]) is True


def test_same_group_cross_band_events_do_not_contaminate(tmp_path):
    """同一AP・group窓以内の別バンド変更は、1回のRRMトリガーが複数バンドに
    及んだだけとみなし、互いを汚染扱いしない（31番: 誤検知の主因だったケース）。
    """
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.series(START, _samples(6), ap=S.AP1),
    )
    within = met.CONTAMINATION_GROUP_SECONDS - 1
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=6), pre_channel=1, channel=6, ap=S.AP1, band="24"),
        S.rrm_action(
            START + timedelta(minutes=6, seconds=within / 2),
            pre_channel=36, channel=44, ap=S.AP1, band="5",
        ),
        S.rrm_action(
            START + timedelta(minutes=6, seconds=within),
            pre_channel=37, channel=53, ap=S.AP1, band="6",
        ),
    ])
    res = run(logs)

    assert len(res.rows) == 3
    assert not res.rows["contaminated"].any()
    assert res.meta["contaminated_count"] == 0


def test_same_band_event_within_group_window_still_contaminates(tmp_path):
    """同一AP・group窓以内でも「同一バンド」の別イベントは汚染扱いのまま。"""
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.series(START, _samples(6), ap=S.AP1),
    )
    within = met.CONTAMINATION_GROUP_SECONDS - 1
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=6), pre_channel=36, channel=44, ap=S.AP1, band="5"),
        S.rrm_action(
            START + timedelta(minutes=6, seconds=within),
            pre_channel=44, channel=48, ap=S.AP1, band="5",
        ),
    ])
    res = run(logs)

    assert len(res.rows) == 2
    assert res.rows["contaminated"].all()
    assert res.meta["contaminated_count"] == 2


def test_event_outside_group_window_still_contaminates(tmp_path):
    """group窓の外（例: 30秒後）の同一APイベントは、前後サンプル区間内なら
    バンドに関わらず汚染扱いになる。"""
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.series(START, _samples(6), ap=S.AP1),
    )
    outside = met.CONTAMINATION_GROUP_SECONDS + 25
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=6), pre_channel=1, channel=6, ap=S.AP1, band="24"),
        S.rrm_action(
            START + timedelta(minutes=6, seconds=outside),
            pre_channel=36, channel=44, ap=S.AP1, band="5",
        ),
    ])
    res = run(logs)

    assert len(res.rows) == 2
    assert res.rows["contaminated"].all()
    assert res.meta["contaminated_count"] == 2


def test_other_ap_events_do_not_affect_contamination(tmp_path):
    """別APの、同時刻・同バンドのイベントがあっても互いに汚染しない
    （AP1の2件は相互に汚染する一方、AP2の1件は汚染しないこと）。"""
    logs = tmp_path / "logs"
    rows = S.series(START, _samples(6), ap=S.AP1) + S.series(START, _samples(6), ap=S.AP2)
    S.write_metrics(logs / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=6), pre_channel=36, channel=44, ap=S.AP1, band="5"),
        S.rrm_action(START + timedelta(minutes=6, seconds=2), pre_channel=44, channel=48, ap=S.AP1, band="5"),
        S.rrm_action(START + timedelta(minutes=6, seconds=2), pre_channel=36, channel=44, ap=S.AP2, band="5"),
    ])
    res = run(logs)
    flags = dict(zip(res.rows["ap_name"] + "@" + res.rows["event_timestamp"], res.rows["contaminated"]))

    assert bool(flags["TEST-AP-01@2026-01-01 10:06:00"]) is True
    assert bool(flags["TEST-AP-01@2026-01-01 10:06:02"]) is True
    assert bool(flags["TEST-AP-02@2026-01-01 10:06:02"]) is False


def test_contamination_grouping_does_not_change_classification_counts(tmp_path):
    """汚染判定のグループ化は分類・件数の集計には漏れ出さない
    （30番で固定した分類別カウントのロジックは、汚染フラグと独立であること）。"""
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.series(START, _samples(6), ap=S.AP1),
    )
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=6), pre_channel=1, channel=6, ap=S.AP1, band="24"),
        S.rrm_action(
            START + timedelta(minutes=6, seconds=1),
            pre_channel=36, channel=44, ap=S.AP1, band="5", reason="radar-detected",
        ),
        S.rrm_action(
            START + timedelta(minutes=6, seconds=2),
            pre_channel=37, channel=53, ap=S.AP1, band="6", reason="post-radar",
        ),
    ])
    res = run(logs)

    assert res.meta["event_count"] == 3
    assert res.meta["change_count"] == 3
    assert res.meta["changes_by_class"] == {"RADAR": 1, "POST_RADAR": 1, "RRM": 1}
    # この3件は互いに別バンド・group窓以内なので、グループ化により汚染は0件
    assert res.meta["contaminated_count"] == 0


def test_impact_clients_equals_clients_before(tmp_path):
    logs = tmp_path / "logs"
    rows = S.series(START, _samples(6), ap=S.AP1)
    for i, value in enumerate([2, 3, 4, 5, 6, 7]):
        rows[i]["num_clients"] = value
    S.write_metrics(logs / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=7), pre_channel=36, channel=44, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=17), pre_channel=44, channel=48, ap=S.AP1),
    ])
    res = run(logs)

    assert res.rows["impact_clients"].tolist() == res.rows["clients_before"].tolist()
    assert res.rows["impact_clients"].tolist() == [3, 5]
    assert res.meta["impact_total"] == 8


def test_client_columns_exist_even_when_every_value_is_zero(tmp_path):
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.series(START, _samples(6, num_clients=0), ap=S.AP1),
    )
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=7), pre_channel=36, channel=44, ap=S.AP1),
    ])
    res = run(logs)
    row = _row(res)

    for column in ("clients_before", "clients_after", "clients_delta", "impact_clients"):
        assert column in res.rows.columns
        assert row[column] == 0
    assert res.meta["impact_total"] == 0


def test_all_three_bands_are_reported_regardless_of_the_event_band(tmp_path):
    logs = tmp_path / "logs"
    rows = S.series(START, _samples(6), ap=S.AP1)
    rows[2].update(
        radio_24_utilization=15, radio_5_utilization=25, radio_6_utilization=35
    )
    S.write_metrics(logs / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        # band=24 のイベントでも 5 / 6GHz の利用率を出す
        S.rrm_action(START + timedelta(minutes=7), pre_channel=1, channel=6, ap=S.AP1, band="24"),
    ])
    row = _row(run(logs))

    assert row["band"] == "24"
    assert (row["util_24_before"], row["util_24_after"], row["util_24_delta"]) == (10, 15, 5)
    assert (row["util_5_before"], row["util_5_after"], row["util_5_delta"]) == (20, 25, 5)
    assert (row["util_6_before"], row["util_6_after"], row["util_6_delta"]) == (30, 35, 5)


def test_result_columns_are_fixed_and_ordered(tmp_path):
    logs = tmp_path / "logs"
    S.write_metrics(
        logs / "ap_metrics_20260101_1000_TZT.csv",
        S.series(START, _samples(6), ap=S.AP1),
    )
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=7), pre_channel=36, channel=44, ap=S.AP1),
    ])
    res = run(logs)

    assert list(res.rows.columns) == list(analysis.RESULT_COLUMNS)
    # チャネル番号の引き算は出さない（36→52 の "+16" に物理的な意味が無いため）
    assert not any("channel_delta" in c or "delta_channel" in c for c in res.rows.columns)
