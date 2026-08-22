"""RRM / RADAR チャネル変更分析のオンデマンドジョブ API。

分析そのものは :mod:`rrm.analysis` に集約されており（CLI と同じ関数）、この
ルーターはジョブの生成・進捗・結果の取り出しだけを行う。ここでロジックを
再実装すると CLI と UI で結果が食い違うため、絶対に持ち込まないこと。

- 分析は **リクエストされたときにだけ** 走る。定期実行・常時分析はしない。
- 同時に走るジョブは 1 つまで。
- 結果はプロセス内に保持する。DB には保存しない（CSV ログだけで再現できる性質を保つ）。
- ジョブの一時ファイルは ``tempfile`` の一時ディレクトリに置く。``data/logs`` には
  **書かない**（混ざると次回の分析が自分の出力を読み込む）。
- ``done`` で完了した結果は ``data/rrm_results/`` に組（xlsx/csv/json）として保存し、
  :mod:`rrm.archive` がローテートする。保存先は ``data/logs`` の外で、
  :data:`hangap.loader.EXCLUDED_DIR_NAMES` でも除外している。
- **対象サイトは複数指定できる**（省略すると全サイト）。floorpeak と違い
  「サイト全体のピーク」のような単一サイト前提の定義が無く、サイト別比較を出すため。
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

from hangap import sites as log_sites
from rrm import analysis, archive, loader
from rrm.analysis import RESULT_COLUMNS
from utils import fmt_dt

router = APIRouter(prefix="/api/rrm", tags=["rrm"])
logger = logging.getLogger(__name__)

#: 分析対象。このダッシュボードが収集したログだけを見る（アップロードは受け付けない）
LOGS_DIR = "/app/data/logs"

#: 分析結果の保存先。**``LOGS_DIR`` の配下に置かないこと**（入力として拾われる）
RESULTS_DIR = f"/app/data/{archive.RESULTS_DIR_NAME}"

MAX_JOBS = 3
JOB_TTL_SECONDS = 3600
MAX_RUN_SECONDS = 600

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

#: リクエストボディで受け付けるキー。これ以外は 400 にする（打ち間違いを黙って無視しない）
_BODY_FIELDS: frozenset[str] = frozenset({"sites", "from", "to"})

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
    status: str = STATUS_RUNNING
    phase: str = analysis.PHASE_LOADING
    finished_at: datetime | None = None
    error: str | None = None
    rows: pd.DataFrame | None = None
    meta: dict[str, Any] | None = None
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
        logger.warning(f"rrm: temp dir cleanup failed for {job_id}: {e}")


def _discard(job: _Job) -> None:
    """ジョブの結果と一時ファイルを捨てる。呼び出し側で _LOCK を取ること。"""
    job.discarded = True
    job.rows = None
    job.outputs = {}
    td, job.tmpdir = job.tmpdir, None
    _cleanup_tmpdir(td, job.job_id)


def _fail_timed_out() -> None:
    """最大実行時間を超えた実行中ジョブを failed にして**枠を解放する**。"""
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
            )
            logger.warning(f"rrm: job {job.job_id} timed out after {elapsed:.0f}s")


def _purge_expired() -> None:
    now = _now()
    with _LOCK:
        for job_id, job in list(_JOBS.items()):
            if job.finished_at is None:
                continue
            if (now - job.finished_at).total_seconds() > JOB_TTL_SECONDS:
                _JOBS.pop(job_id, None)
                _discard(job)


def _sweep() -> None:
    """各リクエストの入口で行う後始末（タイムアウト → TTL の順）。"""
    _fail_timed_out()
    _purge_expired()


def _running_job() -> _Job | None:
    with _LOCK:
        for job in _JOBS.values():
            if job.status == STATUS_RUNNING:
                return job
    return None


def _get_job(job_id: str) -> _Job:
    with _LOCK:
        job = _JOBS.get(job_id)
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
    """ジョブに持たせる完了状態。**読み込んだ入力は一切含めない。**"""

    rows: pd.DataFrame
    meta: dict[str, Any]
    warnings: list[str]
    tmpdir: tempfile.TemporaryDirectory
    outputs: dict[str, Path]


def _analyze_and_write(job: _Job, files: list[Path]) -> _Outcome:
    """分析して出力ファイルを書き出す。

    読み込んだ入力（``ap_metrics`` / ``ap_events`` の DataFrame）は
    :class:`analysis.AnalysisResult` ごとこの関数のスコープに閉じ込め、戻り値には
    結果（明細行）と meta だけを載せる。
    """
    res = analysis.run_analysis(files, job.params, on_phase=lambda p: _set_phase(job, p))
    _set_phase(job, analysis.PHASE_WRITING)
    tmpdir = tempfile.TemporaryDirectory(prefix="rrm_job_")
    base = Path(tmpdir.name)
    stamp = job.started_at.strftime("%Y%m%d_%H%M%S")
    outputs = {
        "xlsx": analysis.write_xlsx(base / f"rrm_result_{stamp}.xlsx", res.rows, res.meta),
        "csv": analysis.write_csv(base / f"rrm_result_{stamp}.csv", res.rows),
        "summary": analysis.write_summary(base / f"rrm_result_{stamp}_summary.txt", res.meta),
    }
    return _Outcome(
        rows=res.rows, meta=res.meta, warnings=res.warnings, tmpdir=tmpdir, outputs=outputs,
    )


def _archive_outcome(job: _Job, outcome: _Outcome) -> None:
    """``done`` で完了した結果を ``data/rrm_results/`` に保存し、ローテートする。

    保存するのは **ジョブがすでに書き出した xlsx / csv をコピーしたもの**。
    保存に失敗しても分析そのものは成功しているので、例外はここで止める。
    """
    try:
        name = archive.unique_name(RESULTS_DIR, job.started_at)
        meta = archive.build_meta(
            name=name, saved_at=_now(), meta=outcome.meta, warnings=outcome.warnings,
        )
        archive.save(RESULTS_DIR, name, outcome.outputs, meta)
        archive.rotate(RESULTS_DIR)
    except Exception as e:  # noqa: BLE001 - 保存の失敗でジョブを壊さない
        logger.warning(f"rrm: 分析結果を保存できませんでした（{job.job_id}）: {e}")


def _run_job(job: _Job) -> None:
    """ワーカースレッド本体。FastAPI のワーカーも APScheduler も止めない。"""
    try:
        # 入力ファイル一覧はここで確定させる。分析中に定期収集がファイルを足しても
        # このジョブの結果は変わらない。
        files = loader.collect_files(LOGS_DIR)
        outcome = _analyze_and_write(job, files)
        with _LOCK:
            if job.timed_out:
                _cleanup_tmpdir(outcome.tmpdir, job.job_id)
                completed = False
            else:
                job.rows = outcome.rows
                job.meta = outcome.meta
                job.warnings = outcome.warnings
                job.tmpdir = outcome.tmpdir
                job.outputs = outcome.outputs
                job.status = STATUS_DONE
                completed = True
        if completed:
            _archive_outcome(job, outcome)
        del outcome
    except (analysis.AnalysisError, loader.LoadError) as e:
        # 対象 0 行もここに来る。「チャネル変更が無かった」ではなく「そもそも
        # 分析対象が無かった」であり、done ではなく failed にする。
        with _LOCK:
            if not job.timed_out:
                job.status = STATUS_FAILED
                job.error = str(e)
    except Exception as e:  # noqa: BLE001 - ワーカースレッドで例外を落とさない
        logger.exception(f"rrm: job {job.job_id} failed")
        with _LOCK:
            if not job.timed_out:
                job.status = STATUS_FAILED
                job.error = f"分析中にエラーが発生しました: {e}"
    finally:
        with _LOCK:
            if not job.timed_out:
                job.finished_at = _now()
            if job.discarded:  # 実行中に DELETE された。書き出したファイルも消す
                _JOBS.pop(job.job_id, None)
                _discard(job)


# ---------------------------------------------------------------------------
# パラメータの検証（不正な値は必ず 400。どのフィールドかをメッセージに含める）
# ---------------------------------------------------------------------------


def _bad_request(field_name: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=f"{field_name}: {message}")


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


def _sites(body: dict[str, Any]) -> tuple[str, ...]:
    """対象サイト。**複数指定できる**。省略・空リストなら全サイト。"""
    raw = body.get("sites")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _bad_request("sites", f"文字列の配列で指定してください: {raw!r}")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise _bad_request("sites", f"空でない文字列で指定してください: {item!r}")
        token = item.strip()
        if token not in out:
            out.append(token)
    return tuple(out)


def _build_params(body: dict | None) -> analysis.AnalysisParams:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="リクエストボディは JSON オブジェクトで指定してください")

    unknown = sorted(set(body) - _BODY_FIELDS)
    if unknown:
        raise _bad_request(", ".join(unknown), "不明なフィールドです")

    window_start = _time(body, "from")
    window_end = _time(body, "to")
    if window_start is not None and window_end is not None and window_start >= window_end:
        raise _bad_request("to", f"from より後の時刻を指定してください: {body['to']!r}")

    return analysis.AnalysisParams(
        sites=_sites(body), window_start=window_start, window_end=window_end,
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


def _rows_to_json(rows: pd.DataFrame) -> list[dict[str, Any]]:
    columns = list(RESULT_COLUMNS)
    return [
        {col: _json_value(val) for col, val in zip(columns, row)}
        for row in rows[columns].itertuples(index=False, name=None)
    ]


def _result_response(
    rows: pd.DataFrame,
    meta: dict[str, Any],
    warnings: list[str],
    *,
    job_id: str | None,
    name: str | None,
) -> dict[str, Any]:
    """結果のレスポンス。**実行中ジョブと保存済み結果で同じ形にすること。**"""
    return {
        "job_id": job_id,
        "name": name,
        "columns": list(RESULT_COLUMNS),
        "meta": meta,
        "warnings": warnings,
        "rows": _rows_to_json(rows),
    }


def _job_state(job: _Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "phase": job.phase,
        "started_at": fmt_dt(job.started_at),
        "finished_at": fmt_dt(job.finished_at),
        "error": job.error,
        "meta": job.meta,
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

    走査は Hang AP / Floor Peak と同じ :mod:`hangap.sites` を使う（同じログから
    同じ一覧が出るべきで、3 つ実装を持つ理由がない）。
    """
    _sweep()
    files = loader.collect_files(LOGS_DIR)
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

    # 実行中判定と登録は必ず同じロックの中で行う（分けると 2 本走る）
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
            _, oldest = _JOBS.popitem(last=False)
            _discard(oldest)
        _JOBS[job.job_id] = job

    threading.Thread(
        target=_run_job, args=(job,), daemon=True, name=f"rrm-{job.job_id[:8]}"
    ).start()

    return {"job_id": job.job_id, "status": job.status, "started_at": fmt_dt(job.started_at)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    """ジョブの状態・進捗・メタ情報を返す。"""
    _sweep()
    return _job_state(_get_job(job_id))


@router.get("/jobs/{job_id}/result")
def get_job_result(job_id: str):
    """結果（rows + meta + warnings）を返す。列は RESULT_COLUMNS と同一・同順。"""
    _sweep()
    job = _get_job(job_id)
    if job.status != STATUS_DONE or job.rows is None or job.meta is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"ジョブはまだ結果を返せません（status={job.status}）",
                "job_id": job.job_id,
                "status": job.status,
                "error": job.error,
            },
        )
    return _result_response(job.rows, job.meta, job.warnings, job_id=job.job_id, name=None)


@router.get("/jobs/{job_id}/download")
def download_job_result(
    job_id: str,
    format: str = Query("xlsx", description="xlsx | csv"),
):
    """CLI と同じ書式のファイルを返す（書き出しは rrm.analysis で共用）。"""
    _sweep()
    job = _get_job(job_id)
    if format not in _MEDIA_TYPES:
        raise _bad_request("format", f"xlsx / csv で指定してください: {format!r}")
    if job.status != STATUS_DONE or job.rows is None or job.meta is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"ジョブはまだ結果を返せません（status={job.status}）",
                "job_id": job.job_id,
                "status": job.status,
            },
        )
    stored = job.outputs.get(format)
    if stored is None or not stored.is_file():
        raise HTTPException(status_code=404, detail=f"出力ファイルが見つかりません: {format}")
    return FileResponse(stored, media_type=_MEDIA_TYPES[format], filename=stored.name)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """ジョブの結果と一時ファイルを破棄する。"""
    _sweep()
    with _LOCK:
        job = _get_job(job_id)
        _discard(job)
        if job.status == STATUS_RUNNING:
            # 動いているスレッドは止められない。レジストリからはワーカーの finally が外す
            logger.info(f"rrm: job {job_id} discarded while running")
        else:
            _JOBS.pop(job_id, None)
    return {"job_id": job_id, "deleted": True}


# ---------------------------------------------------------------------------
# 保存済みの分析結果（data/rrm_results/）
# ---------------------------------------------------------------------------


def _result_name(name: str) -> str:
    """``{name}`` を検証する。**ここを通ったものだけをパスに連結すること。**"""
    if not archive.is_valid_name(name):
        raise _bad_request(
            "name", f"rrm_result_YYYYMMDD_HHMMSS の形式で指定してください: {name!r}"
        )
    return name


def _load_saved(name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """保存済みの csv と json を読む（**再分析はしない**）。"""
    path = archive.member_path(RESULTS_DIR, name, ".csv")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"保存済みの結果が見つかりません: {name}.csv")
    try:
        rows = analysis.read_result_csv(path)
    except (OSError, ValueError) as e:
        logger.warning(f"rrm: 保存済みの csv を読めません: {path.name}: {e}")
        raise HTTPException(
            status_code=409,
            detail=(
                f"保存済みの結果を読み込めませんでした（{name}.csv）。"
                "ダウンロードでファイルそのものを確認してください。"
            ),
        ) from None
    meta: dict[str, Any] = {}
    for result_set in archive.list_sets(RESULTS_DIR):
        if result_set.name == name:
            meta = archive.describe(result_set)
            break
    return rows, meta


@router.get("/results")
def list_saved_results():
    """保存済みの分析結果を新しい順で返す（各要素は添えた json の内容 + サイズ）。"""
    return {"results": archive.list_results(RESULTS_DIR)}


@router.get("/results/{name}/rows")
def get_saved_result_rows(name: str):
    """保存済みの結果を、``jobs/{job_id}/result`` と**同じ形式**で返す。

    保存済みの csv / json を読んで返すだけで、**再分析はしない**。
    """
    name = _result_name(name)
    rows, meta = _load_saved(name)
    return _result_response(rows, meta, list(meta.get("warnings") or []), job_id=None, name=name)


@router.get("/results/{name}/download")
def download_saved_result(
    name: str,
    format: str = Query("xlsx", description="xlsx | csv"),
):
    """保存済みの xlsx / csv をそのまま返す。"""
    name = _result_name(name)
    if format not in _MEDIA_TYPES:
        raise _bad_request("format", f"xlsx / csv で指定してください: {format!r}")
    stored = archive.member_path(RESULTS_DIR, name, f".{format}")
    if not stored.is_file():
        raise HTTPException(
            status_code=404, detail=f"保存済みの結果が見つかりません: {name}.{format}"
        )
    return FileResponse(stored, media_type=_MEDIA_TYPES[format], filename=stored.name)


@router.delete("/results/{name}")
def delete_saved_result(name: str):
    """保存済みの結果を 1 組（xlsx/csv/json）まとめて削除する。"""
    name = _result_name(name)
    for result_set in archive.list_sets(RESULTS_DIR):
        if result_set.name == name:
            freed = archive.delete_set(result_set)
            logger.info(f"rrm: 保存済みの結果を削除しました: {name} ({freed}B)")
            return {"name": name, "deleted": True, "freed_bytes": freed}
    raise HTTPException(status_code=404, detail=f"保存済みの結果が見つかりません: {name}")
