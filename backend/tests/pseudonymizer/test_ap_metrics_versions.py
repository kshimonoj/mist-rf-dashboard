"""指示 09: ap_metrics の座標列追加に伴う 36 列版 / 33 列版(ap_metrics_v1)の仮名化。"""
from conftest import read_csv, write_csv

from pseudonymizer.cli import main
from pseudonymizer.schemas import AP_METRICS_V1_COLUMNS, detect_file_type


def test_ap_metrics_36col_map_id_pseudonymized_xy_passthrough(indir, tmp_path):
    """36 列版: map_id は仮名化され、x_m / y_m はそのまま通る。"""
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 0

    src = read_csv(indir / "ap_metrics_20240101_0900_TZT.csv")
    dst = read_csv(out / "ap_metrics_20240101_0900_TZT.csv")
    assert len(dst) == len(src)
    for s, d in zip(src, dst):
        assert d["map_id"].startswith("30000000-0000-4000-8000-")
        assert d["x_m"] == s["x_m"]
        assert d["y_m"] == s["y_m"]


def test_ap_metrics_v1_33col_is_detected_and_processed(tmp_path):
    """33 列版（座標列追加前）は ap_metrics_v1 として判定され、エラーにならない。"""
    din = tmp_path / "in"
    din.mkdir()
    row = {
        "timestamp": "2024-01-01 09:00:00",
        "site_id": "00000000-0000-4000-8000-000000000001",
        "site_name": "TestSite Alpha",
        "ap_id": "00000000-0000-4000-8000-00000000a001",
        "ap_name": "TEST-AP-01",
        "model": "TESTAP-100",
        "mac": "aabbccddee01",
        "status": "connected",
        "num_clients": "3",
    }
    assert detect_file_type(AP_METRICS_V1_COLUMNS).key == "ap_metrics_v1"

    write_csv(din / "ap_metrics_20231201_0900_TZT.csv", list(AP_METRICS_V1_COLUMNS), [row])

    out = tmp_path / "out"
    assert main([str(din), "--out", str(out)]) == 0

    result = read_csv(out / "ap_metrics_20231201_0900_TZT.csv")
    assert len(result) == 1
    r = result[0]
    assert list(r.keys()) == list(AP_METRICS_V1_COLUMNS)
    assert r["mac"].startswith("020")
    assert r["ap_name"].startswith("AP_")
    assert "map_id" not in r
