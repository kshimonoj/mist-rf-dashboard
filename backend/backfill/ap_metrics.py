"""ap_metrics の CSV ログ（``data/logs``）を DB から再生成する運用ツール。

正本は DB（``ap_metrics`` テーブル）で、``data/logs`` の CSV はその派生物である。
ディスク障害・ローテートの事故・CSV 仕様変更などで派生物が失われたとき、
このコマンドで正本から作り直す。

前提となる重要な性質:

- **DB は UTC 保存・CSV は現地時刻（``app_settings.timezone``）**。この変換を誤ると
  9 時間ずれて分析の窓指定が全く合わなくなる。変換は ``scheduler.ap_metrics_csv_row``
  に一本化してあり、ここでは再実装しない。
- **列構成も ``scheduler.ALL_CSV_COLUMNS`` をそのまま使う**。ローダ
  （``hangap.loader`` → ``pseudonymizer.schemas.detect_file_type``）はヘッダーの
  完全一致で種別を判定するため、1 列でもずれると読めなくなる。
- 粒度・命名規則は自動保存（``scheduler.save_hourly_logs``）と同じ 1 時間 1 ファイル、
  ``ap_metrics_<YYYYMMDD>_<HHMM>_<TZ>.csv``。ファイル名の時刻は**対象期間の終端**。

ネットワークアクセスは行わない（Mist API を呼ばない）。サイト名は DB と既存 CSV から解決する。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker

import database
import scheduler
from database import SessionLocal
from models import ApEvent, ApMetrics, AppSettings, Insight, Snapshot
from scheduler import ALL_CSV_COLUMNS, ap_metrics_csv_row

#: History 画面で通常収集（auto）・手動保存（manual）と区別するための値
TRIGGERED_BY = "restore"

#: 1 ファイルが対象とする期間（自動保存と同じ 1 時間）
BUCKET = timedelta(hours=1)

#: サイト名を既存 CSV から補完するときに読むファイル数の上限
_SITE_NAME_SCAN_FILES = 200

#: SQLite のロック待ち秒数。既定 5 秒は復旧作業中の一瞬の競合で落ちるには短い
_SQLITE_TIMEOUT_SECONDS = 30.0

EXIT_OK = 0
EXIT_INPUT_ERROR = 1


class BackfillError(RuntimeError):
    """入力エラー（終了コード 1）。"""


class _ArgumentParser(argparse.ArgumentParser):
    """argparse の既定終了コード(2)を使わせないための薄いラッパ。"""

    def error(self, message: str) -> None:  # noqa: D102 - argparse のオーバーライド
        self.print_usage(sys.stderr)
        raise BackfillError(f"{self.prog}: {message}")


@dataclass
class BucketPlan:
    """1 時間分（= 1 ファイル分）の再生成結果。"""

    filename: str
    #: 対象期間の終端（現地時刻）。ファイル名と Snapshot.saved_at の元になる
    end_local: datetime
    rows: int
    sites: int


@dataclass
class BackfillResult:
    db_path: str
    logs_dir: str
    tz_str: str
    #: ファイルが無く、新しく書き出した（予定の）バケット
    written: list[BucketPlan] = field(default_factory=list)
    #: ファイルはあるが snapshots に未登録だったため、登録だけ行った（予定の）バケット
    adopted: list[BucketPlan] = field(default_factory=list)
    #: ファイルも snapshots の行も揃っていて、何もしなかったバケット
    skipped: list[BucketPlan] = field(default_factory=list)
    snapshots_added: int = 0
    unresolved_site_ids: list[str] = field(default_factory=list)

    @property
    def rows_written(self) -> int:
        return sum(b.rows for b in self.written)

    @property
    def rows_skipped(self) -> int:
        return sum(b.rows for b in self.skipped)


# ---------------------------------------------------------------------------
# DB / 設定
# ---------------------------------------------------------------------------


def default_db_path() -> str:
    """稼働中の DB ファイルのパス（``database.DATABASE_URL`` から導く）。"""
    url = database.DATABASE_URL
    if not url.startswith("sqlite:///"):
        raise BackfillError(f"SQLite 以外の DATABASE_URL には未対応: {url}")
    return url[len("sqlite:///"):]


def open_source_db(db_path: str) -> sessionmaker:
    """読み込み元の SQLite を開く（稼働中の DB でもバックアップでも同じ扱い）。"""
    if not os.path.isfile(db_path):
        raise BackfillError(f"DB ファイルが見つからない: {db_path}")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            "check_same_thread": False,
            # 既定の 5 秒だと、たまたま重なった書き込みで復旧作業ごと落ちる
            "timeout": _SQLITE_TIMEOUT_SECONDS,
        },
    )
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def load_timezone(session: Session) -> str:
    """CSV を書く現地タイムゾーンを ``app_settings`` から読む。

    行が無い DB（バックアップの断片など）では scheduler の既定値にフォールバックする。
    """
    row = session.query(AppSettings).first()
    tz_str = (row.timezone if row else None) or scheduler._app_timezone
    try:
        ZoneInfo(tz_str)
    except Exception as e:
        raise BackfillError(f"不正なタイムゾーン設定: {tz_str} ({e})")
    return tz_str


# ---------------------------------------------------------------------------
# 時刻の扱い（DB=UTC / CSV=現地時刻）
# ---------------------------------------------------------------------------


def parse_local(value: str, tz: ZoneInfo) -> datetime:
    """``--from`` / ``--to`` を**現地時刻**として解釈し、tz 付き datetime にする。"""
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise BackfillError(
            f"時刻の書式が不正: {value!r}（例: '2026-08-09 16:00'）"
        )
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def to_utc_naive(dt: datetime) -> datetime:
    """tz 付き datetime を、DB の格納形式（UTC の naive）に落とす。"""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def to_local(db_dt: datetime, tz: ZoneInfo) -> datetime:
    """DB の naive datetime（UTC）を現地時刻に変換する。"""
    if db_dt.tzinfo is None:
        db_dt = db_dt.replace(tzinfo=timezone.utc)
    return db_dt.astimezone(tz)


def bucket_filename(end_local: datetime) -> str:
    """自動保存と同じ命名規則（``ap_metrics_YYYYMMDD_HHMM_TZ.csv``）。

    引数は**対象期間の終端**。自動保存は「前回保存〜今」を今の時刻の名前で書き出すため、
    12:00〜13:00 のデータは ``..._1300_...csv`` になる。その規則に合わせる。
    """
    return f"ap_metrics_{end_local.strftime('%Y%m%d_%H%M')}_{end_local.strftime('%Z')}.csv"


# ---------------------------------------------------------------------------
# サイト名の解決（Mist API を呼ばずに DB / 既存 CSV から復元する）
# ---------------------------------------------------------------------------


def resolve_site_names(
    session: Session, site_ids: set[str], logs_dir: str
) -> tuple[dict[str, str], list[str]]:
    """site_id → site_name を、DB（ap_events / insights）と既存 CSV から解決する。

    ``ap_metrics`` テーブルは site_name を持たないため、CSV の site_name 列は
    別の場所から補う必要がある。解決できなかった site_id も返し、呼び出し側が警告する。
    """
    names: dict[str, str] = {}

    def _take(pairs) -> None:
        for site_id, site_name in pairs:
            if site_id in site_ids and site_id not in names and site_name:
                names[site_id] = site_name

    for model in (ApEvent, Insight):
        if len(names) >= len(site_ids):
            break
        try:
            rows = (
                session.query(model.site_id, model.site_name)
                .filter(model.site_name.isnot(None))
                .distinct()
                .all()
            )
        except Exception:
            continue  # そのテーブルが無い DB（バックアップの断片など）は素通り
        _take(rows)

    if len(names) < len(site_ids):
        _take(_scan_logs_for_site_names(logs_dir, site_ids - set(names)))

    unresolved = sorted(site_ids - set(names))
    return names, unresolved


def _scan_logs_for_site_names(logs_dir: str, wanted: set[str]) -> Iterator[tuple[str, str]]:
    """既存 CSV（新しい順）から site_id / site_name の組を拾う。

    種別は問わず、``site_id`` と ``site_name`` の両方を持つヘッダーの CSV を対象にする。
    """
    if not wanted or not os.path.isdir(logs_dir):
        return
    remaining = set(wanted)
    entries = [
        e for e in os.scandir(logs_dir)
        if e.is_file() and e.name.endswith(".csv")
    ]
    entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)

    for entry in entries[:_SITE_NAME_SCAN_FILES]:
        if not remaining:
            return
        try:
            with open(entry.path, encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or not {"site_id", "site_name"} <= set(reader.fieldnames):
                    continue
                for row in reader:
                    site_id = (row.get("site_id") or "").strip()
                    site_name = (row.get("site_name") or "").strip()
                    if site_id in remaining and site_name:
                        remaining.discard(site_id)
                        yield site_id, site_name
                        if not remaining:
                            return
        except OSError:
            continue


# ---------------------------------------------------------------------------
# 再生成の本体
# ---------------------------------------------------------------------------


def _apply_window(query, start_utc: datetime | None, end_utc: datetime | None):
    if start_utc is not None:
        query = query.filter(ApMetrics.timestamp >= start_utc)
    if end_utc is not None:
        query = query.filter(ApMetrics.timestamp < end_utc)
    return query


def _bucket_bounds(
    session: Session, tz: ZoneInfo, start_utc: datetime | None, end_utc: datetime | None
) -> list[tuple[datetime, datetime]]:
    """対象範囲を 1 時間バケット（UTC の ``[開始, 終了)``）に割る。

    最初のバケットの起点は「最古の行の**現地時刻**を正時に丸めたもの」。以降は UTC で
    1 時間ずつ進める（現地時刻で足すと DST のある TZ でバケット幅が狂うため）。
    """
    lo, hi = _apply_window(
        session.query(func.min(ApMetrics.timestamp), func.max(ApMetrics.timestamp)),
        start_utc, end_utc,
    ).one()
    session.rollback()  # 集計の読み取りトランザクションをここで閉じる
    if lo is None or hi is None:
        return []

    cursor = to_utc_naive(to_local(lo, tz).replace(minute=0, second=0, microsecond=0))
    bounds: list[tuple[datetime, datetime]] = []
    while cursor <= hi:
        bounds.append((cursor, cursor + BUCKET))
        cursor += BUCKET
    return bounds


def _load_bucket(
    session: Session, lo: datetime, hi: datetime,
    start_utc: datetime | None, end_utc: datetime | None,
) -> list[ApMetrics]:
    """1 バケット分を**読み切って**返し、読み取りトランザクションを閉じる。

    ここで ``.all()`` を使い切ってから ``rollback()`` するのが重要。``yield_per`` で
    流し読みしたままファイル書き出し・snapshots 登録へ進むと、pysqlite は SELECT を
    遅延実行するため SHARED ロックを掴んだままになり、同じ DB ファイルへの
    書き込みが ``database is locked`` で落ちる（実際にこれで本番が落ちた）。
    """
    rows = (
        _apply_window(
            session.query(ApMetrics).filter(
                ApMetrics.timestamp >= lo, ApMetrics.timestamp < hi
            ),
            start_utc, end_utc,
        )
        # 自動保存と同じ並び（サイト → AP → 時刻）
        .order_by(ApMetrics.site_id, ApMetrics.ap_id, ApMetrics.timestamp)
        .all()
    )
    session.rollback()
    return rows


def _write_bucket(
    path: str, rows: list[ApMetrics], site_names: dict[str, str], tz_str: str
) -> None:
    """1 バケット分を CSV に書き出す（一時ファイル経由で原子的に置き換える）。

    書きかけのファイルが ``.csv`` として残ると、次回実行時に「既にある」と誤って
    スキップされてしまうため、完成してから rename する。
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(ap_metrics_csv_row(r, site_names.get(r.site_id, ""), tz_str))
    os.replace(tmp, path)


def _snapshot_sessionmaker(snapshot_db_path: str | None) -> sessionmaker:
    """``snapshots`` の登録先を決める。

    既定は**稼働中の DB**（History が読む DB）。``--db`` でバックアップから読んだ
    場合も登録先はこちらで正しい。コンテナ外から実行するときだけ
    ``--snapshot-db`` で明示する。
    """
    if snapshot_db_path:
        return open_source_db(snapshot_db_path)
    return SessionLocal


def _check_snapshot_db(Session_: sessionmaker) -> None:
    """書き出す前に登録先 DB を開けるか確かめる（途中で落ちて中途半端に終わらせない）。"""
    db: Session = Session_()
    try:
        db.query(Snapshot).first()
    except Exception as e:
        raise BackfillError(
            f"snapshots の登録先 DB を開けない: {e}\n"
            "  コンテナ外から実行している場合は --snapshot-db で稼働中の DB を指定する"
        )
    finally:
        db.close()


def _count_csv(path: str) -> tuple[int, int]:
    """既存 CSV の行数とサイト数を数える（孤児ファイルを登録するときの件数）。

    DB の行数ではなく**ファイルの実物**を数える。snapshots はファイルの説明だから。
    """
    rows = 0
    sites: set[str] = set()
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rows += 1
                site_id = (row.get("site_id") or "").strip()
                if site_id:
                    sites.add(site_id)
    except OSError:
        return 0, 0
    return rows, len(sites)


def _snapshot_for(
    filename: str, end_local: datetime, site_count: int, record_count: int
) -> Snapshot:
    """登録用の ``Snapshot`` を組み立てる。

    ``saved_at`` は実行時刻ではなく**対象期間の終端**（History を時系列に保つため）。
    """
    return Snapshot(
        filename=filename,
        saved_at=to_utc_naive(end_local),
        triggered_by=TRIGGERED_BY,
        site_count=site_count,
        ap_count=record_count,
    )


def _existing_snapshot_filenames(Session_: sessionmaker) -> set[str]:
    """登録済みのファイル名を最初に 1 回だけ読む（バケットごとに問い合わせない）。"""
    db: Session = Session_()
    try:
        return {name for (name,) in db.query(Snapshot.filename).all()}
    finally:
        db.close()


def _register_snapshots(Session_: sessionmaker, pending: list[Snapshot]) -> int:
    """History 画面に出すため ``snapshots`` にまとめて登録する。

    **読み取り側の接続を閉じてから、1 トランザクションで書く。** バケットごとに
    commit していたときは、読み取り接続がロックを掴んだままだったため
    1 件目の commit で ``database is locked`` になっていた。
    """
    if not pending:
        return 0
    db: Session = Session_()
    try:
        # 別プロセスが同時に登録した場合に備え、書き込み直前にもう一度確かめる
        known = {name for (name,) in db.query(Snapshot.filename).all()}
        fresh = [s for s in pending if s.filename not in known]
        db.add_all(fresh)
        db.commit()
        return len(fresh)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def backfill(
    *,
    db_path: str,
    logs_dir: str,
    window_from: str | None = None,
    window_to: str | None = None,
    snapshot_db_path: str | None = None,
    write: bool = False,
    out=sys.stdout,
) -> BackfillResult:
    """DB から ap_metrics の CSV を再生成する。``write=False`` なら 1 件も書かない。"""
    SourceSession = open_source_db(db_path)
    session: Session = SourceSession()
    try:
        tz_str = load_timezone(session)
        tz = ZoneInfo(tz_str)

        start_local = parse_local(window_from, tz) if window_from else None
        end_local = parse_local(window_to, tz) if window_to else None
        if start_local and end_local and start_local >= end_local:
            raise BackfillError("--from は --to より前でなければならない")
        for label, dt in (("--from", start_local), ("--to", end_local)):
            if dt and (dt.minute or dt.second or dt.microsecond):
                print(
                    f"WARNING: {label} が正時ではないため、境界のファイルは"
                    f"1 時間に満たない内容になる: {dt:%Y-%m-%d %H:%M:%S}",
                    file=out,
                )

        start_utc = to_utc_naive(start_local) if start_local else None
        end_utc = to_utc_naive(end_local) if end_local else None

        # サイト名を引くのは対象期間に現れる site_id だけでよい（無駄な走査と警告を避ける）
        site_id_query = session.query(ApMetrics.site_id).distinct()
        if start_utc is not None:
            site_id_query = site_id_query.filter(ApMetrics.timestamp >= start_utc)
        if end_utc is not None:
            site_id_query = site_id_query.filter(ApMetrics.timestamp < end_utc)
        site_ids = {s for (s,) in site_id_query.all() if s}
        site_names, unresolved = resolve_site_names(session, site_ids, logs_dir)

        result = BackfillResult(
            db_path=db_path, logs_dir=logs_dir, tz_str=tz_str,
            unresolved_site_ids=unresolved,
        )

        SnapshotSession = _snapshot_sessionmaker(snapshot_db_path)
        if write:
            _check_snapshot_db(SnapshotSession)
            os.makedirs(logs_dir, exist_ok=True)
        registered = _existing_snapshot_filenames(SnapshotSession)

        _print_header(result, write, start_local, end_local, out)

        # snapshots へ書くのはループの外（読み取り接続を閉じてから 1 トランザクション）
        pending: list[Snapshot] = []

        for lo, hi in _bucket_bounds(session, tz, start_utc, end_utc):
            rows = _load_bucket(session, lo, hi, start_utc, end_utc)
            if not rows:
                continue

            end_local_bucket = to_local(hi, tz)
            filename = bucket_filename(end_local_bucket)
            path = os.path.join(logs_dir, filename)
            exists = os.path.exists(path)

            if exists and filename in registered:
                result.skipped.append(BucketPlan(
                    filename=filename, end_local=end_local_bucket,
                    rows=len(rows), sites=len({r.site_id for r in rows}),
                ))
                print(f"  = {filename}  rows={len(rows)}  (既存・登録済みのためスキップ)", file=out)
                continue

            if exists:
                # 前回の実行が登録前に落ちた等で残った孤児ファイル。
                # 中身は書き直さず、登録だけ行う（件数はファイルの実物から数える）
                rows_in_file, sites_in_file = _count_csv(path)
                result.adopted.append(BucketPlan(
                    filename=filename, end_local=end_local_bucket,
                    rows=rows_in_file, sites=sites_in_file,
                ))
                print(
                    f"  ~ {filename}  rows={rows_in_file}  "
                    "(既存ファイルを snapshots に登録)", file=out
                )
                if write:
                    pending.append(_snapshot_for(
                        filename, end_local_bucket, sites_in_file, rows_in_file
                    ))
                continue

            sites = len({r.site_id for r in rows})
            if write:
                _write_bucket(path, rows, site_names, tz_str)
                pending.append(_snapshot_for(filename, end_local_bucket, sites, len(rows)))
            result.written.append(BucketPlan(
                filename=filename, end_local=end_local_bucket, rows=len(rows), sites=sites,
            ))
            print(f"  {'+' if write else '-'} {filename}  rows={len(rows)}  sites={sites}", file=out)

    finally:
        # snapshots へ書く前に、読み取り側の接続を必ず手放す
        session.close()

    if write:
        result.snapshots_added = _register_snapshots(SnapshotSession, pending)
    _print_summary(result, write, out)
    return result


def _print_header(
    result: BackfillResult, write: bool,
    start_local: datetime | None, end_local: datetime | None, out,
) -> None:
    mode = "WRITE" if write else "DRY-RUN"
    fmt = "%Y-%m-%d %H:%M"
    span = (
        f"{start_local.strftime(fmt) if start_local else '(DB の最初)'}"
        f" 〜 {end_local.strftime(fmt) if end_local else '(DB の最後)'}"
    )
    print(f"[{mode}] ap_metrics backfill", file=out)
    print(f"  source DB : {result.db_path}", file=out)
    print(f"  logs dir  : {result.logs_dir}", file=out)
    print(f"  timezone  : {result.tz_str}（--from / --to はこの現地時刻として解釈）", file=out)
    print(f"  range     : {span}", file=out)


def _print_summary(result: BackfillResult, write: bool, out) -> None:
    mode = "WRITE" if write else "DRY-RUN"
    print(f"\n[{mode}] 結果", file=out)
    verb = "書き出した" if write else "書き出す予定"
    print(
        f"  files     : {len(result.written)} {verb} / "
        f"{len(result.adopted)} 既存ファイルを登録 / "
        f"{len(result.skipped)} スキップ（既存・登録済み）", file=out
    )
    print(
        f"  rows      : {result.rows_written} / スキップ分 {result.rows_skipped}", file=out
    )
    if write:
        print(f"  snapshots : {result.snapshots_added} 件登録（triggered_by={TRIGGERED_BY}）", file=out)
    else:
        print("  ※ 実際に書き出すには --write を付けて再実行する", file=out)
    if result.unresolved_site_ids:
        print(
            f"  WARNING: site_name を解決できなかった site_id が "
            f"{len(result.unresolved_site_ids)} 件ある（CSV では空欄になる）", file=out
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="backfill.ap_metrics",
        description="DB（正本）から ap_metrics の CSV ログを再生成する",
    )
    parser.add_argument("--from", dest="window_from", metavar="TIME", default=None,
                        help="開始（現地時刻。例: '2026-08-09 16:00'）。省略時は DB の全範囲")
    parser.add_argument("--to", dest="window_to", metavar="TIME", default=None,
                        help="終了（現地時刻・この時刻は含まない）。省略時は DB の全範囲")
    parser.add_argument("--db", dest="db_path", metavar="PATH", default=None,
                        help="読み込む SQLite ファイル（既定: 稼働中の DB）")
    parser.add_argument("--logs-dir", dest="logs_dir", metavar="DIR", default=None,
                        help=f"出力先（既定: {scheduler.LOGS_DIR}）")
    parser.add_argument("--snapshot-db", dest="snapshot_db_path", metavar="PATH", default=None,
                        help="snapshots の登録先（既定: 稼働中の DB。コンテナ外から実行するとき用）")
    parser.add_argument("--write", action="store_true",
                        help="実際に書き出す（既定は dry-run で 1 件も書かない）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        backfill(
            db_path=args.db_path or default_db_path(),
            logs_dir=args.logs_dir or scheduler.LOGS_DIR,
            window_from=args.window_from,
            window_to=args.window_to,
            snapshot_db_path=args.snapshot_db_path,
            write=args.write,
        )
    except BackfillError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
