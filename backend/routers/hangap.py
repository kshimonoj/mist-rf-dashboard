"""ハングAP分析のオンデマンドジョブ API。

分析そのものは ``hangap.analysis`` に集約されており（CLI と同じ関数）、この
ルーターはジョブの生成・進捗・結果の取り出しだけを行う。ここでロジックを
再実装すると CLI と UI で結果が食い違うため、絶対に持ち込まないこと。

- 分析は **リクエストされたときにだけ** 走る。定期実行・常時分析はしない。
- 同時に走るジョブは 1 つまで（5,000 ファイル超の読み込みが並行すると
  稼働中のポーリング処理を圧迫する）。
- 結果はプロセス内に保持する。DB には保存しない。
- ジョブの一時ファイルは ``tempfile`` の一時ディレクトリに置く。``data/logs`` には
  **書かない**（混ざると次回の分析が自分の出力を読み込む）。
- ``done`` で完了した結果だけは ``data/hangap_results/`` に組（xlsx/csv/json）として
  保存し、:mod:`hangap.archive` がローテートする。保存先は ``data/logs`` の外なので、
  次回の分析の入力にはならない（:data:`hangap.loader.EXCLUDED_DIR_NAMES` でも除外）。
"""
from __future__ import annotations

import logging
import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from hangap import analysis, archive, sites as log_sites, table
from hangap.detector import RESULT_COLUMNS
from utils import fmt_dt

router = APIRouter(prefix="/api/hangap", tags=["hangap"])
logger = logging.getLogger(__name__)

#: 分析対象。このダッシュボードが収集したログだけを見る（アップロードは受け付けない）
LOGS_DIR = "/app/data/logs"

#: 分析結果の保存先。**``LOGS_DIR`` の配下に置かないこと**（入力として拾われる）
RESULTS_DIR = f"/app/data/{archive.RESULTS_DIR_NAME}"

#: 保持するジョブ数の上限（超えたら古いものから破棄する）
MAX_JOBS = 3
#: 完了からこの秒数が経過したジョブは自動破棄する
JOB_TTL_SECONDS = 3600
#: ジョブ 1 本の最大実行時間。超えたら failed にして枠を解放する。
#: 実測（250AP / 5,224ファイル）で 6.7 秒なので、10 分は十分な余裕がある。
MAX_RUN_SECONDS = 600

DEFAULT_RESULT_LIMIT = 100
MAX_RESULT_LIMIT = 1000

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

#: リクエストボディで受け付けるキー。これ以外は 400 にする（打ち間違いを黙って無視しない）
_BODY_FIELDS: frozenset[str] = frozenset({
    "from", "to", "sites",
    "min_zero_samples", "min_zero_duration", "event_window_minutes",
    "exodus_threshold", "gap_factor", "neighbor_count", "max_distance_m",
    "neighbor_client_threshold", "truncated_warn_ratio",
})

_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


# ---------------------------------------------------------------------------
# ジョブ
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    job_id: str
    params: analysis.AnalysisParams
    started_at: datetime
    #: 開始時点で確定させた入力ファイル一覧（ワーカーの最初の一歩で埋める）。
    #: 分析中に save_hourly_logs がファイルを足しても、この一覧は変わらない。
    files: list[Path] = field(default_factory=list)
    status: str = STATUS_RUNNING
    phase: str = analysis.PHASE_LOADING
    finished_at: datetime | None = None
    error: str | None = None
    result: pd.DataFrame | None = None
    summary: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    tmpdir: tempfile.TemporaryDirectory | None = None
    outputs: dict[str, Path] = field(default_factory=dict)
    discarded: bool = False
    #: 最大実行時間を超えて打ち切った。ワーカーは止められないので走り続けるが、
    #: 以後このジョブは「実行中」として数えず、遅れて出てきた結果も受け取らない。
    timed_out: bool = False


_JOBS: "OrderedDict[str, _Job]" = OrderedDict()
_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cleanup_tmpdir(td: tempfile.TemporaryDirectory | None, job_id: str) -> None:
    if td is None:
        return
    try:
        td.cleanup()
    except OSError as e:  # 消せなくてもジョブ管理は続ける
        logger.warning(f"hangap: temp dir cleanup failed for {job_id}: {e}")


def _discard(job: _Job) -> None:
    """ジョブの結果と一時ファイルを捨てる。呼び出し側で _LOCK を取ること。"""
    job.discarded = True
    job.result = None
    job.outputs = {}
    td, job.tmpdir = job.tmpdir, None
    _cleanup_tmpdir(td, job.job_id)


def _fail_timed_out() -> None:
    """最大実行時間を超えた実行中ジョブを failed にして**枠を解放する**。

    走っているスレッドは止められない（pandas の読み込み中に割り込む手段がない）。
    ここでできるのは「このジョブを実行中として数えるのをやめる」ことだけで、
    そうしないとワーカーがハングした時にコンテナ再起動以外の復旧手段が無くなる。
    遅れて出てきた結果を受け取らないよう ``timed_out`` を立てておく。
    """
    now = _now()
    with _LOCK:
        for job in _JOBS.values():
            if job.status != STATUS_RUNNING:
                continue
            elapsed = (now - job.started_at).total_seconds()
            if elapsed <= MAX_RUN_SECONDS:
                continue
            job.timed_out = True
            job.status = STATUS_FAILED
            job.finished_at = now
            job.error = (
                f"最大実行時間（{MAX_RUN_SECONDS} 秒）を超えました。分析を打ち切り、"
                "次の分析を開始できる状態に戻しました。"
                "これは「ハングが検出されなかった（0 件）」とは別の状態です。"
            )
            logger.warning(f"hangap: job {job.job_id} timed out after {elapsed:.0f}s")


def _sweep() -> None:
    """各リクエストの入口で行う後始末（タイムアウト → TTL の順）。"""
    _fail_timed_out()
    _purge_expired()


def _purge_expired() -> None:
    """完了から JOB_TTL_SECONDS を過ぎたジョブを破棄する（各リクエストの入口で呼ぶ）。"""
    now = _now()
    with _LOCK:
        for job_id, job in list(_JOBS.items()):
            if job.finished_at is None:
                continue
            if (now - job.finished_at).total_seconds() > JOB_TTL_SECONDS:
                _JOBS.pop(job_id, None)
                _discard(job)


def _running_job() -> _Job | None:
    with _LOCK:
        for job in _JOBS.values():
            if job.status == STATUS_RUNNING:
                return job
    return None


def _get_job(job_id: str) -> _Job:
    with _LOCK:
        job = _JOBS.get(job_id)
        # 破棄予約済みのジョブは、スレッドが終わるまで _JOBS に残るが利用者からは無い
        if job is None or job.discarded:
            raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")
    return job


def _set_phase(job: _Job, phase: str) -> None:
    with _LOCK:
        if job.timed_out:  # 打ち切り済みのジョブの進捗は書き換えない
            return
        job.phase = phase


@dataclass
class _Outcome:
    """ジョブに持たせる完了状態。**入力側は一切含めない。**"""

    result: pd.DataFrame
    summary: dict[str, Any]
    warnings: list[str]
    tmpdir: tempfile.TemporaryDirectory
    outputs: dict[str, Path]


def _analyze_and_write(job: _Job, files: list[Path]) -> _Outcome:
    """分析して出力ファイルを書き出す。

    読み込んだ入力（``ap_metrics`` などの DataFrame。実測で 50 万行規模）は
    :class:`analysis.AnalysisResult` ごとこの関数のスコープに閉じ込め、戻り値には
    結果（数百行）と summary だけを載せる。完了ジョブが入力を掴んだままにならない
    のはこの境界のためなので、``AnalysisResult`` をそのままジョブへ渡さないこと。
    """
    res = analysis.run_analysis(files, job.params, on_phase=lambda p: _set_phase(job, p))
    _set_phase(job, analysis.PHASE_WRITING)
    meta = res.meta()
    tmpdir = tempfile.TemporaryDirectory(prefix="hangap_job_")
    base = Path(tmpdir.name)
    stamp = job.started_at.strftime("%Y%m%d_%H%M%S")
    outputs = {
        "xlsx": analysis.write_xlsx(base / f"hangap_result_{stamp}.xlsx", res.result, meta),
        "csv": analysis.write_csv(base / f"hangap_result_{stamp}.csv", res.result),
        "summary": analysis.write_summary(base / f"hangap_result_{stamp}_summary.txt", meta),
    }
    return _Outcome(
        result=res.result,
        summary=_build_summary(res, meta),
        warnings=res.all_warnings,
        tmpdir=tmpdir,
        outputs=outputs,
    )


def _archive_outcome(job: _Job, outcome: _Outcome) -> None:
    """``done`` で完了した結果を ``data/hangap_results/`` に保存し、ローテートする。

    保存するのは **ジョブがすでに書き出した xlsx / csv をコピーしたもの**。書式は
    :mod:`hangap.analysis` の 1 箇所にしか無く、ここで作り直さない（ダウンロードで
    受け取るファイルと保存されるファイルが必ず同一になる）。

    保存に失敗しても分析そのものは成功しているので、例外はここで止めて done のまま
    返す（保存できないことを理由にジョブを failed にしない）。
    """
    try:
        name = archive.unique_name(RESULTS_DIR, job.started_at)
        meta = archive.build_meta(
            name=name,
            saved_at=_now(),
            summary=outcome.summary,
            warnings=outcome.warnings,
        )
        archive.save(RESULTS_DIR, name, outcome.outputs, meta)
        archive.rotate(RESULTS_DIR)
    except Exception as e:  # noqa: BLE001 - 保存の失敗でジョブを壊さない
        logger.warning(f"hangap: 分析結果を保存できませんでした（{job.job_id}）: {e}")


def _run_job(job: _Job) -> None:
    """ワーカースレッド本体。FastAPI のワーカーも APScheduler も止めない。"""
    try:
        # 入力ファイル一覧はここで確定させる。分析中に save_hourly_logs が
        # data/logs にファイルを足しても、このジョブの結果は変わらない。
        files = analysis.collect_files(LOGS_DIR)
        job.files = files
        outcome = _analyze_and_write(job, files)
        with _LOCK:
            if job.timed_out:
                # 打ち切り済み。枠はすでに解放されているので結果は受け取らない
                _cleanup_tmpdir(outcome.tmpdir, job.job_id)
                completed = False
            else:
                job.result = outcome.result
                job.summary = outcome.summary
                job.warnings = outcome.warnings
                job.tmpdir = outcome.tmpdir
                job.outputs = outcome.outputs
                job.status = STATUS_DONE
                completed = True
        # 保存は done のときだけ。failed（タイムアウト・ap_metrics 0 件など）では
        # 何も書かない（残しても「分析できなかった」記録にしかならない）。
        if completed:
            _archive_outcome(job, outcome)
        del outcome
    except analysis.AnalysisError as e:
        # ap_metrics を 1 行も読めなかった場合もここに来る。「検出 0 件」ではなく
        # 「そもそも分析対象が無かった」であり、done ではなく failed にする。
        with _LOCK:
            if not job.timed_out:
                job.status = STATUS_FAILED
                job.error = str(e)
    except Exception as e:  # noqa: BLE001 - ワーカースレッドで例外を落とさない
        logger.exception(f"hangap: job {job.job_id} failed")
        with _LOCK:
            if not job.timed_out:
                job.status = STATUS_FAILED
                job.error = f"分析中にエラーが発生しました: {e}"
    finally:
        with _LOCK:
            if not job.timed_out:  # 打ち切り時の finished_at は打ち切り時刻のまま
                job.finished_at = _now()
            job.files = []  # 入力ファイル一覧も完了時に手放す
            if job.discarded:  # 実行中に DELETE された。書き出したファイルも消す
                _JOBS.pop(job.job_id, None)
                _discard(job)


# ---------------------------------------------------------------------------
# サマリー
# ---------------------------------------------------------------------------


def _dt(value: object) -> str | None:
    """ログ由来の naive な時刻を文字列にする（UTC 変換はしない）。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _period(period: tuple | None) -> list[str | None] | None:
    if period is None:
        return None
    return [_dt(period[0]), _dt(period[1])]


def _build_summary(res: analysis.AnalysisResult, meta: analysis.Meta) -> dict[str, Any]:
    df = res.result
    report = res.report
    total = len(df)
    counts = df["回復状況"].value_counts()
    verdicts = df["周辺AP判定"].value_counts()

    return {
        "detected_intervals": total,
        # 回復状況の内訳
        "recovery_status": {s: int(counts.get(s, 0)) for s in analysis.STATUS_ORDER},
        # 周辺AP判定の内訳
        "neighbor_verdict": {v: int(verdicts.get(v, 0)) for v in analysis.VERDICT_ORDER},
        "exodus_suspected": int(df["退場疑い"].sum()) if total else 0,
        "event_matched_intervals": (
            int((df["AP Event（±30分）"] == "あり").sum()) if total else 0
        ),
        "condition_text": meta.condition_text,
        "result_summary_text": meta.result_summary_text,
        "loader": {
            "files_scanned": report.files_scanned,
            "gap_factor": report.gap_factor,
            "file_stats": [
                {
                    "file_type": key,
                    "files": st.files,
                    "rows": st.rows,
                    "duplicates_removed": st.duplicates_removed,
                    "loaded": st.loaded,
                }
                for key, st in sorted(report.file_stats.items())
            ],
            "unclassified": len(report.unclassified),
            "sampling_interval_seconds": report.overall_interval_seconds,
            "interval_groups": [
                {"interval_seconds": float(rep), "ap_count": int(n)}
                for rep, n in report.interval_groups
            ],
            "gaps": {
                "count": report.gaps.count,
                "total_seconds": report.gaps.total_seconds,
                "max_seconds": report.gaps.max_seconds,
                "total_missing_samples": report.gaps.total_missing_samples,
            },
            "metrics_period": _period(report.metrics_period),
            "events_period": _period(report.events_period),
            "metrics_rows": report.metrics_rows,
            "events_rows": report.events_rows,
            "ap_count": report.ap_count,
            "rf_neighbors_rows": report.rf_neighbors_rows,
            "rf_neighbors_latest": _dt(report.rf_neighbors_latest),
            "site_periods": [
                {
                    "site_id": sp.site_id,
                    "site_name": sp.site_name,
                    "rows": sp.rows,
                    "ap_count": sp.ap_count,
                    "first": _dt(sp.first),
                    "last": _dt(sp.last),
                }
                for sp in report.site_periods
            ],
            "report_text": report.render(),
        },
    }


# ---------------------------------------------------------------------------
# パラメータの検証（不正な値は必ず 400。どのフィールドかをメッセージに含める）
# ---------------------------------------------------------------------------


def _bad_request(field_name: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=f"{field_name}: {message}")


def _number(
    body: dict[str, Any],
    key: str,
    default: float,
    *,
    integer: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if body.get(key) is None:
        return default
    raw = body[key]
    if isinstance(raw, bool):
        raise _bad_request(key, f"数値で指定してください: {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise _bad_request(key, f"数値で指定してください: {raw!r}") from None
    if value != value:  # NaN
        raise _bad_request(key, f"数値で指定してください: {raw!r}")
    if integer:
        if value != int(value):
            raise _bad_request(key, f"整数で指定してください: {raw!r}")
        value = int(value)
    if minimum is not None and value < minimum:
        raise _bad_request(key, f"{minimum:g} 以上で指定してください: {raw!r}")
    if maximum is not None and value > maximum:
        raise _bad_request(key, f"{maximum:g} 以下で指定してください: {raw!r}")
    return value


def _time(body: dict[str, Any], key: str) -> pd.Timestamp | None:
    if body.get(key) is None:
        return None
    raw = body[key]
    if not isinstance(raw, str):
        raise _bad_request(key, f"文字列で指定してください: {raw!r}")
    try:
        return analysis.parse_time(raw, key, key)
    except analysis.ParamError as e:
        raise _bad_request(key, str(e)) from None


def _min_zero_duration(body: dict[str, Any]) -> pd.Timedelta | None:
    key = "min_zero_duration"
    raw = body.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise _bad_request(key, f"'30m' のような時間表記か分数で指定してください: {raw!r}")
    if isinstance(raw, (int, float)):
        if raw <= 0:
            raise _bad_request(key, f"0 より大きい値で指定してください: {raw!r}")
        return pd.Timedelta(minutes=float(raw))
    if not isinstance(raw, str):
        raise _bad_request(key, f"'30m' のような時間表記か分数で指定してください: {raw!r}")
    try:
        td = analysis.parse_duration(raw, key, key)
    except analysis.ParamError as e:
        raise _bad_request(key, str(e)) from None
    if td <= pd.Timedelta(0):
        raise _bad_request(key, f"0 より大きい値で指定してください: {raw!r}")
    return td


def _sites(body: dict[str, Any]) -> tuple[str, ...] | None:
    """対象サイト。**省略（null）はすべてのサイト**。

    空配列は 400 にする（「1 つも選んでいない」を「すべて」と読み替えると、
    指定漏れのまま全サイトが対象になってしまう）。
    """
    key = "sites"
    raw = body.get(key)
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise _bad_request(key, f"site_id の配列で指定してください: {raw!r}")
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise _bad_request(key, f"site_id は空でない文字列で指定してください: {value!r}")
        token = value.strip()
        if token not in out:
            out.append(token)
    if not out:
        raise _bad_request(
            key, "1 つ以上指定してください（すべてのサイトを対象にする場合は省略します）"
        )
    return tuple(out)


def _build_params(body: dict[str, Any] | None) -> analysis.AnalysisParams:
    """リクエストボディから分析条件を組み立てる。

    既定値は :class:`analysis.AnalysisParams` （= hangap 側の定数）をそのまま使う。
    ここで既定値を再定義しないこと（CLI と食い違う）。
    """
    body = body or {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="リクエストボディは JSON オブジェクトで指定してください")

    unknown = sorted(set(body) - _BODY_FIELDS)
    if unknown:
        raise _bad_request(", ".join(unknown), "不明なフィールドです")

    d = analysis.AnalysisParams()  # 既定値の参照元
    window_start = _time(body, "from")
    window_end = _time(body, "to")
    if window_start is not None and window_end is not None and window_start >= window_end:
        raise _bad_request("to", f"from より後の時刻を指定してください: {body['to']!r}")

    event_window_minutes = _number(
        body, "event_window_minutes", d.event_window.total_seconds() / 60, minimum=0
    )

    return analysis.AnalysisParams(
        window_start=window_start,
        window_end=window_end,
        sites=_sites(body),
        min_zero_samples=int(
            _number(body, "min_zero_samples", d.min_zero_samples, integer=True, minimum=1)
        ),
        min_zero_duration=_min_zero_duration(body),
        event_window=pd.Timedelta(minutes=event_window_minutes),
        exodus_threshold=_number(body, "exodus_threshold", d.exodus_threshold),
        gap_factor=_number(body, "gap_factor", d.gap_factor, minimum=0),
        neighbor_count=int(
            _number(body, "neighbor_count", d.neighbor_count, integer=True, minimum=0)
        ),
        max_distance_m=_number(body, "max_distance_m", d.max_distance_m, minimum=0),
        neighbor_client_threshold=_number(
            body, "neighbor_client_threshold", d.neighbor_client_threshold, minimum=0
        ),
        truncated_warn_ratio=_number(
            body, "truncated_warn_ratio", d.truncated_warn_ratio, minimum=0, maximum=1
        ),
    )


# ---------------------------------------------------------------------------
# 結果の整形
# ---------------------------------------------------------------------------


def _json_value(value: object) -> Any:
    """DataFrame のセルを JSON にできる値へ落とす（NaT / pd.NA / NaN は null）。"""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float) and value != value:
        return None
    return value


def _rows_to_json(df: pd.DataFrame) -> list[dict[str, Any]]:
    columns = list(RESULT_COLUMNS)
    return [
        {col: _json_value(val) for col, val in zip(columns, row)}
        for row in df[columns].itertuples(index=False, name=None)
    ]


def _select_rows(
    df: pd.DataFrame,
    *,
    offset: int,
    limit: int,
    status: str | None,
    sort: str | None,
    order: str,
    filters: list[str],
) -> tuple[int, pd.DataFrame]:
    """絞り込み → 並び替え → 切り出し。**絞り込みはサーバ側で行う**（ページングと併用するため）。

    実行中ジョブの結果でも保存済み結果でも同じ関数を通す（同じ指定が同じように効くこと）。
    ダウンロードはここを通らないので、絞り込みの影響を受けない。
    """
    if status is not None:
        if status not in analysis.STATUS_ORDER:
            raise _bad_request(
                "status", f"次のいずれかで指定してください: {', '.join(analysis.STATUS_ORDER)}"
            )
        df = df[df["回復状況"] == status]

    try:
        parsed = table.parse_filters(filters)
    except table.FilterError as e:
        raise _bad_request(e.field_name, str(e)) from None
    df = table.apply_filters(df, parsed)

    if sort is not None:
        if sort not in RESULT_COLUMNS:
            raise _bad_request("sort", f"結果に無い列です: {sort!r}")
        if order not in ("asc", "desc"):
            raise _bad_request("order", f"asc / desc で指定してください: {order!r}")
        df = df.sort_values(
            sort, ascending=(order == "asc"), kind="stable", na_position="last"
        )

    return len(df), df.iloc[offset:offset + limit]


def _rows_response(
    page: pd.DataFrame,
    total: int,
    *,
    job_id: str | None,
    name: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """結果テーブルのレスポンス。**実行中ジョブと保存済み結果で同じ形にすること。**

    フロントは同じコンポーネントで両方を表示するため、ここで形が分かれると
    表示側に分岐が増える。``job_id`` / ``name`` は出どころを示すだけで、
    どちらか一方が null になる。
    """
    return {
        "job_id": job_id,
        "name": name,
        "total": total,
        "offset": offset,
        "limit": limit,
        "columns": list(RESULT_COLUMNS),
        # 列ごとの絞り込みの入力方法。フロントで列の性質を定義し直さないために返す
        "column_kinds": dict(table.COLUMN_KINDS),
        "enum_choices": {col: list(v) for col, v in table.ENUM_CHOICES.items()},
        "rows": _rows_to_json(page),
    }


def _job_state(job: _Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "phase": job.phase,
        "started_at": fmt_dt(job.started_at),
        "finished_at": fmt_dt(job.finished_at),
        "error": job.error,
        "summary": job.summary,
        "warnings": job.warnings,
    }


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/sites")
def list_log_sites(
    refresh: bool = Query(False, description="キャッシュを捨ててログを読み直す"),
):
    """``data/logs`` に含まれるサイトを返す（分析対象の選択肢）。

    **``/api/sites``（現在の監視対象）からは作らない。** 環境を切り替えると
    ``data/logs`` には現在監視していないサイトのログが残るため、監視対象だけを
    選択肢にすると、そのログを分析できなくなる。

    走査結果はプロセス内にキャッシュする（入力ファイルが増減・更新されれば
    自動で作り直す。``?refresh=true`` で明示的に読み直す）。
    """
    _sweep()
    files = analysis.collect_files(LOGS_DIR)
    scan = log_sites.scan(files, refresh=refresh)
    return {
        "sites": [
            {
                "site_id": s.site_id,
                "site_name": s.site_name,
                "ap_count": s.ap_count,
                "rows": s.rows,
                "files": s.files,
                "first": s.first,
                "last": s.last,
            }
            for s in scan.sites
        ],
        "files_scanned": scan.files_scanned,
        "metrics_files": scan.metrics_files,
        "scanned_at": fmt_dt(scan.scanned_at),
        "cached": scan.cached,
    }


@router.post("/analyze", status_code=202)
def start_analysis(body: dict | None = Body(default=None)):
    """分析ジョブを開始する。同時に実行できるジョブは 1 つまで（実行中は 409）。"""
    _sweep()
    params = _build_params(body)

    # 実行中判定と登録は必ず同じロックの中で行う。分けると、2 本の POST が
    # どちらも「実行中なし」を見て 2 つ走ってしまう。
    with _LOCK:
        running = _running_job()
        if running is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "分析ジョブがすでに実行中です。完了を待つか、破棄してください。",
                    "job_id": running.job_id,
                },
            )
        job = _Job(job_id=uuid.uuid4().hex, params=params, started_at=_now())
        while len(_JOBS) >= MAX_JOBS:
            # status=running のジョブはこの時点で存在しない（すぐ上で 409 にしている）。
            # 打ち切り済み（timed_out）のジョブはスレッドが生き残っていることがあるが、
            # 枠としてはすでに解放済みなので、ここで捨ててよい。
            _, oldest = _JOBS.popitem(last=False)
            _discard(oldest)
        _JOBS[job.job_id] = job

    threading.Thread(
        target=_run_job, args=(job,), daemon=True, name=f"hangap-{job.job_id[:8]}"
    ).start()

    return {
        "job_id": job.job_id,
        "status": job.status,
        "started_at": fmt_dt(job.started_at),
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    """ジョブの状態・進捗・サマリーを返す。"""
    _sweep()
    return _job_state(_get_job(job_id))


@router.get("/jobs/{job_id}/result")
def get_job_result(
    job_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_RESULT_LIMIT, ge=1, le=MAX_RESULT_LIMIT),
    status: str | None = Query(None, description="回復状況で絞り込む"),
    sort: str | None = Query(None, description="並び替える列（RESULT_COLUMNS のいずれか）"),
    order: str = Query("asc", description="asc | desc"),
    filters: list[str] = Query(
        default_factory=list,
        alias="filter",
        description="列ごとの絞り込み（列名:演算子:値）。複数指定は AND",
    ),
):
    """結果テーブルを返す。列は detector.RESULT_COLUMNS と同一・同順。"""
    _sweep()
    job = _get_job(job_id)
    if job.status != STATUS_DONE or job.result is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"ジョブはまだ結果を返せません（status={job.status}）",
                "job_id": job.job_id,
                "status": job.status,
                "error": job.error,
            },
        )

    total, page = _select_rows(
        job.result,
        offset=offset, limit=limit, status=status, sort=sort, order=order, filters=filters,
    )
    return _rows_response(
        page, total, job_id=job.job_id, name=None, offset=offset, limit=limit
    )


@router.get("/jobs/{job_id}/download")
def download_job_result(job_id: str, format: str = Query("xlsx", description="xlsx | csv")):
    """CLI と同じ書式のファイルを返す（書き出しは hangap.analysis で共用）。"""
    _sweep()
    job = _get_job(job_id)
    if format not in _MEDIA_TYPES:
        raise _bad_request("format", f"xlsx / csv で指定してください: {format!r}")
    if job.status != STATUS_DONE:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"ジョブはまだ結果を返せません（status={job.status}）",
                "job_id": job.job_id,
                "status": job.status,
            },
        )
    path = job.outputs.get(format)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f"出力ファイルが見つかりません: {format}")
    return FileResponse(path, media_type=_MEDIA_TYPES[format], filename=path.name)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """ジョブの結果と一時ファイルを破棄する。"""
    _sweep()
    with _LOCK:
        job = _get_job(job_id)
        _discard(job)
        if job.status == STATUS_RUNNING:
            # 動いているスレッドは止められない。読み込みが終わるまでは「実行中」と
            # して数え続ける（ここで _JOBS から外すと、5,000 ファイルの読み込みが
            # 走ったまま次のジョブを開始できてしまう）。レジストリからは
            # ワーカーの finally が外す。
            logger.info(f"hangap: job {job_id} discarded while running")
        else:
            _JOBS.pop(job_id, None)
    return {"job_id": job_id, "deleted": True}


# ---------------------------------------------------------------------------
# 保存済みの分析結果（data/hangap_results/）
# ---------------------------------------------------------------------------


def _result_name(name: str) -> str:
    """``{name}`` を検証する。**ここを通ったものだけをパスに連結すること。**

    ``hangap_result_YYYYMMDD_HHMMSS`` 以外は 400。パス区切り・``..``・絶対パスは
    この形にマッチしないので、``RESULTS_DIR`` の外を指す名前は作れない。
    """
    if not archive.is_valid_name(name):
        raise _bad_request(
            "name", f"hangap_result_YYYYMMDD_HHMMSS の形式で指定してください: {name!r}"
        )
    return name


@router.get("/results")
def list_saved_results():
    """保存済みの分析結果を新しい順で返す（各要素は添えた json の内容 + サイズ）。"""
    return {"results": archive.list_results(RESULTS_DIR)}


@router.get("/results/{name}/rows")
def get_saved_result_rows(
    name: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_RESULT_LIMIT, ge=1, le=MAX_RESULT_LIMIT),
    status: str | None = Query(None, description="回復状況で絞り込む"),
    sort: str | None = Query(None, description="並び替える列（RESULT_COLUMNS のいずれか）"),
    order: str = Query("asc", description="asc | desc"),
    filters: list[str] = Query(
        default_factory=list,
        alias="filter",
        description="列ごとの絞り込み（列名:演算子:値）。複数指定は AND",
    ),
):
    """保存済みの結果を、``jobs/{job_id}/result`` と**同じ形式**で返す。

    保存済みの csv を読んで返すだけで、**再分析はしない**（保存した時点の結果を
    そのまま見せる）。ページング・ソート・絞り込みも実行中ジョブと同じ実装
    （:func:`_select_rows`）を通す。
    """
    name = _result_name(name)
    path = archive.member_path(RESULTS_DIR, name, ".csv")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"保存済みの結果が見つかりません: {name}.csv")
    try:
        df = table.read_result_csv(path)
    except (OSError, ValueError) as e:
        logger.warning(f"hangap: 保存済みの csv を読めません: {path.name}: {e}")
        raise HTTPException(
            status_code=409,
            detail=(
                f"保存済みの結果を読み込めませんでした（{name}.csv）。"
                "ダウンロードでファイルそのものを確認してください。"
            ),
        ) from None

    total, page = _select_rows(
        df,
        offset=offset, limit=limit, status=status, sort=sort, order=order, filters=filters,
    )
    return _rows_response(page, total, job_id=None, name=name, offset=offset, limit=limit)


@router.get("/results/{name}/download")
def download_saved_result(name: str, format: str = Query("xlsx", description="xlsx | csv")):
    """保存済みの xlsx / csv を返す（分析時に書き出したファイルそのもの）。"""
    name = _result_name(name)
    if format not in _MEDIA_TYPES:
        raise _bad_request("format", f"xlsx / csv で指定してください: {format!r}")
    path = archive.member_path(RESULTS_DIR, name, f".{format}")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"保存済みの結果が見つかりません: {name}.{format}")
    return FileResponse(path, media_type=_MEDIA_TYPES[format], filename=path.name)


@router.delete("/results/{name}")
def delete_saved_result(name: str):
    """保存済みの結果を 1 組（xlsx/csv/json）まとめて削除する。"""
    name = _result_name(name)
    for result_set in archive.list_sets(RESULTS_DIR):
        if result_set.name == name:
            freed = archive.delete_set(result_set)
            logger.info(f"hangap: 保存済みの結果を削除しました: {name} ({freed}B)")
            return {"name": name, "deleted": True, "freed_bytes": freed}
    raise HTTPException(status_code=404, detail=f"保存済みの結果が見つかりません: {name}")
