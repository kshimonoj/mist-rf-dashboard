"""ダウンロード時のその場仮名化の一貫性（要件 5・6・7）。

**ここが落ちると仮名化データは使い物にならない。**

- 種別をまたいで同じ AP が同じ仮名になること
- 別々のリクエストで落としても仮名が変わらないこと
- サーバを再起動しても（ソルト・マッピングを読み直しても）変わらないこと

合成データのみを使う。
"""
from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path

import pytest

from conftest import FIXTURES_DIR
from pseudonymizer import service
from test_hangap_result import write_result_csv


@pytest.fixture
def store(tmp_path, monkeypatch):
    """ソルト・マッピングを tmp_path/store に隔離する（既定は data/ 直下）。"""
    d = tmp_path / "store"
    monkeypatch.setattr(service, "SALT_PATH", str(d / ".pseudonym_salt.json"))
    monkeypatch.setattr(service, "MAP_PATH", str(d / ".pseudonym_map.json"))
    return d


@pytest.fixture
def files(tmp_path):
    """合成の ap_metrics / ap_events / 分析結果を置いた入力ディレクトリ。"""
    d = tmp_path / "in"
    d.mkdir()
    for name in ("ap_metrics_20240101_0900_TZT.csv", "ap_events_20240101_0900_TZT.csv"):
        shutil.copy(Path(FIXTURES_DIR) / name, d / name)
    write_result_csv(d / "hangap_result_20260101_120000.csv")
    return d


def rows_of(output: service.Output) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(output.content.decode("utf-8-sig"))))


def by_name(outputs: list[service.Output]) -> dict[str, service.Output]:
    return {o.source_name: o for o in outputs}


# ---------------------------------------------------------------------------
# 要件 5: 種別をまたいだ一貫性
# ---------------------------------------------------------------------------


def test_same_ap_gets_the_same_pseudonym_across_file_types(store, files):
    """ap_metrics と分析結果に同じ AP 名があれば、同じ仮名になる。"""
    outputs = by_name(
        service.pseudonymize_files([
            files / "ap_metrics_20240101_0900_TZT.csv",
            files / "hangap_result_20260101_120000.csv",
        ])
    )
    metrics = rows_of(outputs["ap_metrics_20240101_0900_TZT.csv"])
    result = rows_of(outputs["hangap_result_20260101_120000.csv"])

    # 合成フィクスチャの TEST-AP-01 / TEST-AP-02 は分析結果にも出てくる
    src_metrics = list(csv.DictReader((files / "ap_metrics_20240101_0900_TZT.csv").open()))
    metrics_map = {s["ap_name"]: m["ap_name"] for s, m in zip(src_metrics, metrics)}
    assert "TEST-AP-01" in metrics_map and "TEST-AP-02" in metrics_map

    assert result[0]["ap_name"] == metrics_map["TEST-AP-01"]
    assert result[1]["ap_name"] == metrics_map["TEST-AP-02"]
    # 周辺AP名（AP_NAME_LIST）も同じ名前空間で採番される
    assert result[0]["周辺AP名"].split(",")[0].strip() == metrics_map["TEST-AP-02"]


def test_site_name_is_consistent_across_file_types(store, files):
    outputs = by_name(
        service.pseudonymize_files([
            files / "ap_metrics_20240101_0900_TZT.csv",
            files / "hangap_result_20260101_120000.csv",
        ])
    )
    metrics = rows_of(outputs["ap_metrics_20240101_0900_TZT.csv"])
    result = rows_of(outputs["hangap_result_20260101_120000.csv"])
    assert result[0]["site_name"] == metrics[0]["site_name"]


# ---------------------------------------------------------------------------
# 要件 6: タイミングをまたいだ一貫性
# ---------------------------------------------------------------------------


def test_same_file_twice_gives_identical_bytes(store, files):
    path = files / "ap_metrics_20240101_0900_TZT.csv"
    first = service.pseudonymize_files([path])[0]
    second = service.pseudonymize_files([path])[0]
    assert first.content == second.content
    assert first.filename == second.filename


def test_separate_requests_agree_on_the_same_ap(store, files):
    """別々のリクエストで落としても、同じ AP は同じ仮名になる。"""
    metrics = rows_of(
        service.pseudonymize_files([files / "ap_metrics_20240101_0900_TZT.csv"])[0]
    )
    result = rows_of(
        service.pseudonymize_files([files / "hangap_result_20260101_120000.csv"])[0]
    )
    src_metrics = list(csv.DictReader((files / "ap_metrics_20240101_0900_TZT.csv").open()))
    metrics_map = {s["ap_name"]: m["ap_name"] for s, m in zip(src_metrics, metrics)}
    assert result[0]["ap_name"] == metrics_map["TEST-AP-01"]
    assert result[1]["ap_name"] == metrics_map["TEST-AP-02"]


def test_order_of_requests_does_not_change_existing_pseudonyms(store, files):
    """先に分析結果、後からログ。既に配った仮名は動かない。"""
    result_first = rows_of(
        service.pseudonymize_files([files / "hangap_result_20260101_120000.csv"])[0]
    )
    metrics = rows_of(
        service.pseudonymize_files([files / "ap_metrics_20240101_0900_TZT.csv"])[0]
    )
    result_again = rows_of(
        service.pseudonymize_files([files / "hangap_result_20260101_120000.csv"])[0]
    )
    assert result_first == result_again
    src_metrics = list(csv.DictReader((files / "ap_metrics_20240101_0900_TZT.csv").open()))
    metrics_map = {s["ap_name"]: m["ap_name"] for s, m in zip(src_metrics, metrics)}
    assert result_first[0]["ap_name"] == metrics_map["TEST-AP-01"]


def test_time_shift_is_the_same_across_requests(store, files):
    a = rows_of(service.pseudonymize_files([files / "ap_metrics_20240101_0900_TZT.csv"])[0])
    b = rows_of(service.pseudonymize_files([files / "ap_events_20240101_0900_TZT.csv"])[0])
    # 同じソルト = 同じオフセット。日付部分が一致する
    assert a[0]["timestamp"][:10] == b[0]["event_timestamp"][:10]


# ---------------------------------------------------------------------------
# 要件 7: ソルト・マッピングの永続化
# ---------------------------------------------------------------------------


def test_salt_and_map_are_persisted(store, files):
    service.pseudonymize_files([files / "ap_metrics_20240101_0900_TZT.csv"])
    salt = Path(service.SALT_PATH)
    mapping = Path(service.MAP_PATH)
    assert salt.is_file() and mapping.is_file()
    assert (salt.stat().st_mode & 0o777) == 0o600
    assert (mapping.stat().st_mode & 0o777) == 0o600


def test_pseudonyms_survive_a_simulated_restart(store, files):
    """プロセス内キャッシュを持たないこと（毎回ファイルから読み直す）を確認する。"""
    path = files / "ap_metrics_20240101_0900_TZT.csv"
    before = service.pseudonymize_files([path])[0]
    salt_bytes = Path(service.SALT_PATH).read_bytes()
    map_bytes = Path(service.MAP_PATH).read_bytes()

    # 再起動を模す: ファイルはそのまま、モジュール状態は使い回さない
    after = service.pseudonymize_files([path])[0]
    assert after.content == before.content
    assert Path(service.SALT_PATH).read_bytes() == salt_bytes
    # 新規採番が無ければマッピングも変わらない
    assert Path(service.MAP_PATH).read_bytes() == map_bytes


def test_a_new_salt_changes_the_pseudonyms(store, files):
    """ソルトを失うと対応が切れる（README に書いてある通りであることの確認）。"""
    path = files / "ap_metrics_20240101_0900_TZT.csv"
    before = service.pseudonymize_files([path])[0]
    Path(service.SALT_PATH).unlink()
    Path(service.MAP_PATH).unlink()
    after = service.pseudonymize_files([path])[0]
    assert after.content != before.content


# ---------------------------------------------------------------------------
# ファイル名（中身の時刻はずれるのに、ファイル名だけ実日付が残ると台無しになる）
# ---------------------------------------------------------------------------


def test_output_filename_is_marked_and_shifted(store, files):
    out = service.pseudonymize_files([files / "ap_metrics_20240101_0900_TZT.csv"])[0]
    assert out.filename.endswith("_pseudonymized.csv")
    assert "20240101" not in out.filename
    assert "_0900_TZT" in out.filename  # 時刻と TZ の並びは保つ


def test_shift_name_timestamp_keeps_the_rest_of_the_name():
    assert service.shift_name_timestamp("ap_metrics_20260101_0900_JST", -86400) == (
        "ap_metrics_20251231_0900_JST"
    )
    assert service.shift_name_timestamp("hangap_result_20260101_120000", -86400) == (
        "hangap_result_20251231_120000"
    )
