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

from sqlalchemy import create_engine
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
    skipped: bool = False  # 同名ファイルが既にあり、書き出さなかった


@dataclass
class BackfillResult:
    db_path: str
    logs_dir: str
    tz_str: str
    written: list[BucketPlan] = field(default_factory=list)
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
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
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


def bucket_end_of(local_dt: datetime) -> datetime:
    """現地時刻を 1 時間バケットに丸め、**期間の終端**を返す。

    自動保存は「前回保存〜今」を今の時刻の名前で書き出すため、
    12:00〜13:00 のデータは ``..._1300_...csv`` になる。その規則に合わせる。
    """
    start = local_dt.replace(minute=0, second=0, microsecond=0)
    return start + BUCKET


def bucket_filename(end_local: datetime) -> str:
    """自動保存と同じ命名規則（``ap_metrics_YYYYMMDD_HHMM_TZ.csv``）。"""
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


def _iter_buckets(
    session: Session, tz: ZoneInfo, start_utc: datetime | None, end_utc: datetime | None
) -> Iterator[tuple[datetime, list[ApMetrics]]]:
    """時刻順に流し読みし、1 時間バケットごとに ``(期間終端の現地時刻, 行)`` を返す。

    全件を一度にメモリへ載せない（実環境は 50 万行規模）。
    """
    query = session.query(ApMetrics)
    if start_utc is not None:
        query = query.filter(ApMetrics.timestamp >= start_utc)
    if end_utc is not None:
        query = query.filter(ApMetrics.timestamp < end_utc)

    current_end: datetime | None = None
    batch: list[ApMetrics] = []
    for row in query.order_by(ApMetrics.timestamp, ApMetrics.id).yield_per(2000):
        end_local = bucket_end_of(to_local(row.timestamp, tz))
        if current_end is not None and end_local != current_end:
            yield current_end, batch
            batch = []
        current_end = end_local
        batch.append(row)
    if current_end is not None and batch:
        yield current_end, batch


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


def _register_snapshot(
    Session_: sessionmaker, filename: str, saved_at: datetime,
    site_count: int, record_count: int,
) -> bool:
    """History 画面に出すため ``snapshots`` に登録する。既にあれば何もしない。"""
    db: Session = Session_()
    try:
        if db.query(Snapshot).filter_by(filename=filename).first():
            return False
        db.add(Snapshot(
            filename=filename,
            saved_at=saved_at,
            triggered_by=TRIGGERED_BY,
            site_count=site_count,
            ap_count=record_count,
        ))
        db.commit()
        return True
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

        _print_header(result, write, start_local, end_local, out)

        for end_local_bucket, rows in _iter_buckets(session, tz, start_utc, end_utc):
            filename = bucket_filename(end_local_bucket)
            path = os.path.join(logs_dir, filename)
            sites = len({r.site_id for r in rows})
            plan = BucketPlan(
                filename=filename, end_local=end_local_bucket,
                rows=len(rows), sites=sites,
            )

            if os.path.exists(path):
                plan.skipped = True
                result.skipped.append(plan)
                print(f"  = {filename}  rows={len(rows)}  (既存のためスキップ)", file=out)
                continue

            if write:
                # 自動保存と同じ並び（サイト → AP → 時刻）
                rows.sort(key=lambda r: (r.site_id or "", r.ap_id or "", r.timestamp))
                _write_bucket(path, rows, site_names, tz_str)
                # saved_at は実行時刻ではなく対象期間の時刻（History を時系列に保つ）
                if _register_snapshot(
                    SnapshotSession, filename, to_utc_naive(end_local_bucket),
                    sites, len(rows),
                ):
                    result.snapshots_added += 1
            result.written.append(plan)
            print(f"  {'+' if write else '-'} {filename}  rows={len(rows)}  sites={sites}", file=out)

        _print_summary(result, write, out)
        return result
    finally:
        session.close()


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
        f"{len(result.skipped)} スキップ（既存）", file=out
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
