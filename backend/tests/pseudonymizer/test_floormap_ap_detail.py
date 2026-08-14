"""指示 06 修正 2: floormap_ap_detail 種別と MAP_ID 変換型。"""
from conftest import read_csv, write_csv

from pseudonymizer.cli import main
from pseudonymizer.salt import SaltMaterial
from pseudonymizer.schemas import FILE_TYPES_BY_KEY, FLOORMAP_AP_DETAIL_COLUMNS, TransformType as T
from pseudonymizer.transforms import MappingStore, Pseudonymizer

SALT = SaltMaterial(salt=b"\x03" * 32, time_offset_seconds=-3 * 24 * 3600,
                    created_at="2024-01-01T00:00:00+00:00")


def make_engine(**kwargs):
    return Pseudonymizer(SALT, MappingStore(salt_fingerprint=SALT.fingerprint), **kwargs)


def _row(**overrides):
    row = {
        "timestamp": "2024-01-01 09:00:00",
        "site_id": "00000000-0000-4000-8000-000000000001",
        "site_name": "TestSite Alpha",
        "map_id": "00000000-0000-4000-8000-0000000000f1",
        "map_name": "Test Bldg 1F",
        "ap_name": "TEST-AP-01",
        "mac": "aabbccddee01",
        "model": "TESTAP-100",
        "status": "connected",
        "band_24_channel": "1",
        "band_24_bandwidth": "20",
        "band_24_power": "11",
        "band_24_noise_floor": "-95",
        "band_5_channel": "36",
        "band_5_bandwidth": "80",
        "band_5_power": "11",
        "band_5_noise_floor": "-95",
        "band_6_channel": "37",
        "band_6_bandwidth": "80",
        "band_6_power": "11",
        "band_6_noise_floor": "-95",
        "num_clients": "3",
        "x_m": "12.5",
        "y_m": "7.25",
    }
    row.update(overrides)
    return row


def test_floormap_ap_detail_is_detected_and_transformed(tmp_path):
    din = tmp_path / "in"
    din.mkdir()
    row = _row()
    write_csv(din / "floormap_20260516_111448_JST_manual.csv",
              list(FLOORMAP_AP_DETAIL_COLUMNS), [row])

    out = tmp_path / "out"
    assert main([str(din), "--out", str(out)]) == 0

    result = read_csv(out / "floormap_20260516_111448_JST_manual.csv")
    assert len(result) == 1
    r = result[0]

    # 仮名化される列
    assert r["site_id"].startswith("20000000-0000-4000-8000-")
    assert r["map_id"].startswith("30000000-0000-4000-8000-")
    assert r["map_name"].startswith("FLOOR_")
    assert r["ap_name"].startswith("AP_")
    assert r["mac"].startswith("020")  # floormap_ap_detail の mac は AP の MAC

    # x_m / y_m は変換せずそのまま通す
    assert r["x_m"] == row["x_m"]
    assert r["y_m"] == row["y_m"]

    # passthrough 列
    assert r["model"] == row["model"]
    assert r["status"] == row["status"]
    assert r["band_24_channel"] == row["band_24_channel"]
    assert r["band_5_noise_floor"] == row["band_5_noise_floor"]
    assert r["num_clients"] == row["num_clients"]


def test_map_id_uses_an_independent_namespace_from_site_id_and_ap_id():
    """site_id / ap_id / map_id が同じ元値でも異なる仮名になる(名前空間が独立)。"""
    engine = make_engine()
    ft = FILE_TYPES_BY_KEY["floormap_ap_detail"]
    shared_value = "00000000-0000-4000-8000-shared00001"

    row = _row(site_id=shared_value, map_id=shared_value)
    engine.observe_row(ft, row)
    engine.build()

    site_pseudo = engine.pseudonym(T.SITE_ID, shared_value)
    map_pseudo = engine.pseudonym(T.MAP_ID, shared_value)

    assert site_pseudo.startswith("20000000-0000-4000-8000-")
    assert map_pseudo.startswith("30000000-0000-4000-8000-")
    assert site_pseudo != map_pseudo
