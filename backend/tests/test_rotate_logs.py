"""rotate_logs の削除対象・削除条件を固定するテスト。

合成データのみを使う。実データ・実データ由来の値は一切扱わない。

背景（実際に障害を起こした欠陥）: 旧実装はサイズ判定を data/logs の全ファイルで
行う一方、削除できるのは Snapshot テーブルに登録される ap_metrics だけだった。
他種別が容量を占めた状態でキャップを 2MB 超えただけで ap_metrics が全滅した。

現行実装は判定対象と削除対象を一致させる（どちらも LOGS_DIR 直下の全ファイル）。
このテストはその性質と、既定 dry-run（1件も削除しない）を固定する。
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

#: data/logs 直下に置かれる 6 種別（ファイル名の接頭辞）
KINDS = ("ap_metrics", "sle_metrics", "client_metrics", "floormap", "ap_events", "rf_neighbors")


@pytest.fixture
def rotate_env(tmp_path, monkeypatch):
    """rotate_logs 用に隔離した DB とログディレクトリを用意する。

    環境変数はホスト側の設定が漏れないよう毎回消す（既定値の挙動を試すため）。
    """
    logs_dir = tmp_path / "logs"
    os.makedirs(logs_dir)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(scheduler, "LOGS_DIR", str(logs_dir))
    monkeypatch.delenv(scheduler.ENV_ROTATE_DRY_RUN, raising=False)
    monkeypatch.delenv(scheduler.ENV_LOG_MAX_TOTAL_MB, raising=False)

    return logs_dir, Session


@pytest.fixture
def live_run(monkeypatch):
    """dry-run を解除する（実際に削除させるテスト用）。"""
    monkeypatch.setenv(scheduler.ENV_ROTATE_DRY_RUN, "0")


def _write_file(logs_dir, name: str, size_bytes: int, *, age_hours: float = 0.0) -> None:
    """指定サイズ・指定の古さ（mtime）でファイルを作る。"""
    path = logs_dir / name
    with open(path, "wb") as f:
        f.write(b"x" * size_bytes)
    mtime = (NOW - timedelta(hours=age_hours)).timestamp()
    os.utime(path, (mtime, mtime))


def _cap_mb(monkeypatch, mb: float) -> None:
    monkeypatch.setenv(scheduler.ENV_LOG_MAX_TOTAL_MB, str(mb))


def _cap_bytes(monkeypatch, num_bytes: float) -> None:
    monkeypatch.setenv(scheduler.ENV_LOG_MAX_TOTAL_MB, str(num_bytes / 1024 / 1024))


def _add_snapshot(Session, filename: str, saved_at: datetime) -> None:
    db = Session()
    db.add(Snapshot(filename=filename, saved_at=saved_at, triggered_by="auto",
                    site_count=1, ap_count=10))
    db.commit()
    db.close()


def _files(logs_dir) -> list[str]:
    return sorted(f for f in os.listdir(logs_dir) if os.path.isfile(logs_dir / f))


def _files_of_kind(logs_dir, kind: str) -> list[str]:
    return sorted(f for f in _files(logs_dir) if scheduler._log_kind(f) == kind)


def _snapshot_filenames(Session) -> list[str]:
    db = Session()
    try:
        return sorted(s.filename for s in db.query(Snapshot).all())
    finally:
        db.close()


def _fill_one_kind(logs_dir, kind: str, count: int, size: int, *, oldest_age_hours: float):
    """同一種別のファイルを count 件、古い順に作る。作った順（古い順）で返す。"""
    names = []
    for i in range(count):
        name = f"{kind}_2026081{i % 10}_{1200 + i:04d}_JST.csv"
        _write_file(logs_dir, name, size, age_hours=oldest_age_hours - i)
        names.append(name)
    return names


# ---------------------------------------------------------------------------
# 要件 1: 既定は dry-run。環境変数なしでは 1 件も削除されない（必須）
# ---------------------------------------------------------------------------


def test_dry_run_is_the_default(rotate_env, monkeypatch, caplog):
    """キャップ超過かつ保持日数超えでも、既定では 1 件も消えない。"""
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 100)
    for kind in KINDS:
        _fill_one_kind(logs_dir, kind, 3, 500, oldest_age_hours=24 * 90)
    before = _files(logs_dir)

    with caplog.at_level(logging.INFO, logger="scheduler"):
        scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == before, "既定（dry-run）では 1 件も削除されない"
    messages = [r.message for r in caplog.records]
    assert any("[ROTATE][DRY-RUN]" in m and "would delete" in m for m in messages)
    assert any("age>30d:" in m and "size cap:" in m for m in messages), "削除理由の内訳が出る"
    assert not any(m.startswith("[ROTATE] deleted") for m in messages), "実削除のログは出ない"


def test_dry_run_default_keeps_snapshot_rows(rotate_env, monkeypatch):
    """dry-run では Snapshot 行も消さない（DB も無変更）。"""
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 100)
    names = _fill_one_kind(logs_dir, "ap_metrics", 3, 500, oldest_age_hours=24 * 90)
    for i, name in enumerate(names):
        _add_snapshot(Session, name, NOW - timedelta(days=90 - i))

    scheduler.rotate_logs(retention_days=30)

    assert _snapshot_filenames(Session) == sorted(names)


# ---------------------------------------------------------------------------
# 要件 2: LOG_ROTATE_DRY_RUN=0 で実際に削除される
# ---------------------------------------------------------------------------


def test_dry_run_disabled_actually_deletes(rotate_env, live_run, monkeypatch, caplog):
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_mb(monkeypatch, 10)  # サイズは十分。年齢基準だけを効かせる
    names = _fill_one_kind(logs_dir, "ap_metrics", 3, 100, oldest_age_hours=24 * 90)

    with caplog.at_level(logging.INFO, logger="scheduler"):
        scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == [names[-1]], "最新 1 件だけが残る"
    messages = [r.message for r in caplog.records]
    assert any(m.startswith("[ROTATE] deleted 2 files") for m in messages)


# ---------------------------------------------------------------------------
# 要件 3: ap_metrics 以外の 5 種別も削除対象になる
# ---------------------------------------------------------------------------


def test_every_kind_is_deletable(rotate_env, live_run, monkeypatch):
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_mb(monkeypatch, 10)
    for kind in KINDS:
        _fill_one_kind(logs_dir, kind, 3, 100, oldest_age_hours=24 * 90)

    scheduler.rotate_logs(retention_days=30)

    for kind in KINDS:
        assert len(_files_of_kind(logs_dir, kind)) == 1, f"{kind} も削除対象になる"


# ---------------------------------------------------------------------------
# 要件 4: 削除順は mtime 昇順（古いものから）
# ---------------------------------------------------------------------------


def test_deletes_oldest_first(rotate_env, live_run, monkeypatch):
    """名前順と mtime 順が逆のファイル群でも、消えるのは mtime が古い方。"""
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 250)
    # 名前の昇順 = a, b, c だが、mtime は c が最も古い
    _write_file(logs_dir, "ap_metrics_20260810_1200_JST.csv", 100, age_hours=2)
    _write_file(logs_dir, "ap_metrics_20260811_1200_JST.csv", 100, age_hours=1)
    _write_file(logs_dir, "ap_metrics_20260812_1200_JST.csv", 100, age_hours=10)

    scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == [
        "ap_metrics_20260810_1200_JST.csv",
        "ap_metrics_20260811_1200_JST.csv",
    ], "mtime が最も古い 20260812 のファイルが最初に消える"


# ---------------------------------------------------------------------------
# 要件 5: 年齢基準は種別を問わず効く
# ---------------------------------------------------------------------------


def test_age_based_deletion_covers_every_kind(rotate_env, live_run, monkeypatch, caplog):
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_mb(monkeypatch, 10)  # キャップは効かせない
    for kind in KINDS:
        # 古い 2 件（保持日数超え）と新しい 1 件
        _write_file(logs_dir, f"{kind}_20260101_1200_JST.csv", 10, age_hours=24 * 90)
        _write_file(logs_dir, f"{kind}_20260102_1200_JST.csv", 10, age_hours=24 * 60)
        _write_file(logs_dir, f"{kind}_20260815_1200_JST.csv", 10, age_hours=1)

    with caplog.at_level(logging.INFO, logger="scheduler"):
        scheduler.rotate_logs(retention_days=30)

    for kind in KINDS:
        assert _files_of_kind(logs_dir, kind) == [f"{kind}_20260815_1200_JST.csv"]
    assert any(f"age>30d: {2 * len(KINDS)}" in r.message for r in caplog.records)


def test_files_within_retention_are_kept(rotate_env, live_run, monkeypatch):
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_mb(monkeypatch, 10)
    for kind in KINDS:
        _fill_one_kind(logs_dir, kind, 3, 100, oldest_age_hours=24 * 5)
    before = _files(logs_dir)

    scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == before


# ---------------------------------------------------------------------------
# 要件 6: サイズキャップは下回ったら止まる
# ---------------------------------------------------------------------------


def test_size_cap_stops_once_under(rotate_env, live_run, monkeypatch, caplog):
    """合計 600B / cap 350B。古い方から 3 件消した時点で 300B となり止まる。"""
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 350)
    names = _fill_one_kind(logs_dir, "sle_metrics", 6, 100, oldest_age_hours=6)

    with caplog.at_level(logging.INFO, logger="scheduler"):
        scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == sorted(names[3:]), "キャップを下回った時点で削除は止まる"
    assert any("size cap: 3" in r.message for r in caplog.records)


def test_size_cap_default_is_far_above_current_usage(rotate_env, live_run, monkeypatch):
    """LOG_MAX_TOTAL_MB 未設定なら既定 5000MB。数百MB程度では何も消えない。"""
    logs_dir, Session = rotate_env
    assert scheduler.DEFAULT_LOG_MAX_TOTAL_MB >= 5000
    assert scheduler._log_max_total_bytes() == scheduler.DEFAULT_LOG_MAX_TOTAL_MB * 1024 * 1024
    names = _fill_one_kind(logs_dir, "ap_metrics", 3, 1000, oldest_age_hours=3)

    scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == sorted(names)


# ---------------------------------------------------------------------------
# 要件 7: 上限を極端に小さくしても各種別の最新 1 件は残る
# ---------------------------------------------------------------------------


def test_newest_file_of_each_kind_always_survives(rotate_env, live_run, monkeypatch, caplog):
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 1)  # 事実上ゼロ
    newest = {}
    for kind in KINDS:
        names = _fill_one_kind(logs_dir, kind, 4, 1000, oldest_age_hours=24 * 90)
        newest[kind] = names[-1]

    with caplog.at_level(logging.WARNING, logger="scheduler"):
        scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == sorted(newest.values()), "各種別の最新 1 件だけが残る"
    assert any("Size cap still exceeded" in r.message for r in caplog.records)


def test_min_keep_per_kind_applies_to_all_kinds(rotate_env, live_run, monkeypatch):
    """既定のフロア（直近 10 件）は ap_metrics 限定ではなく全種別に効く。"""
    logs_dir, Session = rotate_env
    assert scheduler._MIN_KEEP_PER_KIND == 10
    _cap_bytes(monkeypatch, 1)
    for kind in KINDS:
        _fill_one_kind(logs_dir, kind, 13, 100, oldest_age_hours=24 * 90)

    scheduler.rotate_logs(retention_days=30)

    for kind in KINDS:
        assert len(_files_of_kind(logs_dir, kind)) == 10, f"{kind} も直近 10 件は残る"


# ---------------------------------------------------------------------------
# 要件 8: ap_metrics を削除したら Snapshot 行も消える
# ---------------------------------------------------------------------------


def test_snapshot_rows_follow_deleted_ap_metrics(rotate_env, live_run, monkeypatch):
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_mb(monkeypatch, 10)
    names = _fill_one_kind(logs_dir, "ap_metrics", 3, 100, oldest_age_hours=24 * 90)
    for i, name in enumerate(names):
        _add_snapshot(Session, name, NOW - timedelta(days=90 - i))

    scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == [names[-1]]
    assert _snapshot_filenames(Session) == [names[-1]], "残ったファイルの行だけが残る"


# ---------------------------------------------------------------------------
# 要件 9: Snapshot 行が無いファイルも削除対象（今回の修正の要点）
# ---------------------------------------------------------------------------


def test_files_without_snapshot_rows_are_deleted(rotate_env, live_run, monkeypatch):
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_mb(monkeypatch, 10)
    # Snapshot に登録されない ap_metrics（登録前に落ちた等）と、他種別
    _fill_one_kind(logs_dir, "ap_metrics", 3, 100, oldest_age_hours=24 * 90)
    _fill_one_kind(logs_dir, "client_metrics", 3, 100, oldest_age_hours=24 * 90)
    assert _snapshot_filenames(Session) == []

    scheduler.rotate_logs(retention_days=30)

    assert len(_files_of_kind(logs_dir, "ap_metrics")) == 1
    assert len(_files_of_kind(logs_dir, "client_metrics")) == 1


# ---------------------------------------------------------------------------
# 要件 10: サブディレクトリは対象外（必須）
# ---------------------------------------------------------------------------


def test_subdirectory_files_are_never_touched(rotate_env, live_run, monkeypatch):
    """data/hangap_results/ 相当のサブディレクトリは走査も削除もしない。"""
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 1)
    sub = logs_dir / "hangap_results"
    os.makedirs(sub)
    _write_file(sub, "hangap_result_20260101_120000.csv", 5000, age_hours=24 * 365)
    _write_file(sub, "hangap_result_20260101_120000.json", 5000, age_hours=24 * 365)
    _fill_one_kind(logs_dir, "ap_metrics", 3, 100, oldest_age_hours=24 * 90)

    scheduler.rotate_logs(retention_days=30)

    assert sorted(os.listdir(sub)) == [
        "hangap_result_20260101_120000.csv",
        "hangap_result_20260101_120000.json",
    ], "サブディレクトリのファイルは削除されない"
    assert os.path.isdir(sub), "サブディレクトリ自体も消えない"


def test_subdirectory_size_is_not_counted(rotate_env, live_run, monkeypatch):
    """サブディレクトリの容量は合計にも入れない（判定対象と削除対象を一致させる）。"""
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 1000)
    sub = logs_dir / "hangap_results"
    os.makedirs(sub)
    _write_file(sub, "hangap_result_20260101_120000.csv", 5000, age_hours=1)
    names = _fill_one_kind(logs_dir, "ap_metrics", 3, 100, oldest_age_hours=3)

    scheduler.rotate_logs(retention_days=30)

    assert _files(logs_dir) == sorted(names), "直下の合計 300B のみで判定される"


# ---------------------------------------------------------------------------
# 要件 11: 旧障害（削除できないファイルが容量を占める）の再現防止
# ---------------------------------------------------------------------------


def test_other_kind_hogging_space_no_longer_wipes_ap_metrics(rotate_env, live_run, monkeypatch):
    """旧障害の再現シナリオ。

    ap_metrics 5 件（各 100B）+ 巨大な client_metrics（1200B）で cap 1000B 超過。
    旧実装ではこの状況で ap_metrics だけが削除対象となり全滅した（応急処置後は
    1 件も削除できず、キャップが恒久的に無効化された）。現行実装は容量を占めて
    いる client_metrics 自体を削除できるので、ap_metrics は 1 件も減らない。
    """
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 1000)
    ap_names = []
    for i in range(5):
        name = f"ap_metrics_2026081{i}_1200_JST.csv"
        _write_file(logs_dir, name, 100, age_hours=5 - i)
        _add_snapshot(Session, name, NOW - timedelta(hours=5 - i))
        ap_names.append(name)
    _write_file(logs_dir, "client_metrics_20260814_1200_JST.csv", 1200, age_hours=10)
    _write_file(logs_dir, "client_metrics_20260815_1200_JST.csv", 100, age_hours=1)

    scheduler.rotate_logs(retention_days=30)

    assert _files_of_kind(logs_dir, "ap_metrics") == sorted(ap_names), "ap_metrics は全件残る"
    assert _files_of_kind(logs_dir, "client_metrics") == [
        "client_metrics_20260815_1200_JST.csv"
    ], "容量を占めていた側が削除される"
    assert _snapshot_filenames(Session) == sorted(ap_names)


def test_unknown_filenames_are_counted_and_deletable(rotate_env, live_run, monkeypatch):
    """種別に当てはまらないファイルも「数えるなら消せる」側に置く。

    合計に入るのに削除できないファイルを 1 つでも作ると、旧障害と同じ
    「下回れない超過」が再発する。
    """
    logs_dir, Session = rotate_env
    monkeypatch.setattr(scheduler, "_MIN_KEEP_PER_KIND", 1)
    _cap_bytes(monkeypatch, 500)
    _write_file(logs_dir, "unexpected_old.txt", 900, age_hours=48)
    _write_file(logs_dir, "unexpected_new.txt", 100, age_hours=1)
    names = _fill_one_kind(logs_dir, "ap_metrics", 2, 100, oldest_age_hours=3)

    scheduler.rotate_logs(retention_days=30)

    assert "unexpected_old.txt" not in _files(logs_dir)
    assert "unexpected_new.txt" in _files(logs_dir), "種別不明でも最新 1 件は残す"
    assert _files_of_kind(logs_dir, "ap_metrics") == sorted(names)


# ---------------------------------------------------------------------------
# 環境変数の読み取り
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("0", False), ("false", False), ("no", False), ("off", False), ("FALSE", False),
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("", True), ("nonsense", True),
])
def test_dry_run_env_parsing(rotate_env, monkeypatch, value, expected):
    monkeypatch.setenv(scheduler.ENV_ROTATE_DRY_RUN, value)
    assert scheduler._rotate_dry_run() is expected


@pytest.mark.parametrize("value", ["", "abc", "0", "-1"])
def test_invalid_max_total_mb_falls_back_to_default(rotate_env, monkeypatch, value):
    monkeypatch.setenv(scheduler.ENV_LOG_MAX_TOTAL_MB, value)
    assert scheduler._log_max_total_bytes() == scheduler.DEFAULT_LOG_MAX_TOTAL_MB * 1024 * 1024


def test_missing_logs_dir_is_a_noop(rotate_env, live_run, monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "LOGS_DIR", str(tmp_path / "does-not-exist"))
    scheduler.rotate_logs(retention_days=30)  # 例外を出さない


def test_ap_events_backfill_is_the_same_kind_as_ap_events():
    assert scheduler._log_kind("ap_events_backfill_20260815_1200_JST.csv") == "ap_events"
    assert scheduler._log_kind("ap_events_20260815_1200_JST.csv") == "ap_events"
    assert scheduler._log_kind("floormap_20260815_1200_JST_summary.csv") == "floormap"
    assert scheduler._log_kind("whatever.txt") == scheduler._OTHER_KIND
