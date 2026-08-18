"""フロア名の解決（floorpeak.floors）。合成データのみを使う。

この分析の肝は「**全無線が停止していて floormap の ap_list に現れない AP も、
map_id 経由で正しいフロアに載る**」こと。落ちたら仕様が壊れている。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import _fpsynth as S
import pandas as pd
from pseudonymizer.schemas import AP_METRICS_V1_COLUMNS

from floorpeak import analysis, floors, loader

START = datetime(2026, 1, 1, 10, 0, 0)
INTERVAL = 300


def _ap(i: int, **kwargs) -> dict:
    base = {"ap_id": f"test-ap-{i:04d}", "ap_name": f"TEST-AP-{i:02d}", "mac": f"aabbccddee{i:02d}"}
    base.update(kwargs)
    return base


def _write_v1_metrics(path: Path, rows) -> Path:
    """座標列を追加する前の 33 列版 ap_metrics（``ap_metrics_v1``）を書く。

    ``S.metrics_row`` は 36 列版のテンプレートを返すので、map_id / x_m / y_m を
    落として 33 列に絞ってから書く（ヘッダーの完全一致で種別判定されるため）。
    """
    trimmed = [{k: v for k, v in r.items() if k in AP_METRICS_V1_COLUMNS} for r in rows]
    return S.write_csv(path, AP_METRICS_V1_COLUMNS, trimmed)


def _analyze(tmp_path: Path, rows) -> analysis.AnalysisResult:
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_TZT.csv", rows)
    return analysis.run_analysis(
        loader.collect_files(tmp_path), analysis.AnalysisParams(site=S.SITE_ID)
    )


def _floor_of(res: analysis.AnalysisResult, ap_name: str) -> str:
    hit = res.rows[res.rows["ap_name"] == ap_name]
    assert len(hit) == 1, f"{ap_name} の行が {len(hit)} 件あります"
    return str(hit.iloc[0]["map_name"])


# ---------------------------------------------------------------------------
# 4. ap_list に現れない AP（全無線停止）が map_id 経由でフロアに載る ← この機能の肝
# ---------------------------------------------------------------------------


def test_ap_absent_from_ap_list_is_placed_by_map_id(tmp_path):
    rows = [
        S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1)),
        # 全無線停止。floormap の ap_list にはどのバンドの行にも出てこない
        S.metrics_row(START, num_clients=0, status="disconnected", map_id=S.MAP_1F, **_ap(2)),
    ]
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36, ap_list=["TEST-AP-01"]),
    ])
    res = _analyze(tmp_path, rows)

    assert _floor_of(res, "TEST-AP-01") == S.FLOOR_1F
    assert _floor_of(res, "TEST-AP-02") == S.FLOOR_1F, "無線停止 AP が未割当に落ちている"
    assert res.meta["floor_count"] == 1
    assert not any(floors.UNASSIGNED in w for w in res.warnings)


def test_bands_are_not_filtered(tmp_path):
    """2.4G の行にしか出てこない AP も同じフロアに載る（band で絞らない）。"""
    rows = [
        S.metrics_row(START, num_clients=3, map_id=S.MAP_1F, **_ap(1)),
        S.metrics_row(START, num_clients=1, map_id=S.MAP_2F, **_ap(2)),
    ]
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36, ap_list=["TEST-AP-01"]),
        S.floormap_row(START, map_name=S.FLOOR_2F, band="24", channel=1, ap_list=["TEST-AP-02"]),
    ])
    res = _analyze(tmp_path, rows)

    assert _floor_of(res, "TEST-AP-01") == S.FLOOR_1F
    assert _floor_of(res, "TEST-AP-02") == S.FLOOR_2F


# ---------------------------------------------------------------------------
# 4b. map_id を持たない AP（33 列版 ap_metrics）は ap_name 経由で解決する
# ---------------------------------------------------------------------------


def test_v1_ap_metrics_falls_back_to_ap_name(tmp_path):
    """33 列版 ap_metrics（map_id 列が無い）でも ap_name 経由で全 AP が解決すること。"""
    rows = [
        S.metrics_row(START, num_clients=5, **_ap(1)),
        S.metrics_row(START, num_clients=3, **_ap(2)),
    ]
    _write_v1_metrics(tmp_path / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36,
                       ap_list=["TEST-AP-01", "TEST-AP-02"]),
    ])
    res = analysis.run_analysis(
        loader.collect_files(tmp_path), analysis.AnalysisParams(site=S.SITE_ID)
    )

    assert _floor_of(res, "TEST-AP-01") == S.FLOOR_1F
    assert _floor_of(res, "TEST-AP-02") == S.FLOOR_1F
    assert not any(floors.UNASSIGNED in w for w in res.warnings)
    # 異常ではなく正常動作なので、警告ではなく注記に出る
    assert any(
        "floormap_summary の AP 名からフロアを特定しました" in n for n in res.floors.notes
    )
    assert res.meta["floor_resolution_notes"]


def test_mixed_v1_and_v2_ap_metrics_both_resolve(tmp_path):
    """map_id があるAPと無いAPが混在しても、両方が正しいフロアに載ること。"""
    v2_rows = [S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1))]
    v1_rows = [S.metrics_row(START, num_clients=3, **_ap(2))]
    S.write_metrics(tmp_path / "ap_metrics_20260101_1000_TZT_v2.csv", v2_rows)
    _write_v1_metrics(tmp_path / "ap_metrics_20260101_1000_TZT_v1.csv", v1_rows)
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36,
                       ap_list=["TEST-AP-01", "TEST-AP-02"]),
    ])
    res = analysis.run_analysis(
        loader.collect_files(tmp_path), analysis.AnalysisParams(site=S.SITE_ID)
    )

    assert _floor_of(res, "TEST-AP-01") == S.FLOOR_1F, "map_id 経由の解決が壊れている"
    assert _floor_of(res, "TEST-AP-02") == S.FLOOR_1F, "ap_name 経由のフォールバックが壊れている"
    assert len(res.rows) == 2


def test_ap_not_in_ap_list_without_map_id_stays_unassigned_and_kept(tmp_path):
    """ap_list にも現れず map_id も無い AP は「（未割当）」に残り、除外されないこと。"""
    rows = [
        S.metrics_row(START, num_clients=5, **_ap(1)),
        S.metrics_row(START, num_clients=3, **_ap(2)),
    ]
    _write_v1_metrics(tmp_path / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_floormap(tmp_path, START, [
        # AP-02 はどのフロアの ap_list にも現れない
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36, ap_list=["TEST-AP-01"]),
    ])
    res = analysis.run_analysis(
        loader.collect_files(tmp_path), analysis.AnalysisParams(site=S.SITE_ID)
    )

    assert _floor_of(res, "TEST-AP-01") == S.FLOOR_1F
    assert _floor_of(res, "TEST-AP-02") == floors.UNASSIGNED
    assert len(res.rows) == 2, "未割当の AP が結果から落ちている"
    assert any("map_id を持たない AP が 1 台あります" in w for w in res.warnings)
    assert any("ap_list" in w for w in res.warnings)
    # 33 列版だから特定できない、という断定はしない
    assert not any("33 列版" in w and "特定できません" in w for w in res.warnings)


def test_map_id_wins_over_conflicting_ap_name_fallback():
    """map_id で引ける場合は、ap_name 直接引きの結果と食い違っても map_id 側が勝つこと。"""
    resolution = floors.FloorResolution(
        map_id_to_name={"map-x": "Floor A (map_id)"},
        ap_name_to_floor={"AP-1": "Floor B (ap_name)"},
    )
    assert resolution.floor_of("map-x", "AP-1") == "Floor A (map_id)"
    # map_id が引けなければ ap_name 側にフォールバックする
    assert resolution.floor_of("", "AP-1") == "Floor B (ap_name)"
    assert resolution.floor_of("unknown-map-id", "AP-1") == "Floor B (ap_name)"
    assert resolution.floor_of("", "unknown-ap") == floors.UNASSIGNED


# ---------------------------------------------------------------------------
# 5. 1 つの map_id に複数の map_name（データ側の異常）
# ---------------------------------------------------------------------------


def test_conflicting_floor_names_take_the_majority_and_warn(tmp_path):
    rows = [
        S.metrics_row(START, num_clients=3, map_id=S.MAP_1F, **_ap(1)),
        S.metrics_row(START, num_clients=2, map_id=S.MAP_1F, **_ap(2)),
        S.metrics_row(START, num_clients=1, map_id=S.MAP_1F, **_ap(3)),
    ]
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36,
                       ap_list=["TEST-AP-01", "TEST-AP-02"]),
        S.floormap_row(START, map_name=S.FLOOR_2F, band="5", channel=40, ap_list=["TEST-AP-03"]),
    ])
    res = _analyze(tmp_path, rows)

    assert set(res.rows["map_name"]) == {S.FLOOR_1F}
    conflict = [w for w in res.warnings if S.MAP_1F in w]
    assert len(conflict) == 1
    assert S.FLOOR_1F in conflict[0] and S.FLOOR_2F in conflict[0]


# ---------------------------------------------------------------------------
# 6. map_id が空の AP は「（未割当）」として残す（除外しない）
# ---------------------------------------------------------------------------


def test_ap_without_map_id_goes_to_unassigned_and_is_kept(tmp_path):
    rows = [
        S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1)),
        S.metrics_row(START, num_clients=4, map_id="", **_ap(2)),
    ]
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36, ap_list=["TEST-AP-01"]),
    ])
    res = _analyze(tmp_path, rows)

    assert len(res.rows) == 2, "未割当の AP が結果から落ちている"
    assert _floor_of(res, "TEST-AP-02") == floors.UNASSIGNED
    # map_id が空 → 33 列版 ap_metrics を読んだ可能性まで書いた警告が出る
    assert any("map_id を持たない AP が 1 台" in w for w in res.warnings)
    # フロアの並びで「（未割当）」は末尾
    assert [f["map_name"] for f in res.meta["floors"]] == [S.FLOOR_1F, floors.UNASSIGNED]


def test_unknown_map_id_goes_to_unassigned(tmp_path):
    """map_id はあるが、どのフロア名にも紐付かない AP。"""
    rows = [
        S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1)),
        S.metrics_row(START, num_clients=4, map_id="test-map-id-unknown", **_ap(2)),
    ]
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36, ap_list=["TEST-AP-01"]),
    ])
    res = _analyze(tmp_path, rows)

    assert _floor_of(res, "TEST-AP-02") == floors.UNASSIGNED
    assert len(res.rows) == 2
    # 「map_id が空」とは別の警告になる（原因の切り分けができること）
    assert any("どのフロアにも紐付かない AP が 1 台" in w for w in res.warnings)
    assert not any("map_id を持たない" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# 7. floormap が 24 時間以上離れている
# ---------------------------------------------------------------------------


def test_floormap_older_than_24h_leaves_everything_unassigned(tmp_path):
    rows = [
        S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1)),
        S.metrics_row(START, num_clients=4, map_id=S.MAP_2F, **_ap(2)),
    ]
    stale = START - timedelta(hours=25)
    S.default_floormap(tmp_path, stale)
    res = _analyze(tmp_path, rows)

    assert set(res.rows["map_name"]) == {floors.UNASSIGNED}
    assert len(res.rows) == 2
    assert res.meta["floormap_file"] is None
    assert any("時間離れています" in w for w in res.warnings)


def test_floormap_within_24h_is_used(tmp_path):
    """23 時間前なら使う（境界の反対側）。ずれは meta に必ず残す。"""
    rows = [S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1))]
    S.default_floormap(tmp_path, START - timedelta(hours=23))
    res = _analyze(tmp_path, rows)

    assert _floor_of(res, "TEST-AP-01") == S.FLOOR_1F
    assert res.meta["floormap_file"] is not None
    assert res.meta["floormap_offset_seconds"] == 23 * 3600


def test_no_floormap_at_all_warns(tmp_path):
    rows = [S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1))]
    res = _analyze(tmp_path, rows)

    assert set(res.rows["map_name"]) == {floors.UNASSIGNED}
    assert any("1 本もありません" in w for w in res.warnings)


def test_nearest_floormap_is_chosen(tmp_path):
    """複数あってもピーク時点に最も近い 1 本だけを読む。"""
    rows = [S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1))]
    S.write_floormap(tmp_path, START - timedelta(hours=3), [
        S.floormap_row(START - timedelta(hours=3), map_name="Old Floor", band="5",
                       channel=36, ap_list=["TEST-AP-01"]),
    ])
    S.write_floormap(tmp_path, START + timedelta(minutes=10), [
        S.floormap_row(START + timedelta(minutes=10), map_name="New Floor", band="5",
                       channel=36, ap_list=["TEST-AP-01"]),
    ])
    res = _analyze(tmp_path, rows)

    assert _floor_of(res, "TEST-AP-01") == "New Floor"
    assert res.meta["floormap_offset_seconds"] == 600


def test_other_site_rows_in_floormap_are_ignored(tmp_path):
    rows = [S.metrics_row(START, num_clients=5, map_id=S.MAP_1F, **_ap(1))]
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, site_name=S.OTHER_SITE_NAME, map_name="Other Floor",
                       band="5", channel=36, ap_list=["TEST-AP-01"]),
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36, ap_list=["TEST-AP-01"]),
    ])
    res = _analyze(tmp_path, rows)
    assert _floor_of(res, "TEST-AP-01") == S.FLOOR_1F


def test_rank_in_floor_is_deterministic(tmp_path):
    """同数のときは ap_name の昇順（実行のたびに順位が入れ替わらないこと）。"""
    rows = [
        S.metrics_row(START, num_clients=4, map_id=S.MAP_1F, **_ap(3)),
        S.metrics_row(START, num_clients=4, map_id=S.MAP_1F, **_ap(1)),
        S.metrics_row(START, num_clients=9, map_id=S.MAP_1F, **_ap(2)),
    ]
    S.write_floormap(tmp_path, START, [
        S.floormap_row(START, map_name=S.FLOOR_1F, band="5", channel=36,
                       ap_list=["TEST-AP-01", "TEST-AP-02", "TEST-AP-03"]),
    ])
    res = _analyze(tmp_path, rows)

    ranked = res.rows.sort_values("rank_in_floor")[["ap_name", "rank_in_floor"]]
    assert list(ranked["ap_name"]) == ["TEST-AP-02", "TEST-AP-01", "TEST-AP-03"]
    assert list(ranked["rank_in_floor"]) == [1, 2, 3]


def test_filename_timestamp_parsing():
    """収集されている 2 つの命名（毎正時 / 手動）をどちらも拾う。"""
    assert floors.parse_name_timestamp(Path("floormap_20260818_1400_JST_summary.csv")) == datetime(2026, 8, 18, 14, 0)
    assert floors.parse_name_timestamp(Path("floormap_20260516_151954_JST_manual_summary.csv")) == datetime(2026, 5, 16, 15, 19, 54)
    assert floors.parse_name_timestamp(Path("ap_metrics_20260818_1400_JST.csv")) is None
    assert floors.parse_name_timestamp(Path("floormap_20260818_1400_JST.csv")) is None
