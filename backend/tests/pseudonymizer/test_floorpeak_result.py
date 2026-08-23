"""Floor Peak 分析結果 CSV の仮名化（種別判定・列ごとの変換）。

合成データのみを使う（実データ・実データ由来の値は fixtures に置かない）。
"""
from __future__ import annotations

import pytest

from pseudonymizer import service
from pseudonymizer.schemas import (
    FLOORPEAK_RESULT_COLUMNS,
    TransformType as T,
    detect_file_type,
)

SYNTH_ROWS: tuple[dict[str, str], ...] = (
    {
        "ap_name": "TEST-AP-01",
        "mac": "aabbccddee01",
        "model": "AP45",
        "num_clients": "12",
        "status": "connected",
        "map_id": "map-001",
        "map_name": "TestFloor 1F",
        "x_m": "3.5",
        "y_m": "8.2",
        "rank_in_floor": "1",
    },
    {
        "ap_name": "TEST-AP-02",
        "mac": "aabbccddee02",
        "model": "AP34",
        "num_clients": "4",
        "status": "connected",
        "map_id": "map-001",
        "map_name": "TestFloor 1F",
        "x_m": "",
        "y_m": "",
        "rank_in_floor": "2",
    },
)


def write_result_csv(path, rows=SYNTH_ROWS, *, bom: bool = True) -> None:
    """分析結果と同じ書式（全 10 列 / utf-8-sig）で書き出す。"""
    import csv

    encoding = "utf-8-sig" if bom else "utf-8"
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=list(FLOORPEAK_RESULT_COLUMNS), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def result_path(tmp_path):
    path = tmp_path / "floorpeak_result_20260101_120000.csv"
    write_result_csv(path)
    return path


@pytest.fixture
def store(tmp_path, monkeypatch):
    """ソルト・マッピングを tmp_path に隔離する。"""
    monkeypatch.setattr(service, "SALT_PATH", str(tmp_path / "store" / ".pseudonym_salt.json"))
    monkeypatch.setattr(service, "MAP_PATH", str(tmp_path / "store" / ".pseudonym_map.json"))
    return tmp_path / "store"


def run(paths):
    return service.pseudonymize_files([p for p in paths])


def parse(output: service.Output) -> list[dict[str, str]]:
    import csv
    import io

    text = output.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# 要件 1: 種別判定
# ---------------------------------------------------------------------------


def test_detects_floorpeak_result_from_header():
    ft = detect_file_type(list(FLOORPEAK_RESULT_COLUMNS))
    assert ft is not None
    assert ft.key == "floorpeak_result"
    assert len(FLOORPEAK_RESULT_COLUMNS) == 10


def test_column_definition_matches_floorpeak_analysis():
    """``RESULT_COLUMNS`` を写して持っているので、ずれたらここで落とす。"""
    from floorpeak.analysis import RESULT_COLUMNS

    assert FLOORPEAK_RESULT_COLUMNS == RESULT_COLUMNS


def test_rules_for_key_columns():
    ft = detect_file_type(list(FLOORPEAK_RESULT_COLUMNS))
    assert ft.rule_for("ap_name") is T.AP_NAME
    assert ft.rule_for("mac") is T.AP_MAC
    assert ft.rule_for("map_name") is T.MAP_NAME
    assert ft.rule_for("map_id") is T.MAP_ID
    for col in ("model", "num_clients", "status", "x_m", "y_m", "rank_in_floor"):
        assert ft.rule_for(col) is T.PASSTHROUGH, col


# ---------------------------------------------------------------------------
# 要件 2-4: 列ごとの変換
# ---------------------------------------------------------------------------


def test_ap_name_and_mac_are_pseudonymized(store, result_path):
    rows = parse(run([result_path])[0])
    assert rows[0]["ap_name"].startswith("AP_")
    assert rows[0]["ap_name"] != SYNTH_ROWS[0]["ap_name"]
    assert rows[0]["mac"] != SYNTH_ROWS[0]["mac"]
    # 同じ AP は常に同じ仮名（ap_name と mac が同一 AP を指すので揃うこと）
    assert rows[1]["ap_name"].startswith("AP_")
    assert rows[0]["ap_name"] != rows[1]["ap_name"]


def test_map_name_is_pseudonymized(store, result_path):
    rows = parse(run([result_path])[0])
    assert rows[0]["map_name"] != SYNTH_ROWS[0]["map_name"]
    # 同じフロアは常に同じ仮名
    assert rows[0]["map_name"] == rows[1]["map_name"]


def test_numeric_and_status_columns_are_passed_through(store, result_path):
    rows = parse(run([result_path])[0])
    assert rows[0]["num_clients"] == "12"
    assert rows[0]["status"] == "connected"
    assert rows[0]["model"] == "AP45"
    assert rows[0]["rank_in_floor"] == "1"
    assert rows[0]["x_m"] == "3.5"
    assert rows[1]["x_m"] == ""


def test_columns_and_row_count_are_preserved(store, result_path):
    out = run([result_path])[0]
    rows = parse(out)
    assert len(rows) == len(SYNTH_ROWS)
    assert list(rows[0].keys()) == list(FLOORPEAK_RESULT_COLUMNS)
    assert out.content.startswith(b"\xef\xbb\xbf")  # Excel 向けの BOM を保つ


# ---------------------------------------------------------------------------
# 回帰: 既存種別に影響が無いこと
# ---------------------------------------------------------------------------


def test_hangap_result_still_detected():
    from pseudonymizer.schemas import HANGAP_RESULT_COLUMNS

    ft = detect_file_type(list(HANGAP_RESULT_COLUMNS))
    assert ft is not None
    assert ft.key == "hangap_result"
