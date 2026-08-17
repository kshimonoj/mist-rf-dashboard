"""窓に対してデータ範囲が足りない場合の警告（UserWarning）のテスト。

History Log は 1 時間単位のため、任意の時間帯を分析するには複数ファイルの結合が必要になり、
窓の外側のデータが欠けやすい。欠けたまま検出すると誤分類につながるため、detect() は
エラーにはせず警告だけを出す。合成データで意図的にデータ範囲を狭めて発生させる。

窓を指定したら窓内のサンプルだけで分析するため、「窓の先頭で始まる区間が検出されない」ことは
**常に真** であり、警告しない（毎回鳴る警告は読み飛ばされ、本当に問題があるときの警告まで
無視されるようになる）。警告するのは次の 3 つだけ。

  1. データ開始が window_start より後（指定した期間の一部にデータが無い）
  2. データ終端が window_end に届いていない（しきい値 log_save_interval）
  3. イベントの収集がメトリクスより遅れている
     （``メトリクス終端 - イベント終端`` > log_save_interval）

3 は以前「イベント終端が window_end + event_window に届いているか」で判定していたが、
「最後のイベントの時刻」は収集の新しさを表さない（イベントは疎なので、収集が健全でも
最後のイベントが数時間前になる）。収集が追いついているのに警告が出ていたため、
メトリクス終端とのラグで判定するよう変えた。
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import _synth as S
import pandas as pd

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


# ---------------------------------------------------------------------------
# イベントのラグ（メトリクス終端 - イベント終端）
# ---------------------------------------------------------------------------

#: _rows(20) のメトリクス終端（START + 95min）
DATA_END_20 = START + timedelta(seconds=INTERVAL * 19)


def test_no_warning_when_event_lag_is_within_log_save_interval(tmp_path):
    """イベントのラグがログ保存間隔未満なら警告しない（収集は追いついている）。

    実データで観測した状態を模した合成データ。メトリクス終端との差が 52 分しかないのに、
    旧基準（イベント終端が window_end + event_window に届いているか）では不足
    1 時間 39 分として警告が出ていた。イベントは疎なので「最後のイベントの時刻」は
    収集の新しさを表さず、その警告は収集が正常でも鳴っていた。

    ここでは旧基準なら鳴る（不足 99 分 >= しきい値 90 分）条件のまま、
    新基準では 1 件も警告が出ないことを確認する。
    """
    rows = _rows(20)  # メトリクス終端は START + 95min
    event_window = timedelta(minutes=30)
    # データ終端の 17 分先（log_save_interval 未満なのでデータ終端側の警告も出ない）
    window_end = DATA_END_20 + timedelta(minutes=17)
    events = [S.event_row(DATA_END_20 - timedelta(minutes=52))]  # ラグ52分 < 60分

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run(
            tmp_path, rows, events=events,
            window_start=SAFE_START, window_end=window_end,
            event_window=event_window,
            log_save_interval=timedelta(minutes=60),
        )


def test_warns_when_event_lag_exceeds_log_save_interval(tmp_path):
    """イベントのラグがログ保存間隔を超えたら警告する（収集が止まっている疑い）。"""
    rows = _rows(20)
    events = [S.event_row(DATA_END_20 - timedelta(minutes=75))]  # ラグ75分 > 60分

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(
            tmp_path, rows, events=events,
            window_start=SAFE_START, window_end=DATA_END_20,
            log_save_interval=timedelta(minutes=60),
        )

    messages = [str(w.message) for w in caught]
    assert any("イベントの収集がメトリクスより" in m and "遅れています" in m for m in messages)
    # window_end を基準にした表現は使わない（何が起きているかが分からない）
    assert not any("event_window" in m for m in messages)


def test_event_lag_warning_does_not_depend_on_window_end(tmp_path):
    """ラグの判定は窓と無関係（window_end を省略しても出る）。"""
    rows = _rows(20)
    events = [S.event_row(DATA_END_20 - timedelta(minutes=75))]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run(
            tmp_path, rows, events=events,
            window_start=None, window_end=None,
            log_save_interval=timedelta(minutes=60),
        )

    assert any("イベントの収集がメトリクスより" in str(w.message) for w in caught)


def test_no_event_lag_warning_when_metrics_are_empty():
    """メトリクスが 1 件も無ければ、このラグ警告は出さない（比較対象が無い）。

    ローダを通さず detect() を直接呼ぶ（ap_metrics が 0 行のログは load() が
    そもそも受け付けない）。イベントだけがある状態を作る。
    """
    metrics = pd.DataFrame(
        {c: pd.Series(dtype="object") for c in
         ("ap_id", "ap_name", "site_name", "timestamp", "num_clients", "status")}
    )
    events = pd.DataFrame({
        "ap_name": ["TEST-AP-01"],
        "event_timestamp": [pd.Timestamp(START)],
        "event_type": ["AP_CONFIG_CHANGED"],
    })

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = detect(
            metrics, events, None,
            window_end=START + timedelta(days=7),
            log_save_interval=timedelta(minutes=60),
        )
    assert len(result) == 0
