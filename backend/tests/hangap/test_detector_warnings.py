"""窓に対してデータ範囲が足りない場合の警告（UserWarning）のテスト。

History Log は 1 時間単位のため、任意の時間帯を分析するには複数ファイルの結合が必要になり、
窓の外側のデータが欠けやすい。欠けたまま検出すると誤分類につながるため、detect() は
エラーにはせず警告だけを出す。合成データで意図的にデータ範囲を狭めて発生させる。

窓を指定したら窓内のサンプルだけで分析するため、「窓の先頭で始まる区間が検出されない」ことは
**常に真** であり、警告しない（毎回鳴る警告は読み飛ばされ、本当に問題があるときの警告まで
無視されるようになる）。警告するのは次の 3 つだけ。

  1. データ開始が window_start より後（指定した期間の一部にデータが無い）
  2. データ終端が window_end に届いていない（しきい値 log_save_interval）
  3. イベント終端が window_end + event_window に届いていない
     （しきい値 log_save_interval + event_window）
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

#: _rows(12) のデータ終端（START + 55min）
DATA_END_12 = START + timedelta(seconds=INTERVAL * 11)


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


def test_warns_when_data_starts_after_window_start(tmp_path):
    """データが window_start より後から始まっていれば警告する。

    窓の先頭で始まる区間が検出されない件とは別で、「指定した期間の一部にそもそも
    分析対象のサンプルが無い」という状態である。
    """
    rows = _rows(12)
    window_start = START - timedelta(minutes=30)  # データより前

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(tmp_path, rows, window_start=window_start, window_end=SAFE_END)

    messages = [str(w.message) for w in caught]
    assert any("window_start" in m and "より後から" in m for m in messages)


def test_no_warning_when_data_covers_window_start(tmp_path):
    """データが window_start をカバーしていれば、窓の先頭についての警告は出ない。

    窓を指定したら窓の外は見ないので「窓の先頭で始まる区間は検出されない」が、
    それは仕様どおりであり警告しない（毎回鳴る警告にしない）。
    """
    rows = _rows(12)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        # 窓の開始をデータの 1 サンプル目ぴったりに置く（窓の外にサンプルは 1 件も無い）
        run(tmp_path, rows, window_start=START, window_end=SAFE_END)


def test_no_warning_when_data_starts_within_one_sampling_interval(tmp_path):
    """1 サンプル分にも満たないずれでは警告しない。

    毎正時保存のログを「16:00 から」のようにぴったり指定すると、1 サンプル目は必ず
    数秒〜数分後になる。これをデータの欠けとして毎回警告すると読み飛ばされる。
    """
    rows = _rows(12)
    window_start = START - timedelta(seconds=INTERVAL - 5)  # 不足はサンプリング間隔未満

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run(tmp_path, rows, window_start=window_start, window_end=SAFE_END)


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
    """イベントが window_end + event_window + しきい値 を大幅に超えて遅れていれば警告する。"""
    rows = _rows(20)  # メトリクス自体は window_end を十分カバーする
    window_end = START + timedelta(seconds=INTERVAL * 15)
    events = [S.event_row(START)]  # window_end よりずっと手前

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


def test_no_warning_when_window_end_deficit_is_below_log_save_interval(tmp_path):
    """window_end への不足がログ保存間隔未満なら警告しない（毎正時保存の遅延は仕様どおり）。"""
    rows = _rows(12)  # データ終端は START + 55min
    log_save_interval = timedelta(minutes=60)
    window_end = DATA_END_12 + timedelta(minutes=30)  # 不足30分 < 60分

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run(
            tmp_path, rows,
            window_start=SAFE_START, window_end=window_end,
            log_save_interval=log_save_interval,
        )


def test_warns_when_window_end_deficit_exceeds_log_save_interval(tmp_path):
    """window_end への不足がログ保存間隔以上なら、これまでどおり警告する。"""
    rows = _rows(12)  # データ終端は START + 55min
    log_save_interval = timedelta(minutes=60)
    window_end = DATA_END_12 + timedelta(minutes=90)  # 不足90分 >= 60分

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(
            tmp_path, rows,
            window_start=SAFE_START, window_end=window_end,
            log_save_interval=log_save_interval,
        )

    messages = [str(w.message) for w in caught]
    assert any("window_end" in m and "届いていません" in m and "継続中" in m for m in messages)


def test_window_start_warning_is_unaffected_by_log_save_interval(tmp_path):
    """window_start 側の警告は log_save_interval に関係なく出る（しきい値はサンプリング間隔）。"""
    rows = _rows(12)
    window_start = START - timedelta(minutes=30)  # 不足はログ保存間隔（60分）よりは小さい

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(
            tmp_path, rows,
            window_start=window_start, window_end=SAFE_END,
            log_save_interval=timedelta(minutes=60),
        )

    messages = [str(w.message) for w in caught]
    assert any("window_start" in m and "より後から" in m for m in messages)


def test_no_warning_when_event_deficit_is_below_log_save_interval(tmp_path):
    """イベント側の不足が小さければ（しきい値を大幅に下回る）警告しない。"""
    rows = _rows(20)  # メトリクス自体は window_end を十分カバーする
    window_end = START + timedelta(seconds=INTERVAL * 15)
    event_window = timedelta(minutes=30)
    required = window_end + event_window
    events = [S.event_row(required - timedelta(minutes=20))]  # 不足20分 < 90分

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run(
            tmp_path, rows, events=events,
            window_start=SAFE_START, window_end=window_end,
            event_window=event_window,
            log_save_interval=timedelta(minutes=60),
        )


def test_no_warning_when_event_deficit_is_below_log_save_interval_plus_event_window(tmp_path):
    """イベント側のしきい値は log_save_interval + event_window。

    要求ライン自体が window_end + event_window のため、data_max 側と同じ
    log_save_interval だけをしきい値にすると、収集が追いついていても event_window 分
    だけ過検知してしまう。実データで観測した状態（window_end 08:32 / event_window 30分 /
    イベント終端 07:49 → 不足1時間13分、だがメトリクス終端は07:56で収集は追いついている）
    を模した合成データで、しきい値90分（60分+30分）未満なら警告しないことを確認する。
    """
    rows = _rows(20)  # メトリクス自体は window_end を十分カバーする
    window_end = START + timedelta(seconds=INTERVAL * 15)
    event_window = timedelta(minutes=30)
    required = window_end + event_window
    # 不足80分: log_save_interval(60分)は超えるが、log_save_interval + event_window(90分)未満
    events = [S.event_row(required - timedelta(minutes=80))]

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run(
            tmp_path, rows, events=events,
            window_start=SAFE_START, window_end=window_end,
            event_window=event_window,
            log_save_interval=timedelta(minutes=60),
        )


def test_warns_when_event_deficit_exceeds_log_save_interval_plus_event_window(tmp_path):
    """イベント側の不足が log_save_interval + event_window 以上なら、これまでどおり警告する。"""
    rows = _rows(20)
    window_end = START + timedelta(seconds=INTERVAL * 15)
    event_window = timedelta(minutes=30)
    required = window_end + event_window
    events = [S.event_row(required - timedelta(minutes=120))]  # 不足120分 >= 90分

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(
            tmp_path, rows, events=events,
            window_start=SAFE_START, window_end=window_end,
            event_window=event_window,
            log_save_interval=timedelta(minutes=60),
        )

    messages = [str(w.message) for w in caught]
    assert any("event_window" in m and "イベント" in m and "届いていません" in m for m in messages)
