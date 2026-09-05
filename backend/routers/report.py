"""横断レポート（PPTX）のオンデマンドジョブ API。

組み立ての本体は :mod:`report.analysis` / :mod:`report.builder` に集約されており
（CLI と同じ関数）、このルーターはジョブの生成・進捗・ダウンロードだけを行う。
ここでロジックを再実装すると CLI と UI で出来上がるレポートが食い違う。

- 生成は **リクエストされたときにだけ** 走る。定期実行はしない。
- **元データは各モジュールの保存済み分析結果だけ**（``data/hangap_results`` /
  ``data/floorpeak_results`` / ``data/rrm_results``）。新しい分析は走らせない。
- 生成した PPTX は保存しない（``*_results`` のような一覧を持たない）。ジョブの
  一時ディレクトリに置き、ダウンロードされたら TTL で消える。都度
  「選択 → 生成 → ダウンロード」で完結させる設計。
- 3 つとも未選択は 400（空のレポートは作らない）。
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

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from report import analysis
from utils import fmt_dt

router = APIRouter(prefix="/api/report", tags=["report"])
logger = logging.getLogger(__name__)

#: 各モジュールの保存先（``hangap_results`` などの親）。分析入力（``data/logs``）は読まない
DATA_DIR = "/app/data"

MAX_JOBS = 3
JOB_TTL_SECONDS = 3600
MAX_RUN_SECONDS = 300

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

#: リクエストボディで受け付けるキー。これ以外は 400 にする（打ち間違いを黙って無視しない）
_BODY_FIELDS: frozenset[str] = frozenset(analysis.SECTION_FIELDS.values())


# ---------------------------------------------------------------------------
# ジョブ
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    job_id: str
    params: analysis.ReportParams
    started_at: datetime
    status: str = STATUS_RUNNING
    phase: str = analysis.PHASE_LOADING
    finished_at: datetime | None = None
    error: str | None = None
    result: analysis.ReportResult | None = None
    output: Path | None = None
    tmpdir: tempfile.TemporaryDirectory | None = None
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
        logger.warning(f"report: temp dir cleanup failed for {job_id}: {e}")


def _discard(job: _Job) -> None:
    """ジョブの結果と一時ファイルを捨てる。呼び出し側で _LOCK を取ること。"""
    job.discarded = True
    job.result = None
    job.output = None
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
                f"最大実行時間（{MAX_RUN_SECONDS} 秒）を超えました。生成を打ち切り、"
                "次の生成を開始できる状態に戻しました。"
            )
            logger.warning(f"report: job {job.job_id} timed out after {elapsed:.0f}s")


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


def _run_job(job: _Job) -> None:
    """ワーカースレッド本体。FastAPI のワーカーも APScheduler も止めない。"""
    tmpdir: tempfile.TemporaryDirectory | None = None
    try:
        result = analysis.run_report(
            job.params,
            analysis.ResultsDirs.under(DATA_DIR),
            generated_at=job.started_at,
            on_phase=lambda p: _set_phase(job, p),
        )
        _set_phase(job, analysis.PHASE_WRITING)
        tmpdir = tempfile.TemporaryDirectory(prefix="report_job_")
        output = analysis.write_pptx(
            Path(tmpdir.name) / analysis.output_name(job.started_at), result
        )
        with _LOCK:
            if job.timed_out:
                _cleanup_tmpdir(tmpdir, job.job_id)
            else:
                job.result = result
                job.output = output
                job.tmpdir = tmpdir
                job.status = STATUS_DONE
            tmpdir = None
    except analysis.ReportError as e:
        with _LOCK:
            if not job.timed_out:
                job.status = STATUS_FAILED
                job.error = str(e)
    except Exception as e:  # noqa: BLE001 - ワーカースレッドで例外を落とさない
        logger.exception(f"report: job {job.job_id} failed")
        with _LOCK:
            if not job.timed_out:
                job.status = STATUS_FAILED
                job.error = f"レポートの生成中にエラーが発生しました: {e}"
    finally:
        _cleanup_tmpdir(tmpdir, job.job_id)
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


def _build_params(body: dict | None) -> analysis.ReportParams:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="リクエストボディは JSON オブジェクトで指定してください")

    unknown = sorted(set(body) - _BODY_FIELDS)
    if unknown:
        raise _bad_request(", ".join(unknown), "不明なフィールドです")

    values: dict[str, str | None] = {}
    for field_name in _BODY_FIELDS:
        raw = body.get(field_name)
        if raw is None:
            values[field_name] = None
            continue
        if not isinstance(raw, str):
            raise _bad_request(field_name, f"文字列で指定してください: {raw!r}")
        values[field_name] = raw.strip() or None

    params = analysis.ReportParams(**values)
    if not params.selected():
        raise HTTPException(
            status_code=400,
            detail=(
                "レポートに含める分析結果が 1 つも選ばれていません。"
                "Hang AP / Floor Peak / RRM のいずれかを選んでください"
            ),
        )
    return params


# ---------------------------------------------------------------------------
# レスポンスの整形
# ---------------------------------------------------------------------------


def _job_state(job: _Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "phase": job.phase,
        "started_at": fmt_dt(job.started_at),
        "finished_at": fmt_dt(job.finished_at),
        "error": job.error,
        "sections": _requested_sections(job.params),
    }


def _requested_sections(params: analysis.ReportParams) -> list[dict[str, str]]:
    """選ばれた章を **固定順** で返す（選んだ順序には依存しない）。"""
    return [
        {
            "section": section,
            "label": analysis.SECTION_LABELS[section],
            "name": params.name_for(section),
        }
        for section in params.selected()
    ]


def _result_response(job: _Job) -> dict[str, Any]:
    result = job.result
    assert result is not None and job.output is not None  # 呼び出し側で done を確認済み
    return {
        "job_id": job.job_id,
        "filename": job.output.name,
        "download_url": f"{router.prefix}/jobs/{job.job_id}/download",
        "generated_at": fmt_dt(result.generated_at),
        "slide_count": result.slide_count,
        "sections": [
            {
                "section": source.section,
                "label": source.label,
                "name": source.name,
                "saved_at": source.meta.get("saved_at"),
            }
            for source in result.sources
        ],
        "slides": [
            {"section": s.section, "kind": s.kind, "title": s.title} for s in result.slides
        ],
    }


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.post("/generate", status_code=202)
def start_generate(body: dict | None = Body(default=None)):
    """レポート生成ジョブを開始する。同時に実行できるジョブは 1 つまで（実行中は 409）。"""
    _sweep()
    params = _build_params(body)

    # 実行中判定と登録は必ず同じロックの中で行う（分けると 2 本走る）
    with _LOCK:
        running = _running_job()
        if running is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "レポート生成がすでに実行中です。完了を待つか、破棄してください。",
                    "job_id": running.job_id,
                },
            )
        job = _Job(job_id=uuid.uuid4().hex, params=params, started_at=_now())
        while len(_JOBS) >= MAX_JOBS:
            _, oldest = _JOBS.popitem(last=False)
            _discard(oldest)
        _JOBS[job.job_id] = job

    threading.Thread(
        target=_run_job, args=(job,), daemon=True, name=f"report-{job.job_id[:8]}"
    ).start()

    return {"job_id": job.job_id, "status": job.status, "started_at": fmt_dt(job.started_at)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    """ジョブの状態・進捗を返す。"""
    _sweep()
    return _job_state(_get_job(job_id))


@router.get("/jobs/{job_id}/result")
def get_job_result(job_id: str):
    """生成された PPTX のダウンロード URL と、含まれる章・スライドの一覧を返す。"""
    _sweep()
    job = _get_job(job_id)
    if job.status != STATUS_DONE or job.result is None or job.output is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"ジョブはまだ結果を返せません（status={job.status}）",
                "job_id": job.job_id,
                "status": job.status,
                "error": job.error,
            },
        )
    return _result_response(job)


@router.get("/jobs/{job_id}/download")
def download_job_result(job_id: str):
    """生成された PPTX をそのまま返す（CLI と同じ組み立て結果）。"""
    _sweep()
    job = _get_job(job_id)
    if job.status != STATUS_DONE or job.output is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"ジョブはまだ結果を返せません（status={job.status}）",
                "job_id": job.job_id,
                "status": job.status,
            },
        )
    if not job.output.is_file():
        raise HTTPException(status_code=404, detail="生成されたファイルが見つかりません")
    return FileResponse(job.output, media_type=PPTX_MEDIA_TYPE, filename=job.output.name)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """ジョブの結果と一時ファイルを破棄する。"""
    _sweep()
    with _LOCK:
        job = _get_job(job_id)
        _discard(job)
        if job.status == STATUS_RUNNING:
            # 動いているスレッドは止められない。レジストリからはワーカーの finally が外す
            logger.info(f"report: job {job_id} discarded while running")
        else:
            _JOBS.pop(job_id, None)
    return {"job_id": job_id, "deleted": True}
