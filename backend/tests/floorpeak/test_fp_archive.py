"""保存済み結果のローテートと、走査からの除外（floorpeak.archive / hangap.loader）。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import _fpsynth as S
import pytest

from floorpeak import archive, loader
from hangap import loader as hangap_loader

BASE = datetime(2026, 1, 1, 12, 0, 0)


def _make_set(root: Path, dt: datetime, size: int = 100) -> str:
    name = archive.name_for(dt)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.csv").write_text("x" * size, encoding="utf-8")
    (root / f"{name}.xlsx").write_bytes(b"y" * size)
    (root / f"{name}.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    return name


# ---------------------------------------------------------------------------
# 11. floorpeak_results 配下は hangap.loader の走査対象から外れている
# ---------------------------------------------------------------------------


def test_results_dir_is_excluded_from_scanning(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    S.write_metrics(logs / "ap_metrics_20260101_1000_TZT.csv",
                    [S.metrics_row(BASE, num_clients=1)])
    results = logs / archive.RESULTS_DIR_NAME
    results.mkdir()
    # 結果 csv は ap_metrics と同じ列を持たないので、入力として読まれると
    # 「種別を判定できないファイル」として分析を止めうる
    (results / "floorpeak_result_20260101_120000.csv").write_text(
        "ap_name,mac,model\nTEST-AP-01,aabbccddee01,AP45\n", encoding="utf-8"
    )

    found = [p.name for p in loader.collect_files(logs)]
    assert "ap_metrics_20260101_1000_TZT.csv" in found
    assert "floorpeak_result_20260101_120000.csv" not in found
    assert archive.RESULTS_DIR_NAME in hangap_loader.EXCLUDED_DIR_NAMES


def test_negative_control_without_the_exclusion_the_file_is_picked_up(tmp_path, monkeypatch):
    """**負のコントロール。** 除外を外すと上のテストが成立しなくなることを確かめる。

    これが落ちるときは、除外が効いているのではなく別の理由で拾われていない
    （＝上のテストが何も検証していない）ということ。
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    results = logs / archive.RESULTS_DIR_NAME
    results.mkdir()
    (results / "floorpeak_result_20260101_120000.csv").write_text(
        "ap_name,mac,model\nTEST-AP-01,aabbccddee01,AP45\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        hangap_loader, "EXCLUDED_DIR_NAMES",
        frozenset(hangap_loader.EXCLUDED_DIR_NAMES - {archive.RESULTS_DIR_NAME}),
    )
    found = [p.name for p in loader.collect_files(logs)]
    assert "floorpeak_result_20260101_120000.csv" in found


def test_results_dir_name_matches_the_exclusion_list():
    """保存先の名前と除外リストがずれていないこと（ずれると自分の出力を読む）。"""
    assert archive.RESULTS_DIR_NAME in hangap_loader.EXCLUDED_DIR_NAMES


# ---------------------------------------------------------------------------
# 保存・一覧・削除
# ---------------------------------------------------------------------------


def test_list_results_is_newest_first(tmp_path):
    root = tmp_path / "results"
    older = _make_set(root, BASE)
    newer = _make_set(root, BASE + timedelta(hours=1))
    assert [r["name"] for r in archive.list_results(root)] == [newer, older]


def test_invalid_names_are_never_touched(tmp_path):
    root = tmp_path / "results"
    _make_set(root, BASE)
    stray = root / "notes.csv"
    stray.write_text("keep me", encoding="utf-8")

    archive.rotate(root, keep_files=1, keep_bytes=1)
    assert stray.exists(), "組として認識できないファイルを消してはいけない"


def test_rotate_keeps_the_newest_set(tmp_path):
    root = tmp_path / "results"
    for i in range(4):
        _make_set(root, BASE + timedelta(hours=i))
    removed, freed = archive.rotate(root, keep_files=1, keep_bytes=1)

    assert removed == 3 and freed > 0
    remaining = archive.list_sets(root)
    assert len(remaining) == 1
    assert remaining[0].name == archive.name_for(BASE + timedelta(hours=3))


def test_rotate_deletes_sets_whole(tmp_path):
    root = tmp_path / "results"
    old = _make_set(root, BASE)
    _make_set(root, BASE + timedelta(hours=1))
    archive.rotate(root, keep_files=1, keep_bytes=10**9)

    for suffix in archive.MEMBER_SUFFIXES:
        assert not (root / f"{old}{suffix}").exists(), "組の一部だけが残っている"


def test_unique_name_does_not_overwrite(tmp_path):
    root = tmp_path / "results"
    first = _make_set(root, BASE)
    assert archive.unique_name(root, BASE) != first


@pytest.mark.parametrize("name,ok", [
    ("floorpeak_result_20260101_120000", True),
    ("hangap_result_20260101_120000", False),
    ("floorpeak_result_20260101", False),
    ("../floorpeak_result_20260101_120000", False),
    ("floorpeak_result_20260101_120000/x", False),
])
def test_is_valid_name(name, ok):
    assert archive.is_valid_name(name) is ok


def test_rotation_is_independent_of_hangap(tmp_path, monkeypatch):
    """hangap 側の上限を絞っても floorpeak の結果は消えない（逆も同じ）。"""
    from hangap import archive as hangap_archive

    fp_root = tmp_path / "floorpeak_results"
    hg_root = tmp_path / "hangap_results"
    _make_set(fp_root, BASE)
    _make_set(fp_root, BASE + timedelta(hours=1))
    hg_root.mkdir()
    for i in range(2):
        name = hangap_archive.name_for(BASE + timedelta(hours=i))
        (hg_root / f"{name}.csv").write_text("x" * 100, encoding="utf-8")

    hangap_archive.rotate(hg_root, keep_files=1, keep_bytes=1)
    assert len(archive.list_sets(fp_root)) == 2


def test_env_overrides(monkeypatch):
    monkeypatch.setenv(archive.ENV_MAX_FILES, "7")
    monkeypatch.setenv(archive.ENV_MAX_TOTAL_MB, "3")
    assert archive.max_files() == 7
    assert archive.max_total_bytes() == 3 * 1024 * 1024

    monkeypatch.setenv(archive.ENV_MAX_FILES, "0")
    assert archive.max_files() == archive.DEFAULT_MAX_FILES
    monkeypatch.setenv(archive.ENV_MAX_FILES, "abc")
    assert archive.max_files() == archive.DEFAULT_MAX_FILES
