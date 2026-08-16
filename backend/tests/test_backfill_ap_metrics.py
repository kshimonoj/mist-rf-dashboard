"""指示 19: DB から ap_metrics の CSV ログを再生成する運用ツールのテスト。

合成データのみを使う。実データ・実データ由来の値は一切扱わない。

特に外せないのが 2 点:

- **タイムゾーン変換**（DB=UTC / CSV=現地時刻）。誤ると 9 時間ずれ、
  分析の窓指定が全く合わなくなる。
- **列構成**。ローダはヘッダー完全一致で種別を判定するため、
  1 列でもずれると ``ap_metrics`` と認識されず読めなくなる。
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backfill import ap_metrics as backfill
from models import ApEvent, ApMetrics, AppSettings, Base, Snapshot
from pseudonymizer.schemas import detect_file_type
from scheduler import ALL_CSV_COLUMNS

SITE_ID = "test-site-id-0001"
SITE_NAME = "TestSite"
SITE_ID_2 = "test-site-id-0002"
SITE_NAME_2 = "TestSite2"

#: JST 12:00:39 に相当する UTC。9 時間ずれを検出できるよう、正時ちょうどは避ける
BASE_UTC = datetime(2026, 8, 9, 3, 0, 39)


def _make_db(path, rows, *, tz: str | None = "Asia/Tokyo", with_events: bool = True):
    """合成 ap_metrics を持つ SQLite ファイルを作る。"""
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        if tz is not None:
            db.add(AppSettings(id=1, timezone=tz))
        if with_events:
            db.add(ApEvent(
                event_timestamp=BASE_UTC, site_id=SITE_ID, site_name=SITE_NAME,
                ap_mac="aabbccddee01", event_type="AP_CONNECTED",
            ))
            db.add(ApEvent(
                event_timestamp=BASE_UTC, site_id=SITE_ID_2, site_name=SITE_NAME_2,
                ap_mac="aabbccddee02", event_type="AP_CONNECTED",
            ))
        for r in rows:
            db.add(ApMetrics(**r))
        db.commit()
    finally:
        db.close()
    return Session


def _metric(ts: datetime, *, ap: str = "0001", site_id: str = SITE_ID, **extra) -> dict:
    row = dict(
        site_id=site_id,
        ap_id=f"test-ap-{ap}",
        ap_name=f"TEST-AP-{ap}",
        model="AP-TEST",
        mac=f"aabbccddee{ap[-2:]}",
        timestamp=ts,
        num_clients=3,
        status="connected",
        radio_24_channel=1,
        radio_5_channel=36,
        radio_5_utilization=10.5,
    )
    row.update(extra)
    return row


@pytest.fixture
def env(tmp_path, monkeypatch):
    """読み込み元 DB・出力先ログディレクトリ・snapshots 登録先を隔離する。

    ``snapshots`` の登録先は「稼働中の DB」（``backfill.SessionLocal``）なので、
    テストでも別 DB として明示的に差し替える。
    """
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    live_engine = create_engine(
        f"sqlite:///{tmp_path / 'live.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=live_engine)
    LiveSession = sessionmaker(autocommit=False, autoflush=False, bind=live_engine)
    monkeypatch.setattr(backfill, "SessionLocal", LiveSession)
    return tmp_path, logs_dir, LiveSession


def _run(env, rows, *, write=True, db_name="source.db", **kwargs):
    tmp_path, logs_dir, _ = env
    db_path = tmp_path / db_name
    if not db_path.exists():
        _make_db(db_path, rows)
    return backfill.backfill(
        db_path=str(db_path), logs_dir=str(logs_dir), write=write, **kwargs
    )


def _read_csv(path) -> tuple[list[str], list[dict]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _snapshots(LiveSession) -> list[Snapshot]:
    db = LiveSession()
    try:
        return db.query(Snapshot).order_by(Snapshot.filename).all()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 要件 1: タイムゾーン変換（DB=UTC / CSV=JST）
# ---------------------------------------------------------------------------


def test_utc_rows_become_jst_filename_and_timestamp(env):
    """UTC 03:00:39 の行は JST 12:00:39 として、12〜13 時のファイルに入る。"""
    _, logs_dir, _ = env
    _run(env, [_metric(BASE_UTC)])

    # 自動保存と同じく、ファイル名の時刻は「対象期間の終端」
    path = logs_dir / "ap_metrics_20260809_1300_JST.csv"
    assert path.exists(), sorted(p.name for p in logs_dir.iterdir())

    _, rows = _read_csv(path)
    assert [r["timestamp"] for r in rows] == ["2026-08-09 12:00:39"]


def test_utc_date_rollover_uses_local_date(env):
    """UTC 15:30 は JST では翌日 00:30。ファイル名も timestamp も翌日になる。"""
    _, logs_dir, _ = env
    _run(env, [_metric(datetime(2026, 8, 9, 15, 30, 0))])

    path = logs_dir / "ap_metrics_20260810_0100_JST.csv"
    assert path.exists(), sorted(p.name for p in logs_dir.iterdir())
    _, rows = _read_csv(path)
    assert rows[0]["timestamp"] == "2026-08-10 00:30:00"


# ---------------------------------------------------------------------------
# 要件 3: 列構成とローダの種別判定
# ---------------------------------------------------------------------------


def test_header_matches_current_36_columns_and_is_detected_as_ap_metrics(env):
    _, logs_dir, _ = env
    _run(env, [_metric(BASE_UTC)])

    header, _ = _read_csv(logs_dir / "ap_metrics_20260809_1300_JST.csv")
    assert header == ALL_CSV_COLUMNS
    assert len(header) == 36

    ft = detect_file_type(header)
    assert ft is not None and ft.key == "ap_metrics"  # ap_metrics_v1 ではない


def test_site_name_is_resolved_from_db(env):
    """ap_metrics は site_name を持たないので、DB の別テーブルから補う。"""
    _, logs_dir, _ = env
    _run(env, [_metric(BASE_UTC), _metric(BASE_UTC, ap="0002", site_id=SITE_ID_2)])

    _, rows = _read_csv(logs_dir / "ap_metrics_20260809_1300_JST.csv")
    assert {r["site_name"] for r in rows} == {SITE_NAME, SITE_NAME_2}


def test_unresolved_site_name_is_reported_and_left_blank(env):
    """サイト名の手がかりがどこにも無い場合は空欄のまま、警告として報告する。"""
    tmp_path, logs_dir, _ = env
    db_path = tmp_path / "no_events.db"
    _make_db(db_path, [_metric(BASE_UTC)], with_events=False)
    result = backfill.backfill(
        db_path=str(db_path), logs_dir=str(logs_dir), write=True
    )

    assert result.unresolved_site_ids == [SITE_ID]
    _, rows = _read_csv(logs_dir / "ap_metrics_20260809_1300_JST.csv")
    assert rows[0]["site_name"] == ""


# ---------------------------------------------------------------------------
# 要件 2: 時間境界（重複も欠落もしない）
# ---------------------------------------------------------------------------


def test_hour_boundary_splits_without_duplication_or_loss(env):
    _, logs_dir, _ = env
    # JST 11:59:59 / 12:00:00 / 12:59:59 / 13:00:00 に相当する UTC
    local_times = [
        datetime(2026, 8, 9, 11, 59, 59),
        datetime(2026, 8, 9, 12, 0, 0),
        datetime(2026, 8, 9, 12, 59, 59),
        datetime(2026, 8, 9, 13, 0, 0),
    ]
    rows = [_metric(t - timedelta(hours=9), ap=f"{i:04d}") for i, t in enumerate(local_times)]
    result = _run(env, rows)

    files = sorted(p.name for p in logs_dir.iterdir())
    assert files == [
        "ap_metrics_20260809_1200_JST.csv",
        "ap_metrics_20260809_1300_JST.csv",
        "ap_metrics_20260809_1400_JST.csv",
    ]
    counts = {}
    all_timestamps = []
    for name in files:
        _, csv_rows = _read_csv(logs_dir / name)
        counts[name] = len(csv_rows)
        all_timestamps.extend(r["timestamp"] for r in csv_rows)

    assert counts == {
        "ap_metrics_20260809_1200_JST.csv": 1,
        "ap_metrics_20260809_1300_JST.csv": 2,
        "ap_metrics_20260809_1400_JST.csv": 1,
    }
    # 全行がちょうど 1 回ずつ現れる（重複も欠落もない）
    assert sorted(all_timestamps) == sorted(t.strftime("%Y-%m-%d %H:%M:%S") for t in local_times)
    assert result.rows_written == len(local_times)


# ---------------------------------------------------------------------------
# 要件 4: 座標 NULL
# ---------------------------------------------------------------------------


def test_null_coordinates_are_written_as_empty_cells(env):
    """座標収集前の行は map_id / x_m / y_m が NULL。空欄で出るのが正しい。"""
    _, logs_dir, _ = env
    _run(env, [
        _metric(BASE_UTC),
        _metric(BASE_UTC + timedelta(minutes=5), ap="0002",
                map_id="test-map-0001", x_m=1.5, y_m=2.5),
    ])

    _, rows = _read_csv(logs_dir / "ap_metrics_20260809_1300_JST.csv")
    by_ap = {r["ap_id"]: r for r in rows}
    assert by_ap["test-ap-0001"]["map_id"] == ""
    assert by_ap["test-ap-0001"]["x_m"] == ""
    assert by_ap["test-ap-0001"]["y_m"] == ""
    assert by_ap["test-ap-0002"]["map_id"] == "test-map-0001"
    assert by_ap["test-ap-0002"]["x_m"] == "1.5"


# ---------------------------------------------------------------------------
# 要件 5 / 6: 既存ファイルの扱い（3 分岐）と冪等性
#   ファイルあり + 登録済み → スキップ
#   ファイルあり + 未登録   → ファイルは書き直さず登録だけ
#   ファイルなし            → 書き出し + 登録
# ---------------------------------------------------------------------------

BUCKET_FILE = "ap_metrics_20260809_1300_JST.csv"


def _place_existing_csv(logs_dir, *, name: str = BUCKET_FILE, rows: int = 2) -> str:
    """「既にそこにあるファイル」として、中身の分かる正規の CSV を置く。"""
    path = logs_dir / name
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_CSV_COLUMNS)
        writer.writeheader()
        for i in range(rows):
            writer.writerow({
                **{c: "" for c in ALL_CSV_COLUMNS},
                "timestamp": "2026-08-09 12:00:00",
                "site_id": f"existing-site-{i:04d}",
                "ap_id": "existing-ap-0001",
            })
    return path.read_text(encoding="utf-8")


def _add_snapshot(LiveSession, filename: str, triggered_by: str = "auto") -> None:
    db = LiveSession()
    try:
        db.add(Snapshot(filename=filename, saved_at=datetime(2026, 8, 9, 4, 0),
                        triggered_by=triggered_by, site_count=1, ap_count=1))
        db.commit()
    finally:
        db.close()


def test_existing_file_with_snapshot_is_skipped(env):
    """ファイルあり + 登録済み → 何もしない。"""
    tmp_path, logs_dir, LiveSession = env
    before = _place_existing_csv(logs_dir)
    _add_snapshot(LiveSession, BUCKET_FILE)

    result = _run(env, [_metric(BASE_UTC)])

    assert (logs_dir / BUCKET_FILE).read_text(encoding="utf-8") == before
    assert [b.filename for b in result.skipped] == [BUCKET_FILE]
    assert result.written == [] and result.adopted == []
    assert result.snapshots_added == 0
    assert [(s.filename, s.triggered_by) for s in _snapshots(LiveSession)] == [
        (BUCKET_FILE, "auto")
    ]


def test_orphan_file_is_registered_without_being_rewritten(env):
    """ファイルあり + 未登録 → ファイルは触らず、登録だけ行う。

    前回の実行が登録前に落ちたときに、再実行で救えるようにするための分岐。
    """
    tmp_path, logs_dir, LiveSession = env
    before = _place_existing_csv(logs_dir, rows=2)

    result = _run(env, [_metric(BASE_UTC)])

    assert (logs_dir / BUCKET_FILE).read_text(encoding="utf-8") == before
    assert [b.filename for b in result.adopted] == [BUCKET_FILE]
    assert result.written == [] and result.skipped == []
    assert result.snapshots_added == 1

    snap = _snapshots(LiveSession)[0]
    assert snap.triggered_by == "restore"
    # saved_at は対象期間の終端（JST 13:00 = UTC 04:00）
    assert snap.saved_at == datetime(2026, 8, 9, 4, 0)
    # 件数は DB の行数ではなく、ファイルの実物から数える
    assert (snap.site_count, snap.ap_count) == (2, 2)


def test_orphan_file_is_not_registered_in_dry_run(env):
    tmp_path, logs_dir, LiveSession = env
    before = _place_existing_csv(logs_dir)

    result = _run(env, [_metric(BASE_UTC)], write=False)

    assert (logs_dir / BUCKET_FILE).read_text(encoding="utf-8") == before
    assert [b.filename for b in result.adopted] == [BUCKET_FILE]
    assert _snapshots(LiveSession) == []


def test_orphan_adoption_is_idempotent(env):
    """孤児を救った後にもう一度実行しても、登録が重複しない。"""
    tmp_path, logs_dir, LiveSession = env
    _place_existing_csv(logs_dir)

    first = _run(env, [_metric(BASE_UTC)])
    second = _run(env, [_metric(BASE_UTC)])

    assert first.snapshots_added == 1
    assert second.snapshots_added == 0
    assert [b.filename for b in second.skipped] == [BUCKET_FILE]
    assert len(_snapshots(LiveSession)) == 1


def test_second_run_adds_no_duplicate_files_or_snapshots(env):
    tmp_path, logs_dir, LiveSession = env
    rows = [_metric(BASE_UTC), _metric(BASE_UTC + timedelta(hours=1), ap="0002")]

    first = _run(env, rows)
    files_after_first = sorted(p.name for p in logs_dir.iterdir())
    snaps_after_first = [s.filename for s in _snapshots(LiveSession)]

    second = _run(env, rows)

    assert sorted(p.name for p in logs_dir.iterdir()) == files_after_first
    assert [s.filename for s in _snapshots(LiveSession)] == snaps_after_first
    assert len(snaps_after_first) == 2
    assert first.snapshots_added == 2
    assert second.snapshots_added == 0
    assert len(second.skipped) == 2


# ---------------------------------------------------------------------------
# 要件 7: dry-run が既定
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(env):
    tmp_path, logs_dir, LiveSession = env
    result = _run(env, [_metric(BASE_UTC)], write=False)

    assert list(logs_dir.iterdir()) == []
    assert _snapshots(LiveSession) == []
    # 書き出す予定は報告する
    assert [b.filename for b in result.written] == ["ap_metrics_20260809_1300_JST.csv"]
    assert result.rows_written == 1


def test_cli_defaults_to_dry_run(env):
    tmp_path, logs_dir, LiveSession = env
    db_path = tmp_path / "cli.db"
    _make_db(db_path, [_metric(BASE_UTC)])

    rc = backfill.main(["--db", str(db_path), "--logs-dir", str(logs_dir)])
    assert rc == 0
    assert list(logs_dir.iterdir()) == []

    rc = backfill.main(["--db", str(db_path), "--logs-dir", str(logs_dir), "--write"])
    assert rc == 0
    assert [p.name for p in logs_dir.iterdir()] == ["ap_metrics_20260809_1300_JST.csv"]


# ---------------------------------------------------------------------------
# 要件 8: saved_at は実行時刻ではなく対象期間の時刻
# ---------------------------------------------------------------------------


def test_snapshot_saved_at_is_the_bucket_end_not_now(env):
    tmp_path, logs_dir, LiveSession = env
    _run(env, [_metric(BASE_UTC), _metric(BASE_UTC + timedelta(hours=1), ap="0002")])

    snaps = _snapshots(LiveSession)
    # JST 13:00 / 14:00 の終端 = UTC 04:00 / 05:00
    assert [s.saved_at for s in snaps] == [
        datetime(2026, 8, 9, 4, 0, 0),
        datetime(2026, 8, 9, 5, 0, 0),
    ]
    assert {s.triggered_by for s in snaps} == {"restore"}
    assert [(s.site_count, s.ap_count) for s in snaps] == [(1, 1), (1, 1)]


def test_snapshot_counts_sites_and_records(env):
    tmp_path, logs_dir, LiveSession = env
    _run(env, [
        _metric(BASE_UTC),
        _metric(BASE_UTC, ap="0002", site_id=SITE_ID_2),
        _metric(BASE_UTC + timedelta(minutes=5), ap="0002", site_id=SITE_ID_2),
    ])

    snap = _snapshots(LiveSession)[0]
    assert (snap.site_count, snap.ap_count) == (2, 3)


# ---------------------------------------------------------------------------
# 要件 9: --from / --to は JST として解釈される
# ---------------------------------------------------------------------------


def test_from_to_are_interpreted_as_local_time(env):
    _, logs_dir, _ = env
    # JST 11:30 / 12:30 / 13:30
    rows = [
        _metric(datetime(2026, 8, 9, 2, 30, 0), ap="0001"),
        _metric(datetime(2026, 8, 9, 3, 30, 0), ap="0002"),
        _metric(datetime(2026, 8, 9, 4, 30, 0), ap="0003"),
    ]
    result = _run(env, rows, window_from="2026-08-09 12:00", window_to="2026-08-09 13:00")

    assert [b.filename for b in result.written] == ["ap_metrics_20260809_1300_JST.csv"]
    _, csv_rows = _read_csv(logs_dir / "ap_metrics_20260809_1300_JST.csv")
    assert [r["ap_id"] for r in csv_rows] == ["test-ap-0002"]


def test_from_to_are_not_interpreted_as_utc(env):
    """UTC として解釈していたら、この範囲には 1 行も入らない（9 時間ずれの検出）。"""
    _, logs_dir, _ = env
    result = _run(
        env, [_metric(BASE_UTC)],
        window_from="2026-08-09 12:00", window_to="2026-08-09 13:00",
    )
    assert result.rows_written == 1


def test_invalid_range_is_an_input_error(env):
    tmp_path, logs_dir, _ = env
    db_path = tmp_path / "range.db"
    _make_db(db_path, [_metric(BASE_UTC)])

    rc = backfill.main([
        "--db", str(db_path), "--logs-dir", str(logs_dir),
        "--from", "2026-08-09 13:00", "--to", "2026-08-09 12:00", "--write",
    ])
    assert rc == 1
    assert list(logs_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# 要件 10: --db で別ファイルから読める
# ---------------------------------------------------------------------------


def test_reads_from_an_alternate_db_file(env):
    """保持期間で live DB から消えた行を、バックアップした DB から復元する経路。"""
    tmp_path, logs_dir, LiveSession = env
    backup = tmp_path / "backup-copy.db"
    _make_db(backup, [_metric(BASE_UTC)])

    result = backfill.backfill(db_path=str(backup), logs_dir=str(logs_dir), write=True)

    assert result.db_path == str(backup)
    assert result.rows_written == 1
    # 読み込み元はバックアップでも、snapshots は稼働中の DB に登録される
    assert [s.filename for s in _snapshots(LiveSession)] == [
        "ap_metrics_20260809_1300_JST.csv"
    ]


def _prepare_source(tmp_path):
    """1 行だけ入った読み込み元 DB を用意する。"""
    path = tmp_path / "src.db"
    if not path.exists():
        _make_db(path, [_metric(BASE_UTC)])
    return path


def test_snapshot_db_can_be_pointed_at_another_file(env):
    """コンテナ外から実行するとき用の --snapshot-db。"""
    tmp_path, logs_dir, LiveSession = env
    other = tmp_path / "other-live.db"
    _make_db(other, [])

    rc = backfill.main([
        "--db", str(_prepare_source(tmp_path)), "--logs-dir", str(logs_dir),
        "--snapshot-db", str(other), "--write",
    ])
    assert rc == 0

    OtherSession = sessionmaker(bind=create_engine(f"sqlite:///{other}"))
    db = OtherSession()
    try:
        assert [s.filename for s in db.query(Snapshot).all()] == [
            "ap_metrics_20260809_1300_JST.csv"
        ]
    finally:
        db.close()
    # 既定の登録先（稼働中の DB）には入らない
    assert _snapshots(LiveSession) == []


def test_write_stops_before_touching_anything_if_snapshot_db_is_unusable(env, monkeypatch):
    """登録先 DB を開けないなら、CSV を 1 件も書かずに終わる（中途半端に終わらせない）。"""
    tmp_path, logs_dir, _ = env
    broken = sessionmaker(
        bind=create_engine("sqlite:////nonexistent-dir-for-test/live.db")
    )
    monkeypatch.setattr(backfill, "SessionLocal", broken)

    rc = backfill.main([
        "--db", str(_prepare_source(tmp_path)), "--logs-dir", str(logs_dir), "--write",
    ])
    assert rc == 1
    assert list(logs_dir.iterdir()) == []


def test_missing_db_file_is_an_input_error(env):
    tmp_path, logs_dir, _ = env
    rc = backfill.main([
        "--db", str(tmp_path / "does-not-exist.db"), "--logs-dir", str(logs_dir),
    ])
    assert rc == 1


# ---------------------------------------------------------------------------
# database is locked の再発防止
#
# 本番（50 万行）で --write が 1 ファイル目の commit で必ず落ちた。原因は
# 読み取りを yield_per で流したままファイル書き出し・snapshots 登録へ進んでいたこと。
# pysqlite は SELECT を遅延実行するため SHARED ロックを掴んだままになり、
# 同じ DB ファイルへの書き込みが弾かれる。行数が少ないと SELECT が読み切れてしまい
# 再現しないので、ここでは**バッファ（yield_per=2000）を超える行数**を用意する。
# ---------------------------------------------------------------------------

#: 読み取りが 1 回のフェッチで終わらない行数（旧実装のロックを再現するのに必要）
_ROWS_BEYOND_BUFFER = 2700


def _many_rows() -> list[dict]:
    """3 バケットにまたがる、バッファを超える行数の合成データ。"""
    rows = []
    for i in range(_ROWS_BEYOND_BUFFER):
        # 3 秒間隔 → 2700 行で 2 時間 15 分ぶん（12 時台・13 時台・14 時台の 3 バケット）
        rows.append(_metric(BASE_UTC + timedelta(seconds=3 * i), ap=f"{i % 250:04d}"))
    return rows


def test_write_succeeds_when_source_and_snapshot_are_the_same_db_file(env):
    """本番と同じ構成（読み書きが同一ファイル・大きめの行数）で最後まで通ること。"""
    tmp_path, logs_dir, _ = env
    same = tmp_path / "same-file.db"
    _make_db(same, _many_rows())

    result = backfill.backfill(
        db_path=str(same), logs_dir=str(logs_dir),
        snapshot_db_path=str(same), write=True,
    )

    assert result.rows_written == _ROWS_BEYOND_BUFFER
    assert len(result.written) == 3
    assert result.snapshots_added == 3

    OtherSession = sessionmaker(bind=create_engine(f"sqlite:///{same}"))
    db = OtherSession()
    try:
        assert db.query(Snapshot).count() == 3
    finally:
        db.close()


def test_no_read_connection_is_open_while_snapshots_are_written(env, monkeypatch):
    """snapshots を書く時点で、読み取り側の接続が 1 本も残っていないこと。

    これが崩れると同一ファイル構成で database is locked に戻る。
    """
    tmp_path, logs_dir, _ = env
    src = tmp_path / "big.db"
    _make_db(src, _many_rows())

    source_engines = []
    real_open = backfill.open_source_db

    def spy_open(path):
        maker = real_open(path)
        source_engines.append(maker.kw["bind"])
        return maker

    observed = []
    real_register = backfill._register_snapshots

    def spy_register(Session_, pending):
        observed.append([e.pool.checkedout() for e in source_engines])
        return real_register(Session_, pending)

    monkeypatch.setattr(backfill, "open_source_db", spy_open)
    monkeypatch.setattr(backfill, "_register_snapshots", spy_register)

    backfill.backfill(db_path=str(src), logs_dir=str(logs_dir), write=True)

    assert observed, "_register_snapshots が呼ばれていない"
    assert source_engines, "読み取り用エンジンが捕捉できていない"
    assert observed[0] == [0] * len(source_engines), (
        f"snapshots 書き込み時に読み取り接続が開いている: {observed[0]}"
    )


def test_snapshots_are_committed_once_not_per_file(env, monkeypatch):
    """ファイル数ぶん commit しない（148 ファイルで 148 トランザクションにしない）。"""
    tmp_path, logs_dir, _ = env
    src = tmp_path / "many.db"
    _make_db(src, _many_rows())

    calls = []
    real_register = backfill._register_snapshots

    def spy_register(Session_, pending):
        calls.append(len(pending))
        return real_register(Session_, pending)

    monkeypatch.setattr(backfill, "_register_snapshots", spy_register)
    result = backfill.backfill(db_path=str(src), logs_dir=str(logs_dir), write=True)

    assert len(result.written) == 3
    assert calls == [3]  # 3 ファイルぶんを 1 回でまとめて登録


# ---------------------------------------------------------------------------
# その他
# ---------------------------------------------------------------------------


def test_rows_are_ordered_like_the_automatic_save(env):
    """自動保存と同じ並び（site_id → ap_id → timestamp）で書く。"""
    _, logs_dir, _ = env
    _run(env, [
        _metric(BASE_UTC + timedelta(minutes=10), ap="0002"),
        _metric(BASE_UTC, ap="0002"),
        _metric(BASE_UTC + timedelta(minutes=5), ap="0001"),
    ])

    _, rows = _read_csv(logs_dir / "ap_metrics_20260809_1300_JST.csv")
    assert [(r["ap_id"], r["timestamp"]) for r in rows] == [
        ("test-ap-0001", "2026-08-09 12:05:39"),
        ("test-ap-0002", "2026-08-09 12:00:39"),
        ("test-ap-0002", "2026-08-09 12:10:39"),
    ]


def test_timezone_comes_from_app_settings(env):
    """CSV の現地時刻は app_settings.timezone に従う（JST 固定ではない）。"""
    tmp_path, logs_dir, _ = env
    db_path = tmp_path / "utc_settings.db"
    _make_db(db_path, [_metric(BASE_UTC)], tz="UTC")

    backfill.backfill(db_path=str(db_path), logs_dir=str(logs_dir), write=True)

    path = logs_dir / "ap_metrics_20260809_0400_UTC.csv"
    assert path.exists(), sorted(p.name for p in logs_dir.iterdir())
    _, rows = _read_csv(path)
    assert rows[0]["timestamp"] == "2026-08-09 03:00:39"


def test_partial_write_leaves_no_csv_behind(env, monkeypatch):
    """書き出し中に失敗しても、中途半端な .csv を残さない（次回スキップされないため）。"""
    tmp_path, logs_dir, _ = env

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(backfill, "ap_metrics_csv_row", boom)
    with pytest.raises(RuntimeError):
        _run(env, [_metric(BASE_UTC)])

    assert [p.name for p in logs_dir.iterdir()] == ["ap_metrics_20260809_1300_JST.csv.tmp"]
