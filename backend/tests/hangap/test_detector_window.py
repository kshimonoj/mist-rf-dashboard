"""期間（window_start / window_end）を指定したときは、その期間のサンプルだけで分析する。

窓の外を「どこまで見に行くか」の上限が無いと、区間が指定期間の外へ無制限に伸びる
（実測で 6 時間の窓を指定して、ゼロ終了が窓の 6 日後になった）。窓を指定したら
区間の終了・回復判定・``AP最大clients``・サイト全体トレンドのすべてを窓内で完結させる。

例外はイベント相関だけで、イベントは ``ゼロ終了 ± event_window`` で相関を取るため
窓の外のものも参照する（``event_window`` で上限が決まるので無制限には伸びない）。

合成データのみを使う（実データ由来の値は書かない）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

import _synth as S

from hangap.detector import STATUS_ONGOING, STATUS_RECOVERED, detect
from hangap.loader import load

INTERVAL = 300  # 5 分間隔
START = datetime(2026, 1, 1, 9, 0, 5)


def ts(index: int) -> datetime:
    return START + timedelta(seconds=INTERVAL * index)


#: index0-2 は端末あり → index3-19 がゼロ → index20-23 で回復。
#: 回復は窓の外（window_end より後）に置く。窓の外の端末数は窓内より大きくしてある。
COUNTS: list[int] = [9, 5, 5] + [0] * 17 + [8] * 4

WINDOW_START = ts(1)   # index0（9 clients）は窓の外
WINDOW_END = ts(12)    # index12 以降（回復も含む）は窓の外

#: 窓内でゼロが続くサンプル（index3〜11）
ZERO_SAMPLES_IN_WINDOW = 9


def ap_rows(counts, *, index: int = 1, start: datetime = START) -> list[dict]:
    """1 AP 分の行。``counts`` はサンプルごとの num_clients。"""
    return [
        S.metrics_row(
            start + timedelta(seconds=INTERVAL * i),
            ap_id=f"test-ap-{index:04d}",
            ap_name=f"TEST-AP-{index:02d}",
            mac=f"aabbccddee{index:02d}",
            num_clients=count,
        )
        for i, count in enumerate(counts)
    ]


def run(tmp_path, rows, events=None, **kwargs):
    S.write_metrics(tmp_path / "ap_metrics.csv", rows)
    if events is not None:
        S.write_events(tmp_path / "ap_events.csv", events)
    res = load(tmp_path)
    return detect(res.metrics, res.events, res.gaps, **kwargs)


# ---------------------------------------------------------------------------
# 要件1: 窓の外を見ない
# ---------------------------------------------------------------------------


def test_zero_run_crossing_window_end_is_ongoing(tmp_path):
    """window_end の時点でゼロが続いていれば「継続中」。窓の外の回復は拾わない。"""
    out = run(tmp_path, ap_rows(COUNTS), window_start=WINDOW_START, window_end=WINDOW_END)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["回復状況"] == STATUS_ONGOING
    assert row["ゼロ終了"] == ts(11)
    assert row["ゼロ終了"] < WINDOW_END
    # 窓の外（index20）の回復は見えない
    assert pd.isna(row["回復時刻"])
    assert pd.isna(row["直後clients（回復時）"])


def test_zero_count_does_not_exceed_the_window_length(tmp_path):
    """連続ゼロ回数は窓の長さ（÷ サンプリング間隔）で決まる上限を超えない。"""
    out = run(tmp_path, ap_rows(COUNTS), window_start=WINDOW_START, window_end=WINDOW_END)

    max_samples = (WINDOW_END - WINDOW_START).total_seconds() / INTERVAL
    assert int(out.iloc[0]["連続ゼロ回数"]) == ZERO_SAMPLES_IN_WINDOW
    assert int(out.iloc[0]["連続ゼロ回数"]) <= max_samples


# ---------------------------------------------------------------------------
# 要件4/5: 窓なし・片側だけの指定
# ---------------------------------------------------------------------------


def test_no_window_uses_all_loaded_samples(tmp_path):
    """窓を省略すれば従来どおり、読み込んだ全データが対象になる。"""
    out = run(tmp_path, ap_rows(COUNTS))

    assert len(out) == 1
    row = out.iloc[0]
    assert row["回復状況"] == STATUS_RECOVERED
    assert int(row["連続ゼロ回数"]) == 17
    assert row["回復時刻"] == ts(20)
    assert int(row["AP最大clients"]) == 9  # 窓が無いので全データの最大


def test_window_start_only_keeps_samples_after_it(tmp_path):
    """window_start だけの指定でも、その時刻以降のサンプルは従来どおり全部使う。"""
    out = run(tmp_path, ap_rows(COUNTS), window_start=WINDOW_START)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["回復状況"] == STATUS_RECOVERED  # 窓の右端が無いので回復まで追える
    assert int(row["連続ゼロ回数"]) == 17
    assert int(row["AP最大clients"]) == 8  # index0 の 9 は窓の外


def test_window_end_only_cuts_the_run_at_the_window(tmp_path):
    """window_end だけの指定でも、そこでサンプルが尽きて「継続中」になる。"""
    out = run(tmp_path, ap_rows(COUNTS), window_end=WINDOW_END)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["回復状況"] == STATUS_ONGOING
    assert row["ゼロ終了"] == ts(11)
    assert int(row["AP最大clients"]) == 9  # index0 は窓の内側


# ---------------------------------------------------------------------------
# 要件6/7: AP最大clients・サイト全体トレンド
# ---------------------------------------------------------------------------


def test_ap_max_clients_is_the_max_inside_the_window(tmp_path):
    """AP最大clients は窓内の最大値（窓の外の 9 / 8 は拾わない）。"""
    out = run(tmp_path, ap_rows(COUNTS), window_start=WINDOW_START, window_end=WINDOW_END)

    assert int(out.iloc[0]["AP最大clients"]) == 5


def test_site_totals_ignore_samples_outside_the_window(tmp_path):
    """サイト合計clients は窓内のサンプルだけで算出する。

    2 台目の AP は窓の外（window_start より前）にしかサンプルを持たない。窓の外を
    参照していると、その端末数が合計に混ざる（ffill で引きずられる）。
    """
    rows = ap_rows(COUNTS) + [
        S.metrics_row(
            ts(0),
            ap_id="test-ap-0002",
            ap_name="TEST-AP-02",
            mac="aabbccddee02",
            num_clients=100,
        )
    ]
    out = run(tmp_path, rows, window_start=WINDOW_START, window_end=WINDOW_END)

    assert len(out) == 1
    row = out.iloc[0]
    assert float(row["サイト合計clients(ゼロ開始時)"]) == 0.0
    assert float(row["サイト合計clients(ゼロ終了時)"]) == 0.0

    # 窓を外せば同じデータでも 100 が合計に入る（窓の効果であることの確認）
    without_window = run(tmp_path, rows)
    assert float(without_window.iloc[0]["サイト合計clients(ゼロ開始時)"]) == 100.0


# ---------------------------------------------------------------------------
# 要件8: イベント相関だけは窓の外を見てよい
# ---------------------------------------------------------------------------


def test_events_after_window_end_still_correlate(tmp_path):
    """window_end より後のイベントも、ゼロ終了 ± event_window に入れば相関する。

    イベントはメトリクスとは別のログであり、窓を 1 件も超えないと窓の右端付近の区間で
    相関が取れなくなる。ここは event_window で上限が決まるため、窓の外へ無制限に
    伸びる問題は起きない。
    """
    event_at = WINDOW_END + timedelta(minutes=10)  # 窓の外、かつ ゼロ終了 +15 分
    out = run(
        tmp_path,
        ap_rows(COUNTS),
        events=[S.event_row(event_at)],
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        event_window=timedelta(minutes=30),
    )

    assert len(out) == 1
    row = out.iloc[0]
    assert row["ゼロ終了"] < WINDOW_END < event_at
    assert row["AP Event（±30分）"] == "あり"
    assert row["Event時刻"].startswith(event_at.strftime("%Y-%m-%d %H:%M"))
