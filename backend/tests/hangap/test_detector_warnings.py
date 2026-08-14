"""窓に対してデータ範囲が足りない場合の警告（UserWarning）のテスト。

History Log は 1 時間単位のため、任意の時間帯を分析するには複数ファイルの結合が必要になり、
窓の外側のデータが欠けやすい。欠けたまま検出すると誤分類につながるため、detect() は
エラーにはせず警告だけを出す。合成データで意図的にデータ範囲を狭めて発生させる。
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import _synth as S

from hangap.detector import detect
from hangap.loader import load

INTERVAL = 300
START = datetime(2026, 1, 1, 9, 0, 5)

#: 十分な余裕を持たせた「問題ない」窓（前後にサンプルがある）
SAFE_START = START + timedelta(seconds=INTERVAL * 2)
SAFE_END = START + timedelta(seconds=INTERVAL * 8)


def run(tmp_path, rows, events=None, **kwargs):
    S.write_metrics(tmp_path / "ap_metrics.csv", rows)
    if events is not None:
        S.write_events(tmp_path / "ap_events.csv", events)
    res = load(tmp_path)
    return detect(res.metrics, res.events, res.gaps, **kwargs)


def _rows(count: int = 12) -> list[dict]:
    """0 から始まらない適当な系列（警告の有無だけを見るので中身は問わない）。"""
    return [
        S.metrics_row(START + timedelta(seconds=INTERVAL * i), num_clients=(1 if i % 2 else 0))
        for i in range(count)
    ]


def test_no_warning_when_coverage_is_sufficient(tmp_path):
    """窓の前後に十分なサンプルがあれば警告は出ない。"""
    rows = _rows(12)  # データは index0〜11（START 〜 START+55min）

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run(tmp_path, rows, window_start=SAFE_START, window_end=SAFE_END)


def test_warns_when_no_data_before_window_start(tmp_path):
    """window_start より前のサンプルが 1 件も無ければ警告する。"""
    rows = _rows(12)
    window_start = START - timedelta(minutes=30)  # データより前

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(tmp_path, rows, window_start=window_start, window_end=SAFE_END)

    messages = [str(w.message) for w in caught]
    assert any("window_start" in m and "より前" in m for m in messages)


def test_warns_when_data_does_not_reach_window_end(tmp_path):
    """window_end までデータが伸びていなければ警告する（意図的にデータ範囲を狭めた合成データ）。

    データは START から 12 サンプル（55 分）分しかないのに、window_end をその先に置く。
    """
    rows = _rows(12)  # データ終端は START + 55min
    window_end = START + timedelta(hours=3)  # データより大幅に先

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(tmp_path, rows, window_start=SAFE_START, window_end=window_end)

    messages = [str(w.message) for w in caught]
    assert any("window_end" in m and "届いていません" in m and "継続中" in m for m in messages)


def test_warns_when_events_do_not_cover_window_end_plus_event_window(tmp_path):
    """イベントが window_end + event_window までカバーしていなければ警告する。"""
    rows = _rows(20)  # メトリクス自体は window_end を十分カバーする
    window_end = START + timedelta(seconds=INTERVAL * 15)
    events = [S.event_row(START + timedelta(seconds=INTERVAL * 5))]  # window_end よりずっと手前

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(
            tmp_path, rows, events=events,
            window_start=SAFE_START, window_end=window_end,
            event_window=timedelta(minutes=30),
        )

    messages = [str(w.message) for w in caught]
    assert any("event_window" in m and "イベント" in m and "届いていません" in m for m in messages)


def test_no_window_end_means_no_end_coverage_warning(tmp_path):
    """window_end を省略した場合は、右端カバレッジの警告そのものが発生しない。"""
    rows = _rows(4)  # 意図的にごく短いデータ

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run(tmp_path, rows, window_start=SAFE_START, window_end=None)
