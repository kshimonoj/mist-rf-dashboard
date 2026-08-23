"""RRM 分析結果 CSV の仮名化（種別判定・列ごとの変換）。

合成データのみを使う（実データ・実データ由来の値は fixtures に置かない）。
"""
from __future__ import annotations

import pytest

from pseudonymizer import service
from pseudonymizer.schemas import (
    RRM_RESULT_COLUMNS,
    TransformType as T,
    detect_file_type,
)

SYNTH_ROWS: tuple[dict[str, str], ...] = (
    {
        "event_timestamp": "2026-01-01 09:00:00",
        "classification": "RADAR",
        "reason": "radar-detected",
        "site_name": "TestSite Alpha",
        "ap_name": "TEST-AP-01",
        "ap_mac": "aabbccddee01",
        "band": "5",
        "pre_channel": "36",
        "post_channel": "100",
        "channel_changed": "True",
        "before_timestamp": "2026-01-01 08:59:30",
        "after_timestamp": "2026-01-01 09:00:30",
        "match_status": "ok",
        "contaminated": "False",
        "clients_before": "5",
        "clients_after": "3",
        "clients_delta": "-2",
        "util_24_before": "10.0",
        "util_24_after": "9.5",
        "util_24_delta": "-0.5",
        "util_5_before": "40.0",
        "util_5_after": "35.0",
        "util_5_delta": "-5.0",
        "util_6_before": "",
        "util_6_after": "",
        "util_6_delta": "",
        "impact_clients": "2",
    },
    {
        "event_timestamp": "2026-01-01 10:00:00",
        "classification": "RRM",
        "reason": "",
        "site_name": "TestSite Alpha",
        "ap_name": "TEST-AP-02",
        "ap_mac": "aabbccddee02",
        "band": "24",
        "pre_channel": "1",
        "post_channel": "6",
        "channel_changed": "True",
        "before_timestamp": "",
        "after_timestamp": "2026-01-01 10:00:15",
        "match_status": "no_before",
        "contaminated": "False",
        "clients_before": "",
        "clients_after": "8",
        "clients_delta": "",
        "util_24_before": "",
        "util_24_after": "20.0",
        "util_24_delta": "",
        "util_5_before": "",
        "util_5_after": "",
        "util_5_delta": "",
        "util_6_before": "",
        "util_6_after": "",
        "util_6_delta": "",
        "impact_clients": "",
    },
)


def write_result_csv(path, rows=SYNTH_ROWS, *, bom: bool = True) -> None:
    """分析結果と同じ書式（全 27 列 / utf-8-sig）で書き出す。"""
    import csv

    encoding = "utf-8-sig" if bom else "utf-8"
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=list(RRM_RESULT_COLUMNS), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def result_path(tmp_path):
    path = tmp_path / "rrm_result_20260101_120000.csv"
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


def test_detects_rrm_result_from_header():
    ft = detect_file_type(list(RRM_RESULT_COLUMNS))
    assert ft is not None
    assert ft.key == "rrm_result"
    assert len(RRM_RESULT_COLUMNS) == 27


def test_column_definition_matches_rrm_analysis():
    """``RESULT_COLUMNS`` を写して持っているので、ずれたらここで落とす。"""
    from rrm.analysis import RESULT_COLUMNS

    assert RRM_RESULT_COLUMNS == RESULT_COLUMNS


def test_rules_for_key_columns():
    ft = detect_file_type(list(RRM_RESULT_COLUMNS))
    assert ft.rule_for("ap_name") is T.AP_NAME
    assert ft.rule_for("ap_mac") is T.AP_MAC
    assert ft.rule_for("site_name") is T.SITE_NAME
    assert ft.rule_for("event_timestamp") is T.TIMESTAMP
    assert ft.rule_for("before_timestamp") is T.TIMESTAMP
    assert ft.rule_for("after_timestamp") is T.TIMESTAMP
    for col in (
        "classification", "reason", "band", "pre_channel", "post_channel",
        "channel_changed", "match_status", "contaminated",
        "clients_before", "clients_after", "clients_delta",
        "util_24_before", "util_24_after", "util_24_delta",
        "util_5_before", "util_5_after", "util_5_delta",
        "util_6_before", "util_6_after", "util_6_delta",
        "impact_clients",
    ):
        assert ft.rule_for(col) is T.PASSTHROUGH, col


# ---------------------------------------------------------------------------
# 要件 2-4: 列ごとの変換
# ---------------------------------------------------------------------------


def test_ap_name_mac_and_site_name_are_pseudonymized(store, result_path):
    rows = parse(run([result_path])[0])
    assert rows[0]["ap_name"].startswith("AP_")
    assert rows[0]["ap_name"] != SYNTH_ROWS[0]["ap_name"]
    assert rows[0]["ap_mac"] != SYNTH_ROWS[0]["ap_mac"]
    assert rows[0]["site_name"].startswith("SITE_")


def test_timestamps_are_shifted(store, result_path):
    rows = parse(run([result_path])[0])
    assert rows[0]["event_timestamp"] != SYNTH_ROWS[0]["event_timestamp"]
    assert rows[0]["before_timestamp"] != SYNTH_ROWS[0]["before_timestamp"]
    assert rows[0]["after_timestamp"] != SYNTH_ROWS[0]["after_timestamp"]
    # 空欄は空欄のまま（no_before の行）
    assert rows[1]["before_timestamp"] == ""


def test_enum_bool_and_numeric_columns_are_passed_through(store, result_path):
    rows = parse(run([result_path])[0])
    assert rows[0]["classification"] == "RADAR"
    assert rows[0]["reason"] == "radar-detected"
    assert rows[0]["band"] == "5"
    assert rows[0]["match_status"] == "ok"
    assert rows[0]["contaminated"] == "False"
    assert rows[0]["channel_changed"] == "True"
    assert rows[0]["pre_channel"] == "36"
    assert rows[0]["post_channel"] == "100"
    assert rows[0]["clients_delta"] == "-2"
    assert rows[0]["util_5_before"] == "40.0"
    assert rows[1]["match_status"] == "no_before"
    assert rows[1]["clients_before"] == ""


def test_columns_and_row_count_are_preserved(store, result_path):
    out = run([result_path])[0]
    rows = parse(out)
    assert len(rows) == len(SYNTH_ROWS)
    assert list(rows[0].keys()) == list(RRM_RESULT_COLUMNS)
    assert out.content.startswith(b"\xef\xbb\xbf")  # Excel 向けの BOM を保つ


# ---------------------------------------------------------------------------
# 回帰: 既存種別に影響が無いこと
# ---------------------------------------------------------------------------


def test_floorpeak_and_hangap_results_still_detected():
    from pseudonymizer.schemas import FLOORPEAK_RESULT_COLUMNS, HANGAP_RESULT_COLUMNS

    assert detect_file_type(list(FLOORPEAK_RESULT_COLUMNS)).key == "floorpeak_result"
    assert detect_file_type(list(HANGAP_RESULT_COLUMNS)).key == "hangap_result"
