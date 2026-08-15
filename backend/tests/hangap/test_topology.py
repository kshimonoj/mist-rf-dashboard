"""指示 10 パート B: topology-report（RF 隣接 × 距離隣接の比較）。

期待値はすべてこのファイル内で手計算できる合成データから導く。実データは使わない。

シナリオ（1 サイト・1 マップ・5 台。座標は一直線に 10m 間隔）:

    A(0,0)  B(10,0)  C(20,0)  D(30,0)  E(40,0)

RF 隣接（方向つき。距離とはわざと食い違わせている）:

    A → B(-50) C(-60) D(-70) E(-80)
    B → A(-52) C(-55)
    C → A(-61)
    D → A(-71)
    E → A(-81)
"""
from __future__ import annotations

from datetime import datetime

import _synth as S
import pytest

from hangap import cli, topology
from hangap.loader import load

TS = datetime(2026, 1, 1, 4, 30, 0)
MAP1 = "test-map-0001"
MAP2 = "test-map-0002"

A = "aabbccddee01"
B = "aabbccddee02"
C = "aabbccddee03"
D = "aabbccddee04"
E = "aabbccddee05"
OUTSIDE = "aabbccddeeff"

COORDS: dict[str, tuple[float, float]] = {
    A: (0.0, 0.0), B: (10.0, 0.0), C: (20.0, 0.0), D: (30.0, 0.0), E: (40.0, 0.0),
}

RF_LINKS: list[tuple[str, str, float]] = [
    (A, B, -50.0), (A, C, -60.0), (A, D, -70.0), (A, E, -80.0),
    (B, A, -52.0), (B, C, -55.0),
    (C, A, -61.0),
    (D, A, -71.0),
    (E, A, -81.0),
]


def _metrics_rows(coords: dict[str, tuple[float, float]], map_ids: dict[str, str] | None = None):
    rows = []
    for i, (mac, (x, y)) in enumerate(sorted(coords.items()), start=1):
        rows.append(S.metrics_row(
            TS,
            ap_id=f"test-ap-{i:04d}",
            ap_name=f"TEST-AP-{i:02d}",
            mac=mac,
            map_id=(map_ids or {}).get(mac, MAP1),
            x_m=x,
            y_m=y,
        ))
    return rows


def _write_scenario(tmp_path, *, links=None, coords=None, map_ids=None):
    S.write_metrics(tmp_path / "ap_metrics.csv", _metrics_rows(coords or COORDS, map_ids))
    S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", [
        S.rf_neighbor_row(TS, ap, nb, rssi) for ap, nb, rssi in (links or RF_LINKS)
    ])
    return tmp_path


def _analyze(tmp_path, *, band="5", top_n=(2,), **kwargs):
    _write_scenario(tmp_path, **kwargs)
    result = load(tmp_path)
    return topology.analyze(result.metrics, result.rf_neighbors, band=band, top_n=top_n)


def _site(res) -> topology.SiteTopology:
    assert len(res.sites) == 1
    return res.sites[0]


def _approx(value, expected):
    assert value == pytest.approx(expected), f"{value} != {expected}"


# ---------------------------------------------------------------------------
# 1. RF 隣接の広さ
# ---------------------------------------------------------------------------


def test_neighbor_count_and_rssi_distribution(tmp_path):
    s = _site(_analyze(tmp_path))
    # 隣接数: A=4, B=2, C=1, D=1, E=1
    _approx(s.neighbors_median, 1.0)
    _approx(s.neighbors_mean, 1.8)
    _approx(s.neighbors_max, 4.0)
    _approx(s.neighbors_min, 1.0)
    # サイト内 AP 数 5 → 分母は 4
    _approx(s.density_ratio, 1.8 / 4)
    assert s.site_ap_count == 5
    assert s.observer_count == 5

    # RSSI 9 件: -81,-80,-71,-70,-61,-60,-55,-52,-50
    _approx(s.rssi_min, -81.0)
    _approx(s.rssi_q1, -71.0)
    _approx(s.rssi_median, -61.0)
    _approx(s.rssi_q3, -55.0)
    _approx(s.rssi_max, -50.0)


# ---------------------------------------------------------------------------
# 8. 距離計算（マップをまたぐ組は算出しない）
# ---------------------------------------------------------------------------


def test_distance_is_not_computed_across_maps(tmp_path):
    """別 map_id の AP 同士の距離は算出しないこと（座標系が違うため）。"""
    res = _analyze(tmp_path, map_ids={C: MAP2, D: MAP2, E: MAP2})
    detail = res.detail.set_index("ap_mac")
    # map1 は A, B の 2 台のみ → 互いに 1 台だけが距離候補
    assert detail.loc[A, "same_map_ap_count"] == 1
    assert detail.loc[B, "same_map_ap_count"] == 1
    # map2 は C, D, E の 3 台
    assert detail.loc[C, "same_map_ap_count"] == 2
    # A から見て C/D/E は別マップなので距離を出せない
    assert detail.loc[A, "rf_neighbor_other_map"] == 3
    _approx(detail.loc[A, "dist_top2_max_m"], 10.0)


def test_distance_helper_returns_none_for_other_map():
    a = {"map_id": MAP1, "x_m": 0.0, "y_m": 0.0}
    b = {"map_id": MAP2, "x_m": 0.0, "y_m": 0.0}
    assert topology._distance(a, b) is None
    assert topology._distance(a, {"map_id": MAP1, "x_m": 3.0, "y_m": 4.0}) == pytest.approx(5.0)
    assert topology._distance(a, {"map_id": "", "x_m": 1.0, "y_m": 1.0}) is None
    assert topology._distance(a, {"map_id": MAP1, "x_m": None, "y_m": None}) is None


# ---------------------------------------------------------------------------
# 9. 重なり割合（手計算値との一致）
# ---------------------------------------------------------------------------


def test_overlap_ratios_match_hand_calculation(tmp_path):
    """N=2 のとき、重なり割合が手計算値と一致すること。

    距離上位2台: A={B,C} B={A,C} C={B,D} D={C,E} E={D,C}
    RF 隣接    : A={B,C,D,E} B={A,C} C={A} D={A} E={A}

    距離上位2 ∩ RF / 2 = 1.0, 1.0, 0.0, 0.0, 0.0 → 平均 0.4
    RF ∩ 距離上位2 / |RF| = 0.5, 1.0, 0.0, 0.0, 0.0 → 平均 0.3
    """
    s = _site(_analyze(tmp_path, top_n=(2,)))
    t = s.top_n[0]
    assert t.n == 2
    assert t.aps == 5
    _approx(t.dist_in_rf, 0.4)
    _approx(t.rf_in_dist, 0.3)


def test_top_n_agreement_and_distance_match_hand_calculation(tmp_path):
    """RSSI 上位2台と距離上位2台の一致率、およびそのときの平均距離。

    RSSI 上位2 : A={B,C} B={A,C} C={A} D={A} E={A}
    距離上位2  : A={B,C} B={A,C} C={B,D} D={C,E} E={D,C}
    一致率     : 1.0, 1.0, 0.0, 0.0, 0.0 → 平均 0.4
    RSSI 上位2 の平均距離: A=15, B=10, C=20, D=30, E=40 → 平均 23.0
    距離上位2 の平均距離 : A=15, B=10, C=10, D=10, E=15 → 平均 12.0
    距離上位2 の距離すべて: 10 が 8 件, 20 が 2 件 → 中央値 10, 最大 20
    """
    t = _site(_analyze(tmp_path, top_n=(2,))).top_n[0]
    _approx(t.rssi_top_match, 0.4)
    _approx(t.rssi_top_mean_dist_m, 23.0)
    _approx(t.dist_top_mean_dist_m, 12.0)
    _approx(t.dist_median_m, 10.0)
    _approx(t.dist_max_m, 20.0)


def test_multiple_top_n_are_all_computed(tmp_path):
    """N ごとに独立して算出されること。N=4 では A の距離上位4台が RF 隣接と完全一致する。"""
    res = _analyze(tmp_path, top_n=(2, 4))
    s = _site(res)
    assert [t.n for t in s.top_n] == [2, 4]
    detail = res.detail.set_index("ap_mac")
    _approx(detail.loc[A, "dist_top4_in_rf"], 1.0)   # {B,C,D,E} は全部 RF 隣接
    _approx(detail.loc[A, "rf_in_dist_top4"], 1.0)
    _approx(detail.loc[D, "dist_top4_in_rf"], 0.25)  # {C,E,B,A} のうち RF 隣接は A のみ


# ---------------------------------------------------------------------------
# 5. 非対称性
# ---------------------------------------------------------------------------


def test_asymmetry_stats(tmp_path):
    """無向ペア 6 組のうち双方向は A-B / A-C / A-D / A-E の 4 組（B→C は片方向）。

    方向差: A-B=|-50-(-52)|=2, A-C=1, A-D=1, A-E=1 → 中央値 1.0 / 最大 2.0
    """
    s = _site(_analyze(tmp_path))
    assert s.pair_count == 5
    _approx(s.bidirectional_ratio, 4 / 5)
    _approx(s.direction_diff_median_db, 1.0)
    _approx(s.direction_diff_max_db, 2.0)


# ---------------------------------------------------------------------------
# 6. データ品質
# ---------------------------------------------------------------------------


def test_neighbor_outside_ap_metrics_is_counted(tmp_path):
    """ap_metrics に存在しない neighbor_mac（サイト外 AP）を件数として出せること。"""
    links = RF_LINKS + [(A, OUTSIDE, -85.0)]
    s = _site(_analyze(tmp_path, links=links))
    assert s.unknown_neighbor_macs == 1
    assert s.unknown_neighbor_rows == 1


def test_ap_without_coordinates_is_counted(tmp_path):
    """マップ未配置（座標なし）の AP を件数として出せること。"""
    res = _analyze(tmp_path, map_ids={E: ""})
    s = _site(res)
    assert s.aps_with_coords == 4
    assert s.aps_without_coords == 1
    assert not res.detail.set_index("ap_mac").loc[E, "has_coords"]


# ---------------------------------------------------------------------------
# 7. マップまたぎの RF 隣接
# ---------------------------------------------------------------------------


def test_cross_map_ratio_is_zero_on_a_single_map(tmp_path):
    """全 AP が同一マップなら、またぎは 0 でマップ凡例は 1 面だけになること。"""
    s = _site(_analyze(tmp_path))
    assert s.link_count == 9          # 方向つきリンク総数
    assert s.same_map_links == 9
    assert s.cross_map_links == 0
    _approx(s.cross_map_ratio, 0.0)
    _approx(s.undistanceable_ratio, 0.0)
    assert s.map_cross_pairs == []
    assert [(m.label, m.ap_count, m.out_links) for m in s.map_infos] == [("M1", 5, 9)]


def test_cross_map_ratio_counts_directed_links(tmp_path):
    """観測側と被観測側が別 map_id のリンクを、方向つきで数えられること。

    A,B が MAP1 / C,D,E が MAP2。またぎは
    A->C, A->D, A->E, B->C（MAP1 発 4 本）と C->A, D->A, E->A（MAP2 発 3 本）の計 7 本。
    同一マップは A->B, B->A の 2 本。
    """
    res = _analyze(tmp_path, map_ids={C: MAP2, D: MAP2, E: MAP2})
    s = _site(res)
    assert s.link_count == 9
    assert s.cross_map_links == 7
    assert s.same_map_links == 2
    assert s.unknown_map_links == 0
    _approx(s.cross_map_ratio, 7 / 9)
    # 距離を出せないのはまたぎの 7 本だけ（座標欠落なし）
    _approx(s.undistanceable_ratio, 7 / 9)

    # AP 数の多いマップから M1 → MAP2 が M1、MAP1 が M2
    labels = {m.map_id: m.label for m in s.map_infos}
    assert labels == {MAP2: "M1", MAP1: "M2"}
    assert len(s.map_cross_pairs) == 1
    p = s.map_cross_pairs[0]
    assert (p.label_a, p.label_b) == ("M1", "M2")
    assert (p.links, p.a_to_b, p.b_to_a) == (7, 3, 4)

    # マップ別のまたぎ率: MAP1 発は 6 本中 4 本、MAP2 発は 3 本すべて
    by_label = {m.label: m for m in s.map_infos}
    _approx(by_label["M2"].cross_ratio, 4 / 6)
    _approx(by_label["M1"].cross_ratio, 1.0)

    # AP 明細にもまたぎ本数が出る（A は B 以外の 3 台がまたぎ）
    detail = res.detail.set_index("ap_mac")
    assert detail.loc[A, "rf_neighbor_cross_map"] == 3
    _approx(detail.loc[A, "rf_neighbor_cross_map_ratio"], 3 / 4)
    assert detail.loc[C, "rf_neighbor_cross_map"] == 1


def test_cross_map_excludes_links_with_unknown_map(tmp_path):
    """map_id が不明な相手（ap_metrics 外・マップ未配置）はまたぎに数えないこと。"""
    links = RF_LINKS + [(A, OUTSIDE, -85.0)]
    s = _site(_analyze(tmp_path, links=links, map_ids={E: ""}))
    # A->OUTSIDE（ap_metrics 外）, A->E / E->A（マップ未配置）の 3 本が「不明」
    assert s.link_count == 10
    assert s.unknown_map_links == 3
    assert s.cross_map_links == 0
    assert s.same_map_links == 7
    # 距離を出せないのは不明の 3 本
    _approx(s.undistanceable_ratio, 3 / 10)


def test_cross_map_stats_are_rendered(tmp_path):
    res = _analyze(tmp_path, map_ids={C: MAP2, D: MAP2, E: MAP2})
    text = res.render()
    assert "7. マップまたぎの RF 隣接" in text
    assert "M1 <-> M2" in text
    assert MAP2 in text


def test_used_timestamp_is_the_latest_snapshot(tmp_path):
    later = datetime(2026, 1, 2, 4, 30, 0)
    S.write_metrics(tmp_path / "ap_metrics.csv", _metrics_rows(COORDS))
    S.write_rf_neighbors(tmp_path / "day1.csv", [S.rf_neighbor_row(TS, A, B, -58.0)])
    S.write_rf_neighbors(tmp_path / "day2.csv", [
        S.rf_neighbor_row(later, A, B, -50.0),
        S.rf_neighbor_row(later, B, A, -51.0),
    ])
    result = load(tmp_path)
    res = topology.analyze(result.metrics, result.rf_neighbors, band="5", top_n=(2,))
    assert res.used_timestamp.to_pydatetime() == later
    assert len(res.snapshots) == 2
    # 最新時点だけを使うので、観測側 AP は 2 台
    assert _site(res).observer_count == 2


# ---------------------------------------------------------------------------
# バンド指定
# ---------------------------------------------------------------------------


def test_band_filter_selects_only_the_requested_band(tmp_path):
    S.write_metrics(tmp_path / "ap_metrics.csv", _metrics_rows(COORDS))
    S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", [
        S.rf_neighbor_row(TS, A, B, -50.0, band="5"),
        S.rf_neighbor_row(TS, A, C, -40.0, band="24"),
        S.rf_neighbor_row(TS, B, A, -52.0, band="5"),
    ])
    result = load(tmp_path)
    res5 = topology.analyze(result.metrics, result.rf_neighbors, band="5", top_n=(2,))
    assert _site(res5).pair_count == 1
    res24 = topology.analyze(result.metrics, result.rf_neighbors, band="24", top_n=(2,))
    assert _site(res24).pair_count == 1
    _approx(_site(res24).rssi_max, -40.0)


def test_missing_band_produces_a_warning_not_an_error(tmp_path):
    S.write_metrics(tmp_path / "ap_metrics.csv", _metrics_rows(COORDS))
    S.write_rf_neighbors(tmp_path / "rf_neighbors.csv", [
        S.rf_neighbor_row(TS, A, B, -50.0, band="5"),
    ])
    result = load(tmp_path)
    res = topology.analyze(result.metrics, result.rf_neighbors, band="6", top_n=(2,))
    assert res.sites == []
    assert any("rf_neighbors が 1 件もありません" in w for w in res.warnings)
    assert "topology-report" in res.render()


# ---------------------------------------------------------------------------
# 10. 少数 AP
# ---------------------------------------------------------------------------


def test_small_sample_warns_but_still_reports(tmp_path):
    """AP 数 4 の入力で警告が出つつ、算出できる値はすべて出ること。"""
    coords = {A: (0.0, 0.0), B: (10.0, 0.0), C: (20.0, 0.0), D: (30.0, 0.0)}
    links = [
        (A, B, -50.0), (A, C, -60.0), (A, D, -70.0),
        (B, A, -52.0), (B, C, -55.0), (B, D, -65.0),
        (C, A, -61.0), (C, B, -54.0), (C, D, -58.0),
        (D, A, -71.0), (D, B, -66.0), (D, C, -59.0),
    ]
    res = _analyze(tmp_path, coords=coords, links=links, top_n=(2,))
    s = _site(res)
    assert s.site_ap_count == 4
    assert s.is_small_sample
    assert res.has_small_sample
    assert any("評価できません" in w for w in res.warnings)
    assert "サンプル数が少なく、RF隣接の広がりを評価できません" in res.render()
    # 完全グラフ（全 AP が互いに隣接）でも値は出る
    _approx(s.density_ratio, 1.0)
    _approx(s.bidirectional_ratio, 1.0)
    assert s.top_n[0].dist_in_rf == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_writes_txt_and_csv(tmp_path, capsys):
    indir = _write_scenario(tmp_path / "in")
    out = tmp_path / "out"
    rc = cli.main(["topology-report", str(indir), "--out", str(out), "--band", "5",
                   "--top-n", "2,4"])
    assert rc == cli.EXIT_OK

    txts = list(out.glob("topology_report_*.txt"))
    csvs = list(out.glob("topology_report_*.csv"))
    assert len(txts) == 1 and len(csvs) == 1

    text = txts[0].read_text(encoding="utf-8")
    assert "RF 隣接 × 距離隣接 比較レポート" in text
    assert text in capsys.readouterr().out

    header = csvs[0].read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert "dist_top2_in_rf" in header
    assert "rf_in_dist_top4" in header
    assert "rssi_top4_mean_dist_m" in header


def test_cli_rejects_bad_top_n(tmp_path):
    indir = _write_scenario(tmp_path / "in")
    out = tmp_path / "out"
    assert cli.main(["topology-report", str(indir), "--out", str(out),
                     "--top-n", "abc"]) == cli.EXIT_INPUT_ERROR
    assert cli.main(["topology-report", str(indir), "--out", str(out),
                     "--top-n", "0"]) == cli.EXIT_INPUT_ERROR


def test_cli_out_same_as_input_is_error(tmp_path):
    indir = _write_scenario(tmp_path / "in")
    assert cli.main(["topology-report", str(indir), "--out", str(indir)]) == cli.EXIT_OUTPUT_ERROR


def test_cli_without_rf_neighbors_still_completes(tmp_path):
    """rf_neighbors が無くてもエラーにせず、警告つきでレポートを出すこと。"""
    indir = tmp_path / "in"
    S.write_metrics(indir / "ap_metrics.csv", _metrics_rows(COORDS))
    out = tmp_path / "out"
    assert cli.main(["topology-report", str(indir), "--out", str(out)]) == cli.EXIT_OK
    assert len(list(out.glob("topology_report_*.csv"))) == 1
