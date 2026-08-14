"""変換型ごとの振る舞い（形式・分割・種別差・タイムシフト）。"""
import os

import pytest
from conftest import read_csv

from pseudonymizer.cli import main
from pseudonymizer.salt import SaltMaterial
from pseudonymizer.schemas import TransformType as T, detect_file_type
from pseudonymizer.transforms import (
    MappingStore,
    Pseudonymizer,
    format_pseudonym,
    shift_timestamp,
)

SALT = SaltMaterial(salt=b"\x01" * 32, time_offset_seconds=-7 * 24 * 3600,
                    created_at="2024-01-01T00:00:00+00:00")


def make_engine(**kwargs):
    return Pseudonymizer(SALT, MappingStore(salt_fingerprint=SALT.fingerprint), **kwargs)


def test_pseudonym_formats():
    assert format_pseudonym(T.SITE_ID, 7) == "20000000-0000-4000-8000-000000000007"
    assert format_pseudonym(T.AP_ID, 7) == "10000000-0000-4000-8000-000000000007"
    assert format_pseudonym(T.SITE_NAME, 7) == "SITE_007"
    assert format_pseudonym(T.AP_NAME, 200) == "AP_0200"
    assert format_pseudonym(T.HOSTNAME, 3) == "HOST_0003"
    assert format_pseudonym(T.SSID, 3) == "SSID_003"
    assert format_pseudonym(T.MAP_NAME, 3) == "FLOOR_003"
    assert format_pseudonym(T.VLAN, 3) == "3"
    assert format_pseudonym(T.IP, 258) == "10.0.1.2"


def test_ap_mac_matches_ap_name_number():
    """AP_0200 の MAC は末尾 c8（= 200）で対応が取れる。"""
    mac = format_pseudonym(T.AP_MAC, 200)
    assert mac == "0200000000c8"
    assert mac.startswith("02") and len(mac) == 12


def test_ap_mac_and_client_mac_are_separate_series():
    assert format_pseudonym(T.AP_MAC, 1) != format_pseudonym(T.CLIENT_MAC, 1)
    assert format_pseudonym(T.CLIENT_MAC, 1).startswith("02")


def test_shift_timestamp_keeps_format():
    assert shift_timestamp("2024-01-08 09:30:00", -7 * 24 * 3600) == "2024-01-01 09:30:00"
    assert shift_timestamp("", -100) == ""


def test_shift_timestamp_iso_and_z():
    assert shift_timestamp("2024-01-08T09:30:00Z", -86400) == "2024-01-07T09:30:00Z"


def test_mac_input_normalized_regardless_of_separator():
    engine = make_engine()
    ft = detect_file_type("ap_metrics_x.csv")
    engine.observe_row(ft, {"mac": "AA:BB:CC:DD:EE:01"})
    engine.build()
    assert engine.pseudonym(T.AP_MAC, "aabbccddee01") == engine.pseudonym(T.AP_MAC, "AA-BB-CC-DD-EE-01")


def test_empty_values_stay_empty():
    engine = make_engine()
    ft = detect_file_type("ap_events_x.csv")
    engine.observe_row(ft, {"ap_name": "", "ap_mac": ""})
    engine.build()
    row = engine.transform_row(ft, {"ap_name": "", "ap_mac": "", "reason": ""})
    assert row == {"ap_name": "", "ap_mac": "", "reason": ""}


def test_ap_list_is_split_transformed_and_rejoined(indir, tmp_path):
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 0

    floormap = read_csv(out / "floormap_20240101_0900_TZT_summary.csv")
    ap_metrics = read_csv(out / "ap_metrics_20240101_0900_TZT.csv")
    names = {r["ap_name"] for r in ap_metrics}

    first = floormap[0]["ap_list"].split(",")
    assert len(first) == 2, "カンマ区切りが 2 要素に分割されていない"
    assert all(n.startswith("AP_") for n in first)
    assert set(first) == names
    # 1 要素だけの行も壊れない
    assert "," not in floormap[1]["ap_list"]
    assert floormap[1]["ap_list"] in names


def test_ap_metrics_mac_and_client_mac_use_different_series(indir, tmp_path):
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 0

    ap_macs = {r["mac"] for r in read_csv(out / "ap_metrics_20240101_0900_TZT.csv")}
    client_macs = {r["mac"] for r in read_csv(out / "client_metrics_20240101_0900_TZT.csv")}

    assert all(m.startswith("020") for m in ap_macs)
    assert all(m.startswith("021") for m in client_macs)
    assert not (ap_macs & client_macs)


def test_map_name_and_hostname_and_ssid_are_replaced(indir, tmp_path):
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 0

    floormap = read_csv(out / "floormap_20240101_0900_TZT_summary.csv")
    assert all(r["map_name"].startswith("FLOOR_") for r in floormap)

    clients = read_csv(out / "client_metrics_20240101_0900_TZT.csv")
    assert all(r["hostname"].startswith("HOST_") for r in clients)
    assert all(r["ssid"].startswith("SSID_") for r in clients)
    assert all(r["ip"].startswith("10.") for r in clients)


def test_vlan_transformed_by_default_and_kept_with_flag(indir, tmp_path):
    out_a = tmp_path / "out_a"
    assert main([str(indir), "--out", str(out_a)]) == 0
    vlans = {r["vlan_id"] for r in read_csv(out_a / "client_metrics_20240101_0900_TZT.csv")}
    assert vlans and vlans.isdisjoint({"100", "200"})

    out_b = tmp_path / "out_b"
    assert main([str(indir), "--out", str(out_b), "--keep-vlan"]) == 0
    kept = {r["vlan_id"] for r in read_csv(out_b / "client_metrics_20240101_0900_TZT.csv")}
    assert kept == {"100", "200"}


def test_passthrough_columns_are_untouched(indir, tmp_path):
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 0
    src = read_csv(indir / "ap_metrics_20240101_0900_TZT.csv")
    dst = read_csv(out / "ap_metrics_20240101_0900_TZT.csv")
    for a, b in zip(src, dst):
        for col in ("status", "num_clients", "model", "radio_5_channel", "radio_6_noise_floor"):
            assert a[col] == b[col]


def test_salt_file_permissions_are_0600(indir, tmp_path):
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 0
    salt_path = out / ".pseudonym_salt.json"
    assert salt_path.exists()
    assert oct(os.stat(salt_path).st_mode & 0o777) == "0o600"
