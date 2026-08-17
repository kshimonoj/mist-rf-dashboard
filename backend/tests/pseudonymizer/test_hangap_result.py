"""Hang AP 分析結果 CSV の仮名化（種別判定・列ごとの変換・構造由来の日本語）。

合成データのみを使う（実データ・実データ由来の値は fixtures に置かない）。
"""
from __future__ import annotations

import pytest

from conftest import read_csv, write_csv  # noqa: F401  (tests/pseudonymizer/conftest.py)
from pseudonymizer import service
from pseudonymizer.leakcheck import RULE_NON_ASCII
from pseudonymizer.schemas import (
    HANGAP_RESULT_COLUMNS,
    HANGAP_RESULT_TEXT_LITERALS,
    EVENT_LIST_SEPARATOR,
    TransformType as T,
    detect_file_type,
)

# ---------------------------------------------------------------------------
# 合成の分析結果（実データ由来の値は 1 つも含めない）
# ---------------------------------------------------------------------------

SYNTH_ROWS: tuple[dict[str, str], ...] = (
    {
        "ap_name": "TEST-AP-01",
        "site_name": "TestSite Alpha",
        "区間番号": "1",
        "AP内区間数": "2",
        "ゼロ直前時刻": "2026-01-01 09:00:00",
        "直前clients": "3",
        "直後clients（回復時）": "2",
        "ゼロ開始": "2026-01-01 09:05:00",
        "ゼロ終了": "2026-01-01 09:35:00",
        "連続ゼロ回数": "7",
        "回復状況": "回復",
        "回復時刻": "2026-01-01 09:40:00",
        "AP最大clients": "9",
        "AP Event（±30分）": "あり",
        "Event時刻": "2026-01-01 09:30:00 | 2026-01-01 09:36:00",
        "ゼロ終了との差(分)": "-5.0 | +1.0",
        "Event種別": "AP_RRM_ACTION | AP_RECONFIGURED",
        "Event詳細": "reason=rrm, channel=1→6 | bandwidth=20→40",
        "サイト合計clients(ゼロ開始時)": "40",
        "サイト合計clients(ゼロ終了時)": "38",
        "サイト全体変化率": "-0.05",
        "退場疑い": "False",
        "周辺AP数": "2",
        "周辺AP名": "TEST-AP-02, TEST-AP-03",
        "周辺AP距離": "8.0, 12.5",
        "周辺AP端末数": "2.0, 実測なし",
        "周辺AP端末数合計": "2.0",
        "周辺AP判定": "周辺に端末あり",
        "周辺AP RF隣接数": "1",
        "周辺AP実測なし数": "1",
    },
    {
        "ap_name": "TEST-AP-02",
        "site_name": "TestSite Alpha",
        "区間番号": "1",
        "AP内区間数": "1",
        "ゼロ直前時刻": "2026-01-01 10:00:00",
        "直前clients": "1",
        "直後clients（回復時）": "",
        "ゼロ開始": "2026-01-01 10:05:00",
        "ゼロ終了": "2026-01-01 10:45:00",
        "連続ゼロ回数": "9",
        "回復状況": "打ち切り(欠測)",
        "回復時刻": "",
        "AP最大clients": "4",
        "AP Event（±30分）": "",
        "Event時刻": "",
        "ゼロ終了との差(分)": "",
        "Event種別": "",
        "Event詳細": "",
        "サイト合計clients(ゼロ開始時)": "40",
        "サイト合計clients(ゼロ終了時)": "20",
        "サイト全体変化率": "-0.5",
        "退場疑い": "True",
        "周辺AP数": "",
        "周辺AP名": "",
        "周辺AP距離": "",
        "周辺AP端末数": "",
        "周辺AP端末数合計": "",
        "周辺AP判定": "判定不能",
        "周辺AP RF隣接数": "",
        "周辺AP実測なし数": "",
    },
)


def write_result_csv(path, rows=SYNTH_ROWS, *, bom: bool = True) -> None:
    """分析結果と同じ書式（全 30 列 / utf-8-sig）で書き出す。"""
    import csv

    encoding = "utf-8-sig" if bom else "utf-8"
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=list(HANGAP_RESULT_COLUMNS), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def result_path(tmp_path):
    path = tmp_path / "hangap_result_20260101_120000.csv"
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


def test_detects_hangap_result_from_header():
    ft = detect_file_type(list(HANGAP_RESULT_COLUMNS))
    assert ft is not None
    assert ft.key == "hangap_result"
    assert len(HANGAP_RESULT_COLUMNS) == 30


def test_column_definition_matches_hangap_detector():
    """``RESULT_COLUMNS`` を写して持っているので、ずれたらここで落とす。"""
    from hangap.detector import EVENT_SEPARATOR, RESULT_COLUMNS

    assert HANGAP_RESULT_COLUMNS == RESULT_COLUMNS
    assert EVENT_LIST_SEPARATOR == EVENT_SEPARATOR


def test_text_literals_match_hangap_constants():
    """構造由来の日本語の一覧が hangap 側の定数とずれたら落とす。"""
    from hangap.analysis import STATUS_ORDER, VERDICT_ORDER
    from hangap.neighbors import NO_MEASUREMENT

    for value in (*STATUS_ORDER, *VERDICT_ORDER, NO_MEASUREMENT):
        assert value in HANGAP_RESULT_TEXT_LITERALS, value


def test_rules_for_key_columns():
    ft = detect_file_type(list(HANGAP_RESULT_COLUMNS))
    assert ft.rule_for("ap_name") is T.AP_NAME
    assert ft.rule_for("site_name") is T.SITE_NAME
    assert ft.rule_for("周辺AP名") is T.AP_NAME_LIST
    assert ft.rule_for("Event時刻") is T.TIMESTAMP_LIST
    for col in ("ゼロ直前時刻", "ゼロ開始", "ゼロ終了", "回復時刻"):
        assert ft.rule_for(col) is T.TIMESTAMP
    assert ft.rule_for("Event詳細") is T.PASSTHROUGH


# ---------------------------------------------------------------------------
# 要件 2-4: 列ごとの変換
# ---------------------------------------------------------------------------


def test_ap_name_and_site_name_are_pseudonymized(store, result_path):
    rows = parse(run([result_path])[0])
    assert rows[0]["ap_name"].startswith("AP_")
    assert rows[0]["site_name"].startswith("SITE_")


def test_neighbor_ap_names_are_pseudonymized_elementwise(store, result_path):
    """要件 2: 周辺AP名 のカンマ区切りが要素ごとに変換され、区切りが保たれる。"""
    rows = parse(run([result_path])[0])
    names = [n.strip() for n in rows[0]["周辺AP名"].split(",")]
    assert len(names) == 2
    assert all(n.startswith("AP_") for n in names), names
    assert len(set(names)) == 2  # 別の AP は別の仮名
    assert rows[1]["周辺AP名"] == ""  # 空欄は空欄のまま


def test_neighbor_ap_name_matches_the_same_ap_in_ap_name_column(store, result_path):
    """周辺AP名 の TEST-AP-02 と、2 行目の ap_name の TEST-AP-02 は同じ仮名になる。"""
    rows = parse(run([result_path])[0])
    neighbor_first = rows[0]["周辺AP名"].split(",")[0].strip()
    assert neighbor_first == rows[1]["ap_name"]


def test_event_time_list_is_shifted_elementwise(store, result_path):
    """要件 3: Event時刻 の ` | ` 区切りの各要素にタイムシフトが効く。"""
    out = run([result_path])[0]
    rows = parse(out)
    parts = rows[0]["Event時刻"].split(EVENT_LIST_SEPARATOR)
    assert len(parts) == 2
    assert all(p != "" for p in parts)
    assert rows[0]["Event時刻"] != SYNTH_ROWS[0]["Event時刻"]
    # 要素同士の間隔（6 分）は保たれる
    import datetime as dt

    a, b = (dt.datetime.strptime(p, "%Y-%m-%d %H:%M:%S") for p in parts)
    assert b - a == dt.timedelta(minutes=6)
    # ゼロ終了とのずれ幅も揃っている（同じオフセットが当たっている）
    zero_end = dt.datetime.strptime(rows[0]["ゼロ終了"], "%Y-%m-%d %H:%M:%S")
    assert (a - zero_end) == dt.timedelta(minutes=-5)
    assert rows[1]["Event時刻"] == ""


def test_event_detail_is_passed_through(store, result_path):
    """要件 4: channel=A→B のような値は変更されない。"""
    rows = parse(run([result_path])[0])
    assert rows[0]["Event詳細"] == SYNTH_ROWS[0]["Event詳細"]
    assert rows[0]["Event種別"] == SYNTH_ROWS[0]["Event種別"]
    assert rows[0]["回復状況"] == "回復"
    assert rows[1]["回復状況"] == "打ち切り(欠測)"
    assert rows[0]["周辺AP端末数"] == "2.0, 実測なし"


def test_columns_and_row_count_are_preserved(store, result_path):
    out = run([result_path])[0]
    rows = parse(out)
    assert len(rows) == len(SYNTH_ROWS)
    assert list(rows[0].keys()) == list(HANGAP_RESULT_COLUMNS)
    assert out.content.startswith(b"\xef\xbb\xbf")  # Excel 向けの BOM を保つ


# ---------------------------------------------------------------------------
# 要件 11: leak check
# ---------------------------------------------------------------------------


def test_leftover_japanese_fires_leak_check(store, tmp_path):
    """構造由来でない日本語（passthrough 列に紛れた施設名）は違反になる。"""
    from pseudonymizer.leakcheck import LeakCheckFailed

    rows = [dict(SYNTH_ROWS[0])]
    rows[0]["Event詳細"] = "reason=東京本社ビル"
    path = tmp_path / "hangap_result_20260101_120000.csv"
    write_result_csv(path, rows)

    with pytest.raises(LeakCheckFailed) as excinfo:
        run([path])
    message = str(excinfo.value)
    assert RULE_NON_ASCII in message
    assert "Event詳細" in message
    assert "東京本社ビル" not in message  # 値そのものは出さない


def test_structural_japanese_does_not_fire_leak_check(store, result_path):
    """列名・判定値・→ は構造由来なので違反にしない。"""
    assert run([result_path])  # 例外が出ないこと
