"""レポートのテスト用に「保存済みの分析結果」を合成する。

各モジュールの ``archive`` が保存する形（``<name>.csv`` + ``<name>.json``）を
そのまま作る。**実データは一切使わない。** AP 名は ``TEST-AP-01``、MAC は
``aabbccddee01``、サイトは ``test-site`` のように、実データと誤認しようがない
値だけを使う。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Sequence

from floorpeak.analysis import RESULT_COLUMNS as FP_COLUMNS
from hangap.detector import RESULT_COLUMNS as HA_COLUMNS
from rrm.analysis import RESULT_COLUMNS as RRM_COLUMNS

HANGAP_NAME = "hangap_result_20260101_010101"
FLOORPEAK_NAME = "floorpeak_result_20260102_020202"
RRM_NAME = "rrm_result_20260103_030303"


def _write_set(
    results_dir: Path, name: str, columns: Sequence[str], rows: Sequence[dict[str, Any]],
    meta: dict[str, Any],
) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / f"{name}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})
    (results_dir / f"{name}.json").write_text(
        json.dumps({**meta, "name": name}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return csv_path


# ---------------------------------------------------------------------------
# Hang AP
# ---------------------------------------------------------------------------


def hangap_rows(count: int = 3) -> list[dict[str, Any]]:
    """連続ゼロ回数が 1 件ずつ違う行（上位 N 件の並べ替えを確かめられるように）。"""
    return [
        {
            "ap_name": f"TEST-AP-{i:02d}",
            "site_name": "test-site",
            "区間番号": i,
            "AP内区間数": 1,
            "ゼロ開始": f"2026-01-01 0{i}:00:00",
            "ゼロ終了": f"2026-01-01 0{i}:30:00",
            "連続ゼロ回数": 10 * i,
            "回復状況": "回復",
            "周辺AP判定": "周辺に端末あり",
            "退場疑い": "False",
            "直前clients": i,
            "AP最大clients": 20 + i,
        }
        for i in range(1, count + 1)
    ]


def write_hangap(
    results_dir: Path, *, name: str = HANGAP_NAME, rows: Sequence[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    body = list(rows) if rows is not None else hangap_rows()
    _write_set(
        results_dir, name, HA_COLUMNS, body,
        {
            "version": 1,
            "saved_at": "2026-01-01T01:01:01Z",
            "detected_intervals": len(body),
            "recovery_status": {"回復": len(body), "継続中": 0},
            "neighbor_verdict": {"周辺に端末あり": len(body)},
            "exodus_suspected": 0,
            "event_matched_intervals": 1,
            "condition_text": "分析条件: test",
            "metrics_period": ["2026-01-01 00:00:00", "2026-01-01 12:00:00"],
            "events_period": ["2026-01-01 00:00:00", "2026-01-01 12:00:00"],
            "ap_count": 2,
            "files_scanned": 4,
            "warnings": [],
            **(meta or {}),
        },
    )
    return name


# ---------------------------------------------------------------------------
# Floor Peak
# ---------------------------------------------------------------------------

FLOOR_1F = "TEST-FLOOR-1F"
FLOOR_2F = "TEST-FLOOR-2F"


def floorpeak_rows(per_floor: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for floor_index, floor in enumerate((FLOOR_1F, FLOOR_2F), start=1):
        for rank in range(1, per_floor + 1):
            index = floor_index * 10 + rank
            rows.append({
                "ap_name": f"TEST-AP-{index:02d}",
                "mac": f"aabbccddee{index:02d}",
                "model": "AP45" if rank % 2 else "AP47",
                "num_clients": per_floor - rank + 1,
                "status": "connected",
                "map_id": f"test-map-{floor_index}",
                "map_name": floor,
                "x_m": "",
                "y_m": "",
                "rank_in_floor": rank,
            })
    return rows


def write_floorpeak(
    results_dir: Path, *, name: str = FLOORPEAK_NAME,
    rows: Sequence[dict[str, Any]] | None = None, meta: dict[str, Any] | None = None,
) -> str:
    body = list(rows) if rows is not None else floorpeak_rows()
    floors = []
    for floor in dict.fromkeys(r["map_name"] for r in body):
        members = [r for r in body if r["map_name"] == floor]
        floors.append({
            "map_name": floor,
            "ap_count": len(members),
            "num_clients": sum(int(r["num_clients"]) for r in members),
        })
    _write_set(
        results_dir, name, FP_COLUMNS, body,
        {
            "version": 1,
            "saved_at": "2026-01-02T02:02:02Z",
            "site_id": "test-site-id",
            "site_name": "test-site",
            "site_label": "test-site [test-site-id]",
            "selected_by": "auto",
            "peak_time": "2026-01-02 12:00:00",
            "peak_total_clients": sum(f["num_clients"] for f in floors),
            "floormap_file": "floormap_20260102_1200_TZT_summary.csv",
            "ap_count": len(body),
            "floor_count": len(floors),
            "floors": floors,
            "default_floor": floors[0]["map_name"] if floors else None,
            "top_n": 20,
            "model_colors": {"AP45": "1F77B4", "AP47": "D62728"},
            "default_model_color": "9E9E9E",
            "condition_text": "分析条件: test",
            "warnings": [],
            **(meta or {}),
        },
    )
    return name


# ---------------------------------------------------------------------------
# RRM
# ---------------------------------------------------------------------------

CLASSIFICATIONS = ("RADAR", "POST_RADAR", "RRM")


def rrm_rows(count: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "event_timestamp": f"2026-01-03 0{i}:00:00",
            "classification": CLASSIFICATIONS[(i - 1) % len(CLASSIFICATIONS)],
            "reason": "scheduled-site-rrm",
            "site_name": "test-site",
            "ap_name": f"TEST-AP-{i:02d}",
            "ap_mac": f"aabbccddee{i:02d}",
            "band": "5",
            "pre_channel": 36,
            "post_channel": 44,
            "channel_changed": "True",
            "match_status": "ok",
            "contaminated": "False",
            "impact_clients": i,
        }
        for i in range(1, count + 1)
    ]


def rrm_hourly(buckets: int = 6) -> list[dict[str, Any]]:
    """1 時間バケットの並び。**グラフの系列数・データ点数の期待値になる。**"""
    return [
        {
            "bucket": f"2026-01-03 {h:02d}:00:00",
            "changes_RADAR": h % 3,
            "impact_RADAR": h,
            "changes_POST_RADAR": h % 2,
            "impact_POST_RADAR": h,
            "changes_RRM": (h + 1) % 4,
            "impact_RRM": h,
            "changes_total": h % 3 + h % 2 + (h + 1) % 4,
            "impact_total": 3 * h,
        }
        for h in range(buckets)
    ]


def write_rrm(
    results_dir: Path, *, name: str = RRM_NAME, rows: Sequence[dict[str, Any]] | None = None,
    hourly: Sequence[dict[str, Any]] | None = None, meta: dict[str, Any] | None = None,
) -> str:
    body = list(rows) if rows is not None else rrm_rows()
    buckets = list(hourly) if hourly is not None else rrm_hourly()
    _write_set(
        results_dir, name, RRM_COLUMNS, body,
        {
            "version": 1,
            "saved_at": "2026-01-03T03:03:03Z",
            "site_names": ["test-site"],
            "site_labels": ["test-site [test-site-id]"],
            "window_start": None,
            "window_end": None,
            "bucket_seconds": 3600,
            "event_count": len(body),
            "change_count": len(body),
            "noop_count": 0,
            "unmatched_count": 0,
            "contaminated_count": 0,
            "impact_total": sum(int(r["impact_clients"]) for r in body),
            "changes_by_class": {name_: 1 for name_ in CLASSIFICATIONS},
            "noop_by_class": {name_: 0 for name_ in CLASSIFICATIONS},
            "radar_detected": 1,
            "radar_with_change": 1,
            "radar_without_action": 0,
            "hourly": buckets,
            "by_classification": [
                {
                    "classification": name_, "events": 1, "changes": 1, "noop": 0,
                    "unknown_channel": 0, "impact_total": index + 1, "impact_avg": 1.0,
                    "contaminated": 0, "unmatched": 0,
                }
                for index, name_ in enumerate(CLASSIFICATIONS)
            ],
            "by_site": [],
            "by_ap": [
                {
                    "site_name": "test-site", "ap_name": f"TEST-AP-{i:02d}",
                    "ap_mac": f"aabbccddee{i:02d}", "changes": 1, "impact_total": i,
                }
                for i in range(1, len(body) + 1)
            ],
            "classifications": list(CLASSIFICATIONS),
            "class_colors": {"RADAR": "D32F2F", "POST_RADAR": "F57C00", "RRM": "1976D2"},
            "condition_text": "分析条件: test",
            "warnings": [],
            **(meta or {}),
        },
    )
    return name


def dirs_under(root: Path):
    """``ResultsDirs`` を作る（``root`` の下に 3 つの保存先を置く）。"""
    from report.analysis import ResultsDirs

    return ResultsDirs.under(root)
