"""保存済み結果のローテートと名前の検証。合成データのみを使う。

``hangap`` / ``floorpeak`` の保存領域とは独立していること（片方の都合でもう
片方の記録が消えないこと）もここで固定する。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import _rrmsynth as S
import pandas as pd
import pytest

from hangap import loader as hangap_loader
from rrm import analysis, archive, cli

START = datetime(2026, 1, 1, 10, 0, 0)


def _make_set(results_dir: Path, name: str, size: int = 16) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    for suffix in (".xlsx", ".csv"):
        (results_dir / f"{name}{suffix}").write_bytes(b"x" * size)
    (results_dir / f"{name}.json").write_text(
        json.dumps({"name": name, "change_count": 1}), encoding="utf-8"
    )


def test_results_dir_name_matches_the_exclusion_list():
    assert archive.RESULTS_DIR_NAME in hangap_loader.EXCLUDED_DIR_NAMES


@pytest.mark.parametrize("name,ok", [
    ("rrm_result_20260101_100000", True),
    ("rrm_result_2026010_100000", False),
    ("floorpeak_result_20260101_100000", False),
    ("../rrm_result_20260101_100000", False),
    ("rrm_result_20260101_100000/../x", False),
    ("/etc/passwd", False),
])
def test_is_valid_name(name, ok):
    assert archive.is_valid_name(name) is ok


def test_list_results_is_newest_first(tmp_path):
    for name in ("rrm_result_20260101_100000", "rrm_result_20260101_110000"):
        _make_set(tmp_path, name)
    names = [r["name"] for r in archive.list_results(tmp_path)]
    assert names == ["rrm_result_20260101_110000", "rrm_result_20260101_100000"]


def test_invalid_names_are_never_touched(tmp_path):
    _make_set(tmp_path, "rrm_result_20260101_100000")
    stranger = tmp_path / "important.csv"
    stranger.write_text("keep me", encoding="utf-8")

    archive.rotate(tmp_path, keep_files=0, keep_bytes=0)
    assert stranger.is_file()


def test_rotate_keeps_the_newest_set(tmp_path):
    for hour in range(10, 15):
        _make_set(tmp_path, f"rrm_result_20260101_{hour}0000")
    removed, _ = archive.rotate(tmp_path, keep_files=1, keep_bytes=10**9)
    remaining = [s.name for s in archive.list_sets(tmp_path)]
    assert removed == 4
    assert remaining == ["rrm_result_20260101_140000"]


def test_rotate_deletes_sets_whole(tmp_path):
    _make_set(tmp_path, "rrm_result_20260101_100000")
    _make_set(tmp_path, "rrm_result_20260101_110000")
    archive.rotate(tmp_path, keep_files=1, keep_bytes=10**9)
    assert not list(tmp_path.glob("rrm_result_20260101_100000.*"))


def test_unique_name_does_not_overwrite(tmp_path):
    when = datetime(2026, 1, 1, 10, 0, 0)
    _make_set(tmp_path, archive.name_for(when))
    assert archive.unique_name(tmp_path, when) == archive.name_for(when + timedelta(seconds=1))


def test_rotation_is_independent_of_other_analyses(tmp_path):
    """rrm のローテートが hangap / floorpeak の保存領域を触らないこと。"""
    rrm_dir = tmp_path / archive.RESULTS_DIR_NAME
    other = tmp_path / "floorpeak_results"
    _make_set(rrm_dir, "rrm_result_20260101_100000")
    _make_set(rrm_dir, "rrm_result_20260101_110000")
    _make_set(other, "floorpeak_result_20260101_100000")

    archive.rotate(rrm_dir, keep_files=1, keep_bytes=10**9)
    assert len(list(other.glob("floorpeak_result_20260101_100000.*"))) == 3


def test_env_overrides(monkeypatch):
    monkeypatch.setenv(archive.ENV_MAX_FILES, "7")
    monkeypatch.setenv(archive.ENV_MAX_TOTAL_MB, "3")
    assert archive.max_files() == 7
    assert archive.max_total_bytes() == 3 * 1024 * 1024

    monkeypatch.setenv(archive.ENV_MAX_FILES, "0")
    assert archive.max_files() == archive.DEFAULT_MAX_FILES


def test_result_csv_round_trip_keeps_types(tmp_path):
    """保存した csv を読み戻したとき、書き出したときと同じ型に戻ること。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    samples = [{"num_clients": 5, "util_24": 10, "util_5": 20, "util_6": 30}] * 12
    S.write_metrics(logs / "ap_metrics_20260101_1000_TZT.csv", S.series(START, samples, ap=S.AP1))
    S.write_events(logs / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=7), pre_channel=36, channel=44, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=17), pre_channel=44, channel=44, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=27), pre_channel=44, channel=48, ap=S.AP2),
    ])
    out = tmp_path / "out"
    assert cli.main(["analyze", "--logs", str(logs), "--out", str(out)]) == cli.EXIT_OK

    written = next(out.glob("*.csv"))
    restored = analysis.read_result_csv(written)
    assert list(restored.columns) == list(analysis.RESULT_COLUMNS)
    assert restored["channel_changed"].tolist() == [True, False, True]
    assert restored["contaminated"].dtype == bool
    assert str(restored["pre_channel"].dtype) == "Int64"
    assert str(restored["impact_clients"].dtype) == "Int64"
    # ap_metrics にサンプルが無い AP は照合不可（値は空のまま読み戻る）
    assert restored.loc[2, "match_status"] == "no_ap"
    assert pd.isna(restored.loc[2, "clients_before"])
    assert restored.loc[0, "clients_before"] == 5
