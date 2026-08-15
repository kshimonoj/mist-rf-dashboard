"""周辺AP判定（距離ベース）のテスト。合成データのみを使う（実データは使わない）。

判定の核は「同じ map_id・距離の昇順・上位N台・距離上限」の 4 点で、これを崩すと
「その場所に人がいたか」という問いに答えられなくなる。RF 隣接は参考列にとどめ、
判定には一切影響させない。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import _synth as S
import pandas as pd
import pytest

from hangap import neighbors
from hangap.detector import CORE_RESULT_COLUMNS, RESULT_COLUMNS, detect
from hangap.loader import load
from pseudonymizer.schemas import AP_METRICS_V1_COLUMNS

START = datetime(2026, 1, 1, 10, 0, 0)
INTERVAL = 60

#: 対象 AP のクライアント数。index 3〜12 の 10 サンプルがゼロ区間になる
TARGET_VALUES: list[int] = [1, 1, 1] + [0] * 10 + [1] * 7

ZERO_START = START + timedelta(seconds=INTERVAL * 3)
ZERO_END = START + timedelta(seconds=INTERVAL * 12)

TARGET = "TARGET-AP"

#: (ap_name, map_id, x_m, y_m)。TARGET は (0,0)。距離は 5/10/15/20/24/30m
LAYOUT: tuple[tuple[str, str, float, float], ...] = (
    (TARGET, "map-a", 0.0, 0.0),
    ("NEAR-05", "map-a", 5.0, 0.0),
    ("NEAR-10", "map-a", 10.0, 0.0),
    ("NEAR-15", "map-a", 0.0, 15.0),
    ("NEAR-20", "map-a", 20.0, 0.0),
    ("NEAR-24", "map-a", 24.0, 0.0),
    ("FAR-30", "map-a", 30.0, 0.0),
    ("OTHERMAP", "map-b", 1.0, 0.0),  # TARGET のすぐ隣に見えるが別マップ
)


def _ap_id(ap_name: str) -> str:
    return f"test-ap-{ap_name.lower()}"


#: AP 名 → 一目で偽物と分かる MAC（コロンなし小文字）。実行ごとに変わらないよう固定する
_MACS: dict[str, str] = {
    name: f"aabbccddee{i:02d}" for i, (name, *_rest) in enumerate(LAYOUT, start=1)
}


def _mac(ap_name: str) -> str:
    return _MACS[ap_name]


def _rows_for(
    ap_name: str,
    map_id: str | None,
    x_m: float | None,
    y_m: float | None,
    values: list[int],
    *,
    offset_seconds: int = 0,
) -> list[dict]:
    """``offset_seconds`` はポーリング位相のずれ（AP ごとに数秒〜数十秒ずれる環境の再現）。"""
    coords: dict[str, object] = {}
    if map_id is not None:
        coords["map_id"] = map_id
    if x_m is not None:
        coords["x_m"] = x_m
    if y_m is not None:
        coords["y_m"] = y_m
    return [
        S.metrics_row(
            START + timedelta(seconds=INTERVAL * i + offset_seconds),
            ap_id=_ap_id(ap_name),
            ap_name=ap_name,
            mac=_mac(ap_name),
            num_clients=v,
            **coords,
        )
        for i, v in enumerate(values)
    ]


def _site_rows(
    *,
    neighbor_clients: int = 5,
    layout: tuple[tuple[str, str, float, float], ...] = LAYOUT,
) -> list[dict]:
    """LAYOUT どおりに配置した AP 群。TARGET だけがゼロ区間を持つ。"""
    rows: list[dict] = []
    for ap_name, map_id, x_m, y_m in layout:
        values = TARGET_VALUES if ap_name == TARGET else [neighbor_clients] * len(TARGET_VALUES)
        rows.extend(_rows_for(ap_name, map_id, x_m, y_m, values))
    return rows


def _detect(tmp_path: Path, rows: list[dict], *, rf_rows: list[dict] | None = None, **kwargs):
    S.write_metrics(tmp_path / "ap_metrics.csv", rows)
    if rf_rows is not None:
        S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", rf_rows)
    res = load(tmp_path)
    return detect(res.metrics, res.events, res.gaps, rf_neighbors=res.rf_neighbors, **kwargs)


def _target_row(df: pd.DataFrame) -> pd.Series:
    hits = df[df["ap_name"] == TARGET]
    assert len(hits) == 1, f"TARGET の区間が 1 件ではありません: {len(hits)}"
    return hits.iloc[0]


def _names(row: pd.Series) -> list[str]:
    text = str(row["周辺AP名"])
    return [p.strip() for p in text.split(", ")] if text else []


# ---------------------------------------------------------------------------
# 1〜4. 近傍の選び方（距離順 / 上位N / 距離上限 / マップ分離）
# ---------------------------------------------------------------------------


def test_neighbors_are_sorted_by_distance(tmp_path):
    """近傍は距離の昇順に並ぶこと（距離列も同じ順序）。"""
    df = _detect(tmp_path, _site_rows())
    row = _target_row(df)

    assert _names(row) == ["NEAR-05", "NEAR-10", "NEAR-15", "NEAR-20"]
    assert str(row["周辺AP距離"]) == "5.0, 10.0, 15.0, 20.0"
    assert int(row["周辺AP数"]) == 4


def test_top_n_excludes_the_fifth_nearest(tmp_path):
    """N=4 のとき、5 台目（24m）は上限内でも含まれないこと。"""
    df = _detect(tmp_path, _site_rows(), neighbor_count=4)
    assert "NEAR-24" not in _names(_target_row(df))

    # N を広げれば入る（除外理由が「上位N」であることの確認）
    df5 = _detect(tmp_path, _site_rows(), neighbor_count=5)
    assert "NEAR-24" in _names(_target_row(df5))


def test_max_distance_excludes_far_ap_even_within_top_n(tmp_path):
    """距離上限を超える AP は、上位N以内でも除外されること。"""
    df = _detect(tmp_path, _site_rows(), neighbor_count=10, max_distance_m=25.0)
    names = _names(_target_row(df))
    assert "NEAR-24" in names  # 上限内
    assert "FAR-30" not in names  # 上限外

    df_wide = _detect(tmp_path, _site_rows(), neighbor_count=10, max_distance_m=40.0)
    assert "FAR-30" in _names(_target_row(df_wide))


def test_different_map_is_never_a_neighbor(tmp_path):
    """座標が近くても map_id が違えば近傍にしないこと（座標系が違い距離を定義できない）。"""
    df = _detect(tmp_path, _site_rows(), neighbor_count=10, max_distance_m=100.0)
    assert "OTHERMAP" not in _names(_target_row(df))


# ---------------------------------------------------------------------------
# 5〜7. 判定不能（近傍なし / 座標なし / ap_metrics_v1）
# ---------------------------------------------------------------------------


def test_no_neighbor_within_limit_is_undecidable(tmp_path):
    """上限内に AP が 1 台も無ければ判定不能。0 台と混同しないこと。"""
    lonely = (
        (TARGET, "map-a", 0.0, 0.0),
        ("NEAR-05", "map-a", 500.0, 0.0),  # 上限のはるか外
    )
    df = _detect(tmp_path, _site_rows(layout=lonely))
    row = _target_row(df)

    assert row["周辺AP判定"] == neighbors.VERDICT_UNKNOWN
    assert pd.isna(row["周辺AP数"])
    assert pd.isna(row["周辺AP端末数合計"])
    assert str(row["周辺AP名"]) == ""


def test_missing_coordinates_are_undecidable(tmp_path):
    """map_id / x_m が無い AP は判定不能になること。"""
    no_coords = (
        (TARGET, None, None, None),
        ("NEAR-05", "map-a", 5.0, 0.0),
    )
    df = _detect(tmp_path, _site_rows(layout=no_coords))
    row = _target_row(df)

    assert row["周辺AP判定"] == neighbors.VERDICT_UNKNOWN
    assert pd.isna(row["周辺AP端末数合計"])


def test_ap_metrics_v1_without_coordinate_columns_is_undecidable(tmp_path):
    """座標列を持たない旧形式（33 列）だけでも、エラーにならず全区間が判定不能になること。"""
    # 座標を持つ配置で作ってから、旧形式の 33 列だけを書き出す（列そのものが存在しない）
    rows = [{k: v for k, v in row.items() if k in AP_METRICS_V1_COLUMNS} for row in _site_rows()]
    S.write_csv(tmp_path / "ap_metrics_v1.csv", AP_METRICS_V1_COLUMNS, rows)
    res = load(tmp_path)
    df = detect(res.metrics, res.events, res.gaps)

    assert len(df) >= 1
    assert set(df["周辺AP判定"]) == {neighbors.VERDICT_UNKNOWN}
    assert df["周辺AP数"].isna().all()
    assert df["周辺AP端末数合計"].isna().all()


# ---------------------------------------------------------------------------
# 8. 区間中の平均
# ---------------------------------------------------------------------------


def test_neighbor_client_mean_uses_only_samples_inside_the_interval(tmp_path):
    """周辺AP端末数は区間中（ゼロ開始〜ゼロ終了）の平均で、区間外を含まないこと。

    探索窓は推定サンプリング間隔の半分だけ広げているが、その幅は間隔の半分未満なので
    **隣のポーリング周期のサンプルには届かない**。区間の直前・直後に 100 を置いても
    平均に混ざらないことで、その性質を確認する。
    """
    # 区間中だけ 10、区間外は 100。単純平均なら 10.0 になる。
    inside = [100] * 3 + [10] * 10 + [100] * 7
    assert len(inside) == len(TARGET_VALUES)

    rows = _rows_for(TARGET, "map-a", 0.0, 0.0, TARGET_VALUES)
    rows += _rows_for("NEAR-05", "map-a", 5.0, 0.0, inside)
    df = _detect(tmp_path, rows)
    row = _target_row(df)

    assert str(row["周辺AP端末数"]) == "10.0"
    assert float(row["周辺AP端末数合計"]) == pytest.approx(10.0)
    assert int(row["周辺AP実測なし数"]) == 0


def test_phase_shifted_neighbor_is_still_measured(tmp_path):
    """位相がずれた近傍AP でも、許容幅（間隔の半分）の中なら実測値を拾えること。

    AP ごとにポーリング位相がずれる環境では、短い区間だと近傍のサンプルが区間の
    外へこぼれる。代用値で埋めるのではなく、探索窓を広げて **実測のまま** 拾う。
    """
    # 1 サンプルだけのゼロ区間（ゼロ開始 == ゼロ終了 == START+60s）
    target = [1, 0, 1, 1, 1, 1]
    # 近傍は +20 秒ずれ（間隔 60 秒の半分 30 秒より内側）。t=80s のサンプルだけが窓に入る
    shifted = [100, 7, 100, 100, 100, 100]

    rows = _rows_for(TARGET, "map-a", 0.0, 0.0, target)
    rows += _rows_for("NEAR-05", "map-a", 5.0, 0.0, shifted, offset_seconds=20)
    df = _detect(tmp_path, rows, min_zero_samples=1)
    row = _target_row(df)

    assert row["ゼロ開始"] == row["ゼロ終了"]  # 前提: 区間長 0 のケース
    assert str(row["周辺AP端末数"]) == "7.0"  # 実測値そのもの（代用値ではない）
    assert int(row["周辺AP実測なし数"]) == 0


def test_neighbor_without_samples_is_reported_as_not_measured(tmp_path):
    """区間中に実測が無い近傍は「実測なし」と明示し、合計にも含めないこと。

    推定値で埋めると、判定根拠を確かめるための表示に実測でない値が実測と同じ見た目で
    混ざる。0 として足すのも「周辺に人がいなかった」と誤読させるため避ける。
    """
    rows = _rows_for(TARGET, "map-a", 0.0, 0.0, TARGET_VALUES)
    rows += _rows_for("NEAR-05", "map-a", 5.0, 0.0, [6] * len(TARGET_VALUES))
    # この近傍は区間よりずっと後ろにしかサンプルを持たない（許容幅でも届かない）
    rows += _rows_for(
        "NEAR-10", "map-a", 10.0, 0.0, [50] * len(TARGET_VALUES),
        offset_seconds=INTERVAL * 100,
    )
    df = _detect(tmp_path, rows)
    row = _target_row(df)

    assert _names(row) == ["NEAR-05", "NEAR-10"]
    assert str(row["周辺AP端末数"]) == f"6.0, {neighbors.NO_MEASUREMENT}"
    assert int(row["周辺AP実測なし数"]) == 1
    # 合計は実測できた 1 台分だけ（実測なしを 0 として足していない）
    assert float(row["周辺AP端末数合計"]) == pytest.approx(6.0)


def test_explain_marks_not_measured_neighbors(tmp_path):
    """explain でも「実測なし」が値と区別して見えること。"""
    rows = _rows_for(TARGET, "map-a", 0.0, 0.0, TARGET_VALUES)
    rows += _rows_for("NEAR-05", "map-a", 5.0, 0.0, [6] * len(TARGET_VALUES))
    rows += _rows_for(
        "NEAR-10", "map-a", 10.0, 0.0, [50] * len(TARGET_VALUES),
        offset_seconds=INTERVAL * 100,
    )
    S.write_metrics(tmp_path / "ap_metrics.csv", rows)
    res = load(tmp_path)
    ctx = neighbors.build_context(res.metrics, res.rf_neighbors)
    df = detect(res.metrics, res.events, res.gaps, neighbor_context=ctx)

    text = neighbors.render_explain(df, [TARGET], ctx)
    assert neighbors.NO_MEASUREMENT in text
    assert "1 台は実測なし。合計には含めていない" in text


def test_interval_mean_is_the_average_of_varying_samples(tmp_path):
    """区間中に値が変わる場合は、その平均になること。"""
    varying = [999] * 3 + ([0] * 5 + [20] * 5) + [999] * 7  # 区間中の平均 = 10.0
    rows = _rows_for(TARGET, "map-a", 0.0, 0.0, TARGET_VALUES)
    rows += _rows_for("NEAR-05", "map-a", 5.0, 0.0, varying)
    df = _detect(tmp_path, rows)

    assert float(_target_row(df)["周辺AP端末数合計"]) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 9〜10. 判定の分岐 / フィルタしないこと
# ---------------------------------------------------------------------------


def test_verdict_switches_around_the_threshold(tmp_path):
    """しきい値の前後で「周辺に端末あり」「周辺も端末なし」が切り替わること。"""
    # 近傍 4 台がすべて 1 台ずつ → 合計 4.0
    rows = _site_rows(neighbor_clients=1)

    below = _detect(tmp_path, rows, neighbor_client_threshold=4.5)
    assert _target_row(below)["周辺AP判定"] == neighbors.VERDICT_ABSENT

    at = _detect(tmp_path, rows, neighbor_client_threshold=4.0)
    assert _target_row(at)["周辺AP判定"] == neighbors.VERDICT_PRESENT  # 「以上」で判定する

    above = _detect(tmp_path, rows, neighbor_client_threshold=3.5)
    assert _target_row(above)["周辺AP判定"] == neighbors.VERDICT_PRESENT


def test_absent_verdict_rows_are_not_filtered_out(tmp_path):
    """「周辺も端末なし」の区間が結果から除外されないこと（絞り込みは利用者の責務）。"""
    df = _detect(tmp_path, _site_rows(neighbor_clients=0))
    row = _target_row(df)

    assert row["周辺AP判定"] == neighbors.VERDICT_ABSENT
    assert float(row["周辺AP端末数合計"]) == pytest.approx(0.0)
    assert int(row["連続ゼロ回数"]) == 10  # 行そのものは通常どおり出ている


# ---------------------------------------------------------------------------
# 11〜12. RF 隣接（参考列であって判定材料ではない）
# ---------------------------------------------------------------------------


def _rf_rows() -> list[dict]:
    """TARGET ⇄ NEAR-05 / TARGET → NEAR-15 の 2 台分だけを隣接として記録する。"""
    ts = START
    return [
        S.rf_neighbor_row(ts, _mac(TARGET), _mac("NEAR-05"), rssi=-60.0),
        S.rf_neighbor_row(ts, _mac("NEAR-05"), _mac(TARGET), rssi=-62.0),
        S.rf_neighbor_row(ts, _mac(TARGET), _mac("NEAR-15"), rssi=-70.0),
        # 近傍に選ばれない AP との隣接（数に入らないこと）
        S.rf_neighbor_row(ts, _mac(TARGET), _mac("FAR-30"), rssi=-80.0),
    ]


def test_rf_reference_column_counts_only_distance_neighbors(tmp_path):
    """rf_neighbors がある場合、距離で選んだ近傍のうち RF 隣接にも現れる台数が入ること。"""
    df = _detect(tmp_path, _site_rows(), rf_rows=_rf_rows())
    row = _target_row(df)

    assert int(row["周辺AP RF隣接数"]) == 2  # NEAR-05 と NEAR-15（FAR-30 は近傍でない）
    assert int(row["周辺AP数"]) == 4


def test_rf_reference_column_is_blank_without_rf_neighbors(tmp_path):
    """rf_neighbors が無ければ参考列は空。エラーにはしないこと。"""
    df = _detect(tmp_path, _site_rows())
    assert pd.isna(_target_row(df)["周辺AP RF隣接数"])


def test_rf_neighbors_do_not_change_the_verdict(tmp_path, tmp_path_factory):
    """rf_neighbors の有無で周辺AP判定・近傍AP・端末数合計が変わらないこと。"""
    without_dir = tmp_path_factory.mktemp("without_rf")
    with_dir = tmp_path_factory.mktemp("with_rf")

    without = _target_row(_detect(without_dir, _site_rows()))
    withrf = _target_row(_detect(with_dir, _site_rows(), rf_rows=_rf_rows()))

    for col in ("周辺AP判定", "周辺AP名", "周辺AP距離", "周辺AP端末数"):
        assert str(without[col]) == str(withrf[col]), f"{col} が rf_neighbors の有無で変わりました"
    assert float(without["周辺AP端末数合計"]) == float(withrf["周辺AP端末数合計"])
    assert int(without["周辺AP数"]) == int(withrf["周辺AP数"])


# ---------------------------------------------------------------------------
# 13. 既存列の不変
# ---------------------------------------------------------------------------


def test_core_columns_are_unchanged(tmp_path):
    """既存 22 列の名前と順序が変わっていないこと（周辺AP列は末尾に足すだけ）。"""
    expected = (
        "ap_name", "site_name", "区間番号", "AP内区間数", "ゼロ直前時刻", "直前clients",
        "直後clients（回復時）", "ゼロ開始", "ゼロ終了", "連続ゼロ回数", "回復状況", "回復時刻",
        "AP最大clients", "AP Event（±30分）", "Event時刻", "ゼロ終了との差(分)", "Event種別",
        "Event詳細", "サイト合計clients(ゼロ開始時)", "サイト合計clients(ゼロ終了時)",
        "サイト全体変化率", "退場疑い",
    )
    assert CORE_RESULT_COLUMNS == expected
    assert RESULT_COLUMNS[: len(expected)] == expected
    assert RESULT_COLUMNS[len(expected):] == neighbors.NEIGHBOR_COLUMNS

    df = _detect(tmp_path, _site_rows())
    assert list(df.columns) == list(RESULT_COLUMNS)


# ---------------------------------------------------------------------------
# 14. explain
# ---------------------------------------------------------------------------


def test_render_explain_shows_intervals_of_the_requested_ap(tmp_path):
    S.write_metrics(tmp_path / "ap_metrics.csv", _site_rows())
    res = load(tmp_path)
    ctx = neighbors.build_context(res.metrics, res.rf_neighbors)
    df = detect(res.metrics, res.events, res.gaps, neighbor_context=ctx)

    text = neighbors.render_explain(df, [TARGET], ctx)
    assert f"判定根拠: {TARGET}" in text
    assert "区間 #1" in text
    assert "近傍AP（距離 <= 25.0m / 上位 4 台）" in text
    assert "NEAR-05" in text
    assert "周辺AP端末数合計" in text
    assert "サイト全体" in text


def test_render_explain_for_unknown_ap_does_not_raise(tmp_path):
    S.write_metrics(tmp_path / "ap_metrics.csv", _site_rows())
    res = load(tmp_path)
    df = detect(res.metrics, res.events, res.gaps)

    text = neighbors.render_explain(df, ["NO-SUCH-AP"])
    assert "該当する区間がありません" in text


def test_render_explain_reports_why_it_is_undecidable(tmp_path):
    """近傍なしと座標なしを、explain では区別して説明すること。"""
    lonely = (
        (TARGET, "map-a", 0.0, 0.0),
        ("NEAR-05", "map-a", 500.0, 0.0),
    )
    S.write_metrics(tmp_path / "ap_metrics.csv", _site_rows(layout=lonely))
    res = load(tmp_path)
    ctx = neighbors.build_context(res.metrics, res.rf_neighbors)
    df = detect(res.metrics, res.events, res.gaps, neighbor_context=ctx)

    text = neighbors.render_explain(df, [TARGET], ctx)
    assert "判定不能" in text and "25.0m 以内に AP なし" in text
