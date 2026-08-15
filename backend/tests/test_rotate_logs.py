"""rotate_logs のサイズキャップ挙動を固定するテスト。

合成データのみを使う。実データ・実データ由来の値は一切扱わない。

背景: Snapshot テーブルに登録されるのは ap_metrics の CSV のみだが、サイズ
判定の対象は data/logs 配下の全ファイルの合計。合計がキャップを超えても
ap_metrics 以外（client_metrics 等）は削除対象にできないため、ap_metrics を
全部消してもキャップを下回れないケースがある。そのケースで ap_metrics を
全滅させず、削除を見送って warning を出すことを固定する。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import scheduler
from models import Base, Snapshot

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def rotate_env(tmp_path, monkeypatch):
    """rotate_logs 用に隔離した DB とログディレクトリを用意する。"""
    logs_dir = tmp_path / "logs"
    os.makedirs(logs_dir)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(scheduler, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(scheduler, "_MAX_TOTAL_BYTES", 1000)
    monkeypatch.setattr(scheduler, "_MIN_KEEP_SNAPSHOTS", 2)

    return logs_dir, Session


def _write_file(logs_dir, name: str, size_bytes: int) -> None:
    with open(logs_dir / name, "wb") as f:
        f.write(b"x" * size_bytes)


def _add_snapshot(Session, filename: str, saved_at: datetime) -> None:
    db = Session()
    db.add(Snapshot(filename=filename, saved_at=saved_at, triggered_by="auto",
                     site_count=1, ap_count=10))
    db.commit()
    db.close()


def _ap_metrics_files(logs_dir) -> list[str]:
    return sorted(f for f in os.listdir(logs_dir) if f.startswith("ap_metrics"))


def _other_files(logs_dir) -> list[str]:
    return sorted(f for f in os.listdir(logs_dir) if not f.startswith("ap_metrics") and f != "test.db")


def _snapshot_count(Session) -> int:
    db = Session()
    try:
        return db.query(Snapshot).count()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. キャップ未満では何もしない
# ---------------------------------------------------------------------------


def test_under_cap_deletes_nothing(rotate_env):
    logs_dir, Session = rotate_env
    for i in range(3):
        fn = f"ap_metrics_2026081{i}_1200_JST.csv"
        _write_file(logs_dir, fn, 100)
        _add_snapshot(Session, fn, NOW - timedelta(hours=3 - i))

    scheduler.rotate_logs(retention_days=30)

    assert len(_ap_metrics_files(logs_dir)) == 3
    assert _snapshot_count(Session) == 3


# ---------------------------------------------------------------------------
# 2. 削除可能分を全部消してもキャップを下回れないとき、1件も削除せず warning
# ---------------------------------------------------------------------------


def test_undeletable_excess_deletes_nothing_and_warns(rotate_env, caplog):
    """cap=1000。ap_metrics 5件(各100B=500B)を全部消しても、他種別1件(900B)が
    残るため合計900B <= 1000B ... となってしまうと下回れてしまうので、
    他種別を1200Bにして「全部消しても超過したまま」を作る。"""
    logs_dir, Session = rotate_env
    for i in range(5):
        fn = f"ap_metrics_2026081{i}_1200_JST.csv"
        _write_file(logs_dir, fn, 100)
        _add_snapshot(Session, fn, NOW - timedelta(hours=5 - i))
    # Snapshot に登録されない他種別。これ単体でキャップ(1000B)を超過させる。
    _write_file(logs_dir, "client_metrics_20260815_1200_JST.csv", 1200)

    with caplog.at_level(logging.WARNING, logger="scheduler"):
        scheduler.rotate_logs(retention_days=30)

    assert len(_ap_metrics_files(logs_dir)) == 5, "ap_metrics は1件も削除されない"
    assert _snapshot_count(Session) == 5
    assert any("Size cap exceeded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 3. 直近 _MIN_KEEP_SNAPSHOTS 件は削除されない
# ---------------------------------------------------------------------------


def test_recent_min_keep_snapshots_are_preserved(rotate_env):
    """cap=1000, MIN_KEEP=2。ap_metrics 6件(各200B=1200B合計)で他種別なし。
    キャップを下回るには古い方から削除していくが、直近2件は候補から除外される
    ため、最悪でも直近2件(400B)は必ず残る。"""
    logs_dir, Session = rotate_env
    filenames = []
    for i in range(6):
        fn = f"ap_metrics_2026081{i}_1200_JST.csv"
        _write_file(logs_dir, fn, 200)
        _add_snapshot(Session, fn, NOW - timedelta(hours=6 - i))
        filenames.append(fn)

    scheduler.rotate_logs(retention_days=30)

    remaining = set(_ap_metrics_files(logs_dir))
    newest_two = set(filenames[-2:])
    assert newest_two.issubset(remaining), "直近2件は必ず残る"
    assert _snapshot_count(Session) == len(remaining)


# ---------------------------------------------------------------------------
# 4. ap_metrics 以外の種別が削除されない（現状の挙動の固定）
# ---------------------------------------------------------------------------


def test_non_ap_metrics_files_are_never_deleted(rotate_env):
    """他種別ファイルはキャップ超過の起点になっても、削除対象にはならない。"""
    logs_dir, Session = rotate_env
    for i in range(5):
        fn = f"ap_metrics_2026081{i}_1200_JST.csv"
        _write_file(logs_dir, fn, 50)
        _add_snapshot(Session, fn, NOW - timedelta(hours=5 - i))
    _write_file(logs_dir, "client_metrics_20260815_1200_JST.csv", 900)

    scheduler.rotate_logs(retention_days=30)

    assert _other_files(logs_dir) == ["client_metrics_20260815_1200_JST.csv"]
