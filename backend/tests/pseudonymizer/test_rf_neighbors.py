"""指示 10 パート A-3: rf_neighbors の仮名化。

要点は「``neighbor_mac`` / ``neighbor_name`` が ``ap_mac`` / ``ap_name`` と
**同じ名前空間**で採番されること」。別名前空間にすると隣接グラフが壊れて分析不能になる。
同時に、1 行に 2 台の AP が並ぶため、観測側と被観測側が同一視されないことも確認する。
"""
from conftest import read_csv

from pseudonymizer.cli import main
from pseudonymizer.salt import SaltMaterial
from pseudonymizer.schemas import FILE_TYPES_BY_KEY, TransformType as T, ap_link_column_groups
from pseudonymizer.transforms import MappingStore, Pseudonymizer

FIXTURE = "rf_neighbors_20240101_0900_TZT.csv"
AP_A = "aabbccddee01"
AP_B = "aabbccddee02"
AP_OUTSIDE = "aabbccddeeff"


def _run(indir, out):
    assert main([str(indir), "--out", str(out)]) == 0


def test_same_mac_gets_same_pseudonym_in_both_columns(indir, tmp_path):
    """同じ MAC が ap_mac 列と neighbor_mac 列の両方に現れたら同じ仮名になること。"""
    out = tmp_path / "out"
    _run(indir, out)

    src = read_csv(indir / FIXTURE)
    dst = read_csv(out / FIXTURE)

    mac_map: dict[str, set[str]] = {}
    for s, d in zip(src, dst):
        mac_map.setdefault(s["ap_mac"], set()).add(d["ap_mac"])
        mac_map.setdefault(s["neighbor_mac"], set()).add(d["neighbor_mac"])

    for original, pseudonyms in mac_map.items():
        assert len(pseudonyms) == 1, f"{original} に複数の仮名が割り当てられた"

    # 観測側にも被観測側にも現れる MAC が、両方の列で同一の仮名になっている
    a_as_observer = {d["ap_mac"] for s, d in zip(src, dst) if s["ap_mac"] == AP_A}
    a_as_neighbor = {d["neighbor_mac"] for s, d in zip(src, dst) if s["neighbor_mac"] == AP_A}
    assert a_as_observer == a_as_neighbor
    assert a_as_observer and next(iter(a_as_observer)).startswith("020")


def test_neighbor_name_shares_the_ap_name_namespace(indir, tmp_path):
    out = tmp_path / "out"
    _run(indir, out)
    src = read_csv(indir / FIXTURE)
    dst = read_csv(out / FIXTURE)

    name_map: dict[str, set[str]] = {}
    for s, d in zip(src, dst):
        name_map.setdefault(s["ap_name"], set()).add(d["ap_name"])
        if s["neighbor_name"]:
            name_map.setdefault(s["neighbor_name"], set()).add(d["neighbor_name"])
    for original, pseudonyms in name_map.items():
        assert len(pseudonyms) == 1, f"{original} に複数の仮名が割り当てられた"
        assert next(iter(pseudonyms)).startswith("AP_")


def test_neighbor_mac_and_neighbor_name_point_to_the_same_ap_number(indir, tmp_path):
    """neighbor_mac と neighbor_name が同じ AP の番号を指すこと。"""
    out = tmp_path / "out"
    _run(indir, out)
    for row in read_csv(out / FIXTURE):
        number = int(row["ap_name"].removeprefix("AP_"))
        assert row["ap_mac"] == f"020{number:09x}"
        if row["neighbor_name"]:
            nb_number = int(row["neighbor_name"].removeprefix("AP_"))
            assert row["neighbor_mac"] == f"020{nb_number:09x}"


def test_observer_and_neighbor_are_not_treated_as_the_same_ap(indir, tmp_path):
    """1 行に並ぶ 2 台が同一視されないこと（同一視されると仮名が潰れて分析不能になる）。"""
    out = tmp_path / "out"
    _run(indir, out)
    for row in read_csv(out / FIXTURE):
        assert row["ap_mac"] != row["neighbor_mac"]
        if row["neighbor_name"]:
            assert row["ap_name"] != row["neighbor_name"]


def test_unresolved_neighbor_name_stays_empty(indir, tmp_path):
    """名前を解決できなかった隣接（サイト外 AP）は空欄のまま残り、MAC は仮名化されること。"""
    out = tmp_path / "out"
    _run(indir, out)
    src = read_csv(indir / FIXTURE)
    dst = read_csv(out / FIXTURE)
    outside = [d for s, d in zip(src, dst) if s["neighbor_mac"] == AP_OUTSIDE]
    assert len(outside) == 1
    assert outside[0]["neighbor_name"] == ""
    assert outside[0]["neighbor_mac"] not in ("", AP_OUTSIDE)


def test_band_and_rssi_pass_through(indir, tmp_path):
    out = tmp_path / "out"
    _run(indir, out)
    src = read_csv(indir / FIXTURE)
    dst = read_csv(out / FIXTURE)
    for s, d in zip(src, dst):
        assert d["band"] == s["band"]
        assert d["rssi"] == s["rssi"]


def test_link_groups_are_split_per_ap():
    ft = FILE_TYPES_BY_KEY["rf_neighbors"]
    assert ap_link_column_groups(ft) == (("ap_mac", "ap_name"), ("neighbor_mac", "neighbor_name"))


def test_no_inconsistent_linkage_warning_is_emitted():
    """観測側と被観測側を別グループに分けているので、リンク不整合の警告は出ない。"""
    ft = FILE_TYPES_BY_KEY["rf_neighbors"]
    warnings: list[str] = []
    material = SaltMaterial(
        salt=b"test-salt-value", time_offset_seconds=0, created_at="2024-01-01T00:00:00Z",
    )
    p = Pseudonymizer(
        material, MappingStore(salt_fingerprint=material.fingerprint), warn=warnings.append,
    )
    row = {
        "timestamp": "2024-01-01 09:00:00",
        "site_id": "00000000-0000-4000-8000-000000000001",
        "site_name": "TestSite Alpha",
        "band": "5",
        "ap_mac": AP_A, "ap_name": "TEST-AP-01",
        "neighbor_mac": AP_B, "neighbor_name": "TEST-AP-02",
        "rssi": "-58.0",
    }
    p.observe_row(ft, row)
    p.build()
    assert warnings == []
    assert p.pseudonym(T.AP_MAC, AP_A) != p.pseudonym(T.AP_MAC, AP_B)
