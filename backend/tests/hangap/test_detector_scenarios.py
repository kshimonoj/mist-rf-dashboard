"""合成データによるシナリオ A〜H。

ゴールデンデータが無い環境でも検出ロジックの正しさを確認できるようにする。
合成データのみを使う（実データ由来の値は書かない）。

最重要は E（ギャップ跨ぎ）。ギャップを無視した実装でも A〜D は通ってしまうため、
E が無いと「欠測を跨いで連続ゼロを数えてしまう」最大の失敗モードを検出できない。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

import _synth as S

from hangap.detector import (
    RESULT_COLUMNS,
    STATUS_CUT_AP_DOWN,
    STATUS_CUT_GAP,
    STATUS_ONGOING,
    STATUS_RECOVERED,
    detect,
)
from hangap.loader import load

INTERVAL = 300  # 5 分間隔
START = datetime(2026, 1, 1, 9, 0, 5)


def ap_rows(
    index: int,
    counts,
    *,
    statuses=None,
    skip=(),
    start: datetime = START,
    interval: int = INTERVAL,
) -> list[dict]:
    """1 AP 分の行を作る。``counts`` はサンプルごとの num_clients。"""
    rows = []
    for i, count in enumerate(counts):
        if i in skip:
            continue
        rows.append(
            S.metrics_row(
                start + timedelta(seconds=interval * i),
                ap_id=f"test-ap-{index:04d}",
                ap_name=f"TEST-AP-{index:02d}",
                mac=f"aabbccddee{index:02d}",
                num_clients=count,
                status=(statuses[i] if statuses else "connected"),
            )
        )
    return rows


def run(tmp_path, rows, **kwargs):
    """CSV へ書き出し、ローダを通してから検出する（gaps はローダが作るものを使う）。

    既定では window_start / window_end とも省略する（読み込んだ全範囲を使う）。
    """
    S.write_metrics(tmp_path / "ap_metrics.csv", rows)
    res = load(tmp_path)
    return detect(
        res.metrics,
        res.events,
        kwargs.pop("gaps", res.gaps),
        window_start=kwargs.pop("window_start", None),
        window_end=kwargs.pop("window_end", None),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# A: 本物のハング
# ---------------------------------------------------------------------------


def test_scenario_a_real_hang(tmp_path):
    """20 AP のうち 1 台だけ 16→0。他は端末を保持 → 検出され、退場疑い=False。"""
    rows = ap_rows(1, [16] * 4 + [0] * 12 + [16] * 8)
    for i in range(2, 21):
        rows += ap_rows(i, [8] * 24)

    out = run(tmp_path, rows)

    assert list(out.columns) == list(RESULT_COLUMNS)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["ap_name"] == "TEST-AP-01"
    assert row["区間番号"] == 1
    assert row["AP内区間数"] == 1
    assert row["連続ゼロ回数"] == 12
    assert row["回復状況"] == STATUS_RECOVERED
    assert row["直前clients"] == 16
    assert row["直後clients（回復時）"] == 16
    assert row["AP最大clients"] == 16
    assert row["ゼロ開始"] == START + timedelta(seconds=INTERVAL * 4)
    assert row["ゼロ終了"] == START + timedelta(seconds=INTERVAL * 15)
    assert row["回復時刻"] == START + timedelta(seconds=INTERVAL * 16)
    assert row["ゼロ直前時刻"] == START + timedelta(seconds=INTERVAL * 3)
    # サイト全体は保たれている（1 台だけの問題）。
    # 合計は「その時刻の全 AP」なので、ゼロ開始時は当該 AP の 0 も含む。
    assert row["サイト合計clients(ゼロ開始時)"] == 19 * 8
    assert row["サイト合計clients(ゼロ終了時)"] == 19 * 8
    assert row["サイト全体変化率"] == 0.0
    assert bool(row["退場疑い"]) is False


# ---------------------------------------------------------------------------
# B: 退場（サイト全体が減衰）
# ---------------------------------------------------------------------------


def test_scenario_b_exodus(tmp_path):
    """全 AP が減衰してゼロへ → 検出はされるが退場疑い=True。"""
    rows = ap_rows(1, [10, 8, 6, 4, 2] + [0] * 7)  # 先にゼロへ落ちる AP
    for i in range(2, 21):
        rows += ap_rows(i, [10, 10, 10, 8, 6, 4, 2] + [0] * 5)

    out = run(tmp_path, rows)

    target = out[out["ap_name"] == "TEST-AP-01"]
    assert len(target) == 1
    row = target.iloc[0]
    assert row["連続ゼロ回数"] == 7
    assert row["回復状況"] == STATUS_ONGOING
    assert row["サイト合計clients(ゼロ開始時)"] > 0
    assert row["サイト合計clients(ゼロ終了時)"] == 0
    assert row["サイト全体変化率"] == -1.0
    assert bool(row["退場疑い"]) is True

    # しきい値は設定可能（-1 より下は無い＝どの区間も退場疑いにならない）
    strict = run(tmp_path, rows, exodus_threshold=-1.5)
    assert not strict["退場疑い"].any()


# ---------------------------------------------------------------------------
# C: 元々ゼロ
# ---------------------------------------------------------------------------


def test_scenario_c_zero_from_the_start(tmp_path):
    """窓の先頭からゼロ → 直前に >=1 が無いので検出されない。"""
    rows = ap_rows(1, [0] * 10 + [5] * 5)
    out = run(tmp_path, rows)
    assert out.empty
    assert list(out.columns) == list(RESULT_COLUMNS)


# ---------------------------------------------------------------------------
# D: AP 停止
# ---------------------------------------------------------------------------


def test_scenario_d_ap_down(tmp_path):
    """途中で status=disconnected → 打ち切り(AP停止)。回復時刻・直後clients は空。"""
    counts = [5, 5] + [0] * 6 + [0, 0] + [5] * 5
    statuses = ["connected"] * 8 + ["disconnected"] * 2 + ["connected"] * 5
    out = run(tmp_path, ap_rows(1, counts, statuses=statuses))

    assert len(out) == 1
    row = out.iloc[0]
    assert row["回復状況"] == STATUS_CUT_AP_DOWN
    assert row["連続ゼロ回数"] == 6  # 停止した区間は含めない
    assert row["ゼロ終了"] == START + timedelta(seconds=INTERVAL * 7)
    assert out["回復時刻"].isna().all()
    assert out["直後clients（回復時）"].isna().all()


# ---------------------------------------------------------------------------
# E: ギャップ跨ぎ（本タスクで最も重要）
# ---------------------------------------------------------------------------


def test_scenario_e_gap_truncates_and_does_not_resume(tmp_path):
    """ゼロ区間の途中に欠測 → 手前が 打ち切り(欠測) で終わり、向こう側は区間にならない。

    欠測の向こう側の最初のサンプルは「直前サンプルの num_clients >= 1」を満たさない
    （直前は欠測の手前のゼロ）。区間の開始条件はギャップの前後で変わらないので、
    ここから新しい区間は始まらない。連続ゼロ回数が欠測を跨いで過大になることも当然ない。
    """
    counts = [5] + [0] * 21 + [5] * 3
    out = run(tmp_path, ap_rows(1, counts, skip=(11, 12, 13)))

    assert len(out) == 1
    first = out.iloc[0]

    assert first["区間番号"] == 1
    assert first["AP内区間数"] == 1
    assert first["回復状況"] == STATUS_CUT_GAP
    assert first["連続ゼロ回数"] == 10  # index 1〜10。欠測の向こう側は数えない
    assert first["ゼロ終了"] == START + timedelta(seconds=INTERVAL * 10)
    assert pd.isna(first["回復時刻"])
    assert pd.isna(first["直後clients（回復時）"])


def test_scenario_e_zero_on_both_sides_of_the_gap_yields_one_interval(tmp_path):
    """ギャップの前後ともゼロが続くだけなら、2 つ目の区間は検出されないこと。

    ログ収集が断続的な環境で「ずっとゼロなだけの AP」がギャップの数だけ区間として
    量産されるのを防ぐための最重要の回帰テスト。
    """
    # 欠測を 2 回挟んでも、1→0 の遷移は先頭の 1 回しかない
    counts = [5] + [0] * 30 + [5] * 3
    out = run(tmp_path, ap_rows(1, counts, skip=(11, 12, 13, 21, 22, 23)))

    assert len(out) == 1
    assert out.iloc[0]["回復状況"] == STATUS_CUT_GAP
    assert out.iloc[0]["ゼロ開始"] == START + timedelta(seconds=INTERVAL * 1)
    # 直前clients は必ず >= 1（0 の区間が混ざっていないこと）
    assert int(out.iloc[0]["直前clients"]) >= 1


def test_scenario_e_previous_sample_is_always_one_or_more(tmp_path):
    """どの検出区間も「直前clients >= 1」を満たすこと（区間の開始条件そのものの確認）。"""
    counts = [5] + [0] * 21 + [5] * 3
    out = run(tmp_path, ap_rows(1, counts, skip=(11, 12, 13)))

    prev = pd.to_numeric(out["直前clients"], errors="coerce")
    assert not out.empty
    assert (prev >= 1).all(), "直前clients が 1 未満の区間が検出されています"


def test_scenario_e_new_transition_after_the_gap_is_detected(tmp_path):
    """ギャップの向こう側でも 1→0 の遷移が実際にあれば、通常どおり検出されること。

    再開を止めた副作用で「ギャップ以降を一切見なくなる」わけではないことの確認。
    """
    # index 1〜10 ゼロ → 欠測 → index 14〜17 は端末あり → index 18 から再びゼロ
    counts = [5] + [0] * 10 + [0, 0, 0] + [5] * 4 + [0] * 5 + [5] * 3
    out = run(tmp_path, ap_rows(1, counts, skip=(11, 12, 13)))

    assert len(out) == 2
    first, second = out.iloc[0], out.iloc[1]
    assert first["回復状況"] == STATUS_CUT_GAP
    assert first["連続ゼロ回数"] == 10

    assert second["区間番号"] == 2
    assert second["回復状況"] == STATUS_RECOVERED
    assert second["ゼロ開始"] == START + timedelta(seconds=INTERVAL * 18)
    assert second["連続ゼロ回数"] == 5  # index 18〜22
    assert int(second["直前clients"]) == 5


def test_scenario_e_gap_has_missing_samples(tmp_path):
    """E で使うギャップは missing_samples >= 1 の「本物の欠測」であることを確認する。

    missing_samples == 0（ジッタ）はこのテストの対象ではない（下の別テストで検証する）。
    """
    S.write_metrics(
        tmp_path / "ap_metrics.csv",
        ap_rows(1, [5] + [0] * 21 + [5] * 3, skip=(11, 12, 13)),
    )
    res = load(tmp_path)
    assert len(res.gaps) == 1
    assert res.gaps.iloc[0]["missing_samples"] >= 1


def test_scenario_e_jitter_gap_does_not_truncate(tmp_path):
    """missing_samples == 0 のギャップ（ジッタ。1 件も欠けていない）は区間を打ち切らない。

    実測で 300 秒間隔に対し 460 秒のジッタが発生しても、``gap_factor`` を超えるだけで
    サンプル自体は 1 件も欠けていない（missing_samples == 0）。ローダはこれもギャップとして
    報告するが、detector 側は打ち切り対象にしない。
    """
    offsets = [0, 300, 600, 900, 900 + 460, 900 + 460 + 300, 900 + 460 + 600, 900 + 460 + 900,
               900 + 460 + 1200]
    counts = [5, 0, 0, 0, 0, 0, 0, 0, 5]
    rows = [S.metrics_row(START + timedelta(seconds=o), num_clients=c) for o, c in zip(offsets, counts)]

    S.write_metrics(tmp_path / "ap_metrics.csv", rows)
    res = load(tmp_path)
    assert res.gaps.iloc[0]["missing_samples"] == 0  # 前提: ジッタであって欠測ではない

    out = detect(res.metrics, res.events, res.gaps, window_start=None, window_end=None)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["回復状況"] == STATUS_RECOVERED
    assert row["連続ゼロ回数"] == 7  # ジッタを挟んでも 1 区間のまま（分割されない）


def test_scenario_e_without_gaps_merges_and_overcounts(tmp_path):
    """gaps を渡さないと欠測を跨いで連結され、連続ゼロ回数が過大になる。

    このテストは「ギャップ跨ぎ禁止が効いていること」を裏側から確認するためのもの
    （実運用で gaps を省略してはならない）。
    """
    counts = [5] + [0] * 21 + [5] * 3
    out = run(tmp_path, ap_rows(1, counts, skip=(11, 12, 13)), gaps=None)

    assert len(out) == 1
    assert out.iloc[0]["連続ゼロ回数"] == 18  # 10 + 8 が 1 区間に化ける
    assert out.iloc[0]["回復状況"] == STATUS_RECOVERED


# ---------------------------------------------------------------------------
# F: 継続中
# ---------------------------------------------------------------------------


def test_scenario_f_ongoing_is_not_dropped(tmp_path):
    """データ末尾までゼロ → 継続中として出力される（除外されない）。"""
    out = run(tmp_path, ap_rows(1, [5, 5] + [0] * 10))

    assert len(out) == 1
    row = out.iloc[0]
    assert row["回復状況"] == STATUS_ONGOING
    assert row["連続ゼロ回数"] == 10
    assert out["回復時刻"].isna().all()
    assert out["直後clients（回復時）"].isna().all()


# ---------------------------------------------------------------------------
# G: 閾値未満
# ---------------------------------------------------------------------------


def test_scenario_g_below_threshold(tmp_path):
    """ゼロが 4 サンプルだけ → 既定（5）では検出されない。"""
    rows = ap_rows(1, [5] + [0] * 4 + [5] * 5)
    assert run(tmp_path, rows).empty

    # しきい値は設定可能
    loose = run(tmp_path, rows, min_zero_samples=4)
    assert len(loose) == 1
    assert loose.iloc[0]["連続ゼロ回数"] == 4


def test_min_zero_duration_takes_precedence(tmp_path):
    """min_zero_duration を指定したら min_zero_samples より優先される。"""
    rows = ap_rows(1, [5] + [0] * 4 + [5] * 5)  # 4 サンプル = 端から端まで 15 分

    # サンプル数では届かないが、時間では届く
    by_duration = run(tmp_path, rows, min_zero_samples=100, min_zero_duration=timedelta(minutes=15))
    assert len(by_duration) == 1

    # 時間が足りなければ、サンプル数を緩めても採用されない
    strict = run(tmp_path, rows, min_zero_samples=1, min_zero_duration=timedelta(minutes=30))
    assert strict.empty


def test_window_limits_the_scan(tmp_path):
    """窓の外のサンプルは走査しない（窓頭からゼロの区間は開始と見なさない）。"""
    rows = ap_rows(1, [5, 5] + [0] * 10 + [5] * 3)
    late = run(
        tmp_path,
        rows,
        window_start=START + timedelta(seconds=INTERVAL * 3),  # ゼロの途中から
    )
    assert late.empty


# ---------------------------------------------------------------------------
# H: window_start より前の直前サンプル（サンプル自体は絞り込まない）
# ---------------------------------------------------------------------------


def test_scenario_h_previous_sample_outside_window_start(tmp_path):
    """window_start より前に clients>=1 のサンプルがあり、window_start 直後にゼロへ
    落ちて回復する区間 → 検出され、直前clients・ゼロ直前時刻は窓外のサンプルの値になる。

    window_start はサンプル自体を絞り込まない（「ゼロ開始が範囲内か」の判定にのみ使う）ため、
    直前clients を取るサンプルが window_start より前にあってもよい。
    """
    counts = [5, 5, 5] + [0] * 7 + [5] * 3
    # index2（直前サンプル）は窓の外、index3（ゼロ開始）は窓のすぐ内側になるよう置く
    window_start = START + timedelta(seconds=INTERVAL * 3 - 100)
    assert START + timedelta(seconds=INTERVAL * 2) < window_start < START + timedelta(seconds=INTERVAL * 3)

    out = run(tmp_path, ap_rows(1, counts), window_start=window_start)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["回復状況"] == STATUS_RECOVERED
    assert row["連続ゼロ回数"] == 7
    assert row["ゼロ開始"] == START + timedelta(seconds=INTERVAL * 3)
    assert row["ゼロ開始"] >= window_start

    # 直前clients・ゼロ直前時刻は窓の外（index2）のサンプルの値
    assert row["直前clients"] == 5
    assert row["ゼロ直前時刻"] == START + timedelta(seconds=INTERVAL * 2)
    assert row["ゼロ直前時刻"] < window_start
