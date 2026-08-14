"""イベント相関のテスト（合成データのみ）。

重み付け・分類・フィルタは行わず、ゼロ終了 ± event_window に入るものを
すべてそのまま時刻昇順で並べることを確認する。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import _synth as S

from hangap.detector import EVENT_SEPARATOR, detect
from hangap.loader import load

INTERVAL = 300
START = datetime(2026, 1, 1, 9, 0, 5)
WINDOW_START = datetime(2026, 1, 1, 9, 0)
WINDOW_END = datetime(2026, 1, 2, 9, 0)

#: [5,5] + ゼロ 10 + [5]*3 → ゼロ終了は index 11
COUNTS = [5, 5] + [0] * 10 + [5] * 3
ZERO_END = START + timedelta(seconds=INTERVAL * 11)


def metrics_rows() -> list[dict]:
    return [
        S.metrics_row(START + timedelta(seconds=INTERVAL * i), num_clients=c)
        for i, c in enumerate(COUNTS)
    ]


def run(tmp_path, events, **kwargs):
    S.write_metrics(tmp_path / "ap_metrics.csv", metrics_rows())
    S.write_events(tmp_path / "ap_events.csv", events)
    res = load(tmp_path)
    return detect(
        res.metrics,
        res.events,
        res.gaps,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        **kwargs,
    )


def split(value: str) -> list[str]:
    return str(value).split(EVENT_SEPARATOR)


def test_events_within_window_are_listed_in_time_order(tmp_path):
    """ゼロ終了 ±30 分のイベントが時刻昇順で並び、4 列の件数・順序が揃う。"""
    events = [
        S.event_row(ZERO_END + timedelta(minutes=5), event_type="AP_RESTARTED"),
        S.event_row(ZERO_END - timedelta(minutes=20), event_type="AP_DISCONNECTED"),
        S.event_row(ZERO_END + timedelta(minutes=90), event_type="AP_CONFIGURED"),  # 窓の外
    ]
    out = run(tmp_path, events)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["AP Event（±30分）"] == "あり"
    assert split(row["Event種別"]) == ["AP_DISCONNECTED", "AP_RESTARTED"]
    assert split(row["ゼロ終了との差(分)"]) == ["-20", "+5"]
    lengths = {len(split(row[c])) for c in ("Event時刻", "ゼロ終了との差(分)", "Event種別", "Event詳細")}
    assert lengths == {2}


def test_no_events_leaves_the_columns_empty(tmp_path):
    """窓の外のイベントしか無ければ、イベント列はすべて空。"""
    out = run(tmp_path, [S.event_row(ZERO_END + timedelta(minutes=45))])

    row = out.iloc[0]
    for col in ("AP Event（±30分）", "Event時刻", "ゼロ終了との差(分)", "Event種別", "Event詳細"):
        assert row[col] == ""


def test_events_of_other_ap_are_not_matched(tmp_path):
    """突合は ap_name。他 AP のイベントは拾わない。"""
    out = run(
        tmp_path,
        [S.event_row(ZERO_END, ap_name="TEST-AP-99", ap_mac="aabbccddee99")],
    )
    assert out.iloc[0]["AP Event（±30分）"] == ""


def test_event_window_is_configurable(tmp_path):
    """event_window を狭めれば拾わなくなる。"""
    events = [S.event_row(ZERO_END + timedelta(minutes=20))]
    assert run(tmp_path, events).iloc[0]["AP Event（±30分）"] == "あり"

    narrow = run(tmp_path, events, event_window=timedelta(minutes=10))
    assert narrow.iloc[0]["AP Event（±30分）"] == ""


def test_event_detail_formats_only_present_values(tmp_path):
    """Event詳細 は値のあるものだけを連結する（無ければ空文字）。"""
    events = [
        S.event_row(
            ZERO_END - timedelta(minutes=1),
            event_type="AP_RRM_ACTION",
            reason="interference-ap-co-channel",
            band="5",
            channel="40",
            pre_channel="36",
            bandwidth="20",
            pre_bandwidth="40",
        ),
        S.event_row(ZERO_END, event_type="AP_RESTARTED", reason="restart_by_user"),
        S.event_row(ZERO_END + timedelta(minutes=1), event_type="AP_CONFIGURED"),
    ]
    out = run(tmp_path, events)
    details = split(out.iloc[0]["Event詳細"])

    assert details[0] == (
        "reason=interference-ap-co-channel, band=5, channel=36→40, bandwidth=40→20"
    )
    assert details[1] == "reason=restart_by_user"
    assert details[2] == ""


def test_all_matching_events_are_listed_without_filtering(tmp_path):
    """同種・同時刻のイベントも含め、該当するものはすべて列挙する。"""
    events = [
        S.event_row(ZERO_END, event_type="AP_CONFIG_CHANGED_BY_RRM"),
        S.event_row(ZERO_END, event_type="AP_RRM_ACTION"),
        S.event_row(ZERO_END, event_type="AP_CONFIGURED"),
        S.event_row(ZERO_END + timedelta(minutes=2), event_type="AP_RESTARTED"),
    ]
    out = run(tmp_path, events)
    row = out.iloc[0]

    assert len(split(row["Event種別"])) == 4
    assert split(row["ゼロ終了との差(分)"]) == ["0", "0", "0", "+2"]


def test_events_argument_may_be_omitted(tmp_path):
    """events を渡さなくても検出自体は動く（イベント列が空になるだけ）。"""
    S.write_metrics(tmp_path / "ap_metrics.csv", metrics_rows())
    res = load(tmp_path)
    out = detect(
        res.metrics,
        None,
        res.gaps,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert len(out) == 1
    assert out.iloc[0]["Event種別"] == ""
