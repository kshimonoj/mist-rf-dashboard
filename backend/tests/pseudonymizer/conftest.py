import csv
import os
import shutil

import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

ALL_FIXTURES = (
    "ap_metrics_20240101_0900_TZT.csv",
    "ap_events_20240101_0900_TZT.csv",
    "client_metrics_20240101_0900_TZT.csv",
    "sle_metrics_20240101_0900_TZT.csv",
    "floormap_20240101_0900_TZT_summary.csv",
)


@pytest.fixture
def indir(tmp_path):
    """合成フィクスチャを全部コピーした入力ディレクトリ。"""
    d = tmp_path / "in"
    d.mkdir()
    for name in ALL_FIXTURES:
        shutil.copy(os.path.join(FIXTURES_DIR, name), d / name)
    return d


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
