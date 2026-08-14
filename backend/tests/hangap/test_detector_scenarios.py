"""合成データによるシナリオ A〜G。

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
WINDOW_START = datetime(2026, 1, 1, 9, 0)
WINDOW_END = datetime(2026, 1, 2, 9, 0)


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
    """CSV へ書き出し、ローダを通してから検出する（gaps はローダが作るものを使う）。"""
    S.write_metrics(tmp_path / "ap_metrics.csv", rows)
    res = load(tmp_path)
    return detect(
        res.metrics,
        res.events,
        kwargs.pop("gaps", res.gaps),
        window_start=kwargs.pop("window_start", WINDOW_START),
        window_end=kwargs.pop("window_end", WINDOW_END),
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


def test_scenario_e_gap_splits_the_interval(tmp_path):
    """ゼロ区間の途中に欠測 → 2 区間に分かれ、前半が 打ち切り(欠測)。"""
    counts = [5] + [0] * 21 + [5] * 3
    out = run(tmp_path, ap_rows(1, counts, skip=(11, 12, 13)))

    assert len(out) == 2
    first, second = out.iloc[0], out.iloc[1]

    assert first["区間番号"] == 1
    assert first["AP内区間数"] == 2
    assert first["回復状況"] == STATUS_CUT_GAP
    assert first["連続ゼロ回数"] == 10  # index 1〜10。欠測の向こう側は数えない
    assert first["ゼロ終了"] == START + timedelta(seconds=INTERVAL * 10)
    assert pd.isna(first["回復時刻"])
    assert pd.isna(first["直後clients（回復時）"])

    # 欠測の向こう側は「新しい区間」として採番が続く
    assert second["区間番号"] == 2
    assert second["回復状況"] == STATUS_RECOVERED
    assert second["連続ゼロ回数"] == 8  # index 14〜21
    assert second["ゼロ開始"] == START + timedelta(seconds=INTERVAL * 14)
    assert second["回復時刻"] == START + timedelta(seconds=INTERVAL * 22)

    # 合計しても「跨いだ 1 区間」にはならない
    assert first["連続ゼロ回数"] + second["連続ゼロ回数"] == 18


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
