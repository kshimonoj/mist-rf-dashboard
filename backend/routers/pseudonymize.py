"""仮名化ダウンロード（その場変換）。

仮名化版のファイルは作り置きしない。リクエストのたびに元ファイルを読んで変換し、
メモリ上で返す。事前生成すると ``data/logs`` の容量が倍になり、ローテートの対象が
1 系統増え、元ファイルとの食い違いという新しい不整合の余地が生まれるため。

一貫性（同じ AP は常に同じ仮名）はソルトとマッピングの永続化で担保している。
詳細は :mod:`pseudonymizer.service`。

**仮名化であって匿名化ではない。** AP 台数・接続端末数の規模やイベントの発生
パターンは残るため、再識別のリスクはゼロにならない。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from hangap import archive
from pseudonymizer import restore as restore_mod
from pseudonymizer import restore_service, service
from pseudonymizer.cli import CliError
from pseudonymizer.leakcheck import LeakCheckFailed
from pseudonymizer.restore import MissingMaterialError, RestoreError, UnsupportedFormatError
from pseudonymizer.salt import SaltError
from pseudonymizer.transforms import PseudonymizeError
from routers import hangap as hangap_router
from routers import logs as logs_router

router = APIRouter(prefix="/api/pseudonymize", tags=["pseudonymize"])
logger = logging.getLogger(__name__)

ZIP_NAME = "pseudonymized_logs.zip"
RESTORE_ZIP_NAME = "restored_files.zip"

#: 復元レポートを載せるヘッダー。値は UTF-8 JSON を base64 にしたもの
#: （ヘッダーに日本語をそのまま置けないため）。CORS で expose している。
RESTORE_REPORT_HEADER = "X-Restore-Report"

#: 拡張子 → Content-Type。分からないものはテキスト扱いにする。
_MEDIA_TYPES = {
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _content_disposition(filename: str) -> str:
    """ASCII 以外を含みうるファイル名を安全に載せる（RFC 5987）。"""
    return f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"


def _run(paths: list[Path]) -> list[service.Output]:
    """仮名化して結果を返す。失敗はすべて HTTP エラーに変換する。

    leak check の発火は 422。**検出した値そのものはメッセージに含めない**
    （列名・行番号・規則名だけを返す既存の実装をそのまま渡す）。
    """
    try:
        return service.pseudonymize_files(paths)
    except service.PseudonymizeInputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except CliError as e:
        raise HTTPException(status_code=400, detail=f"仮名化できませんでした: {e}") from None
    except LeakCheckFailed as e:
        logger.warning("pseudonymize: leak check failed (%d violation(s))", len(e.violations))
        raise HTTPException(
            status_code=422,
            detail=(
                "仮名化の検査（leak check）で変換漏れを検出したため、ファイルを返しませんでした。"
                f"\n{e}"
            ),
        ) from None
    except (PseudonymizeError, SaltError) as e:
        logger.warning("pseudonymize: failed: %s", e)
        raise HTTPException(status_code=500, detail=f"仮名化に失敗しました: {e}") from None


def _csv_response(out: service.Output) -> Response:
    return Response(
        content=out.content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(out.filename)},
    )


def _zip_response(outputs: list[service.Output]) -> Response:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for out in outputs:
            zf.writestr(out.filename, out.content)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(ZIP_NAME)},
    )


@router.get("/limits")
def get_limits() -> dict:
    """UI が上限を書き写さなくて済むように返す。"""
    return {
        "max_files": service.MAX_FILES,
        "restore_max_files": restore_service.MAX_FILES,
        "restore_max_upload_bytes": restore_service.MAX_UPLOAD_BYTES,
        "restore_extensions": sorted(restore_mod.SUPPORTED_EXTENSIONS),
    }


@router.get("/logs")
def download_pseudonymized_logs(
    files: str = Query(..., description="カンマ区切りのファイル名。複数指定すると ZIP で返す"),
):
    """History のログ CSV を仮名化して返す（1 件なら CSV、複数なら ZIP）。

    複数ファイルは **同一のソルト・マッピング** で変換するので、種別をまたいだ
    突合（ap_metrics と ap_events で同じ AP を追う、など）が保たれる。
    """
    names: list[str] = []
    for raw in files.split(","):
        name = raw.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise HTTPException(status_code=400, detail="ファイルが指定されていません")
    if len(names) > service.MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"一度に仮名化できるのは {service.MAX_FILES} 件までです"
                f"（指定: {len(names)} 件）。選択を減らしてください。"
            ),
        )
    # ログ API と同じファイル名検証を通す。ソルト・マッピングはこのパターンに
    # 一致しないので、ここから取り出すことはできない。
    for name in names:
        logs_router._validate_filename(name)

    paths = [Path(logs_router.LOGS_DIR) / name for name in names]
    for path in paths:
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"ファイルが見つかりません: {path.name}")

    outputs = _run(paths)
    return _csv_response(outputs[0]) if len(outputs) == 1 else _zip_response(outputs)


@router.get("/results/{name}")
def download_pseudonymized_result(
    name: str,
    format: str = Query("csv", description="csv のみ（xlsx は対象外）"),
):
    """保存済みの分析結果（csv）を仮名化して返す。

    xlsx は対象外。1〜3 行目がタイトル・分析条件・警告の自由記述で、そこに
    サイト名・site_id・時刻が入っており、列ベースのホワイトリストが効かないため。
    """
    if format != "csv":
        raise HTTPException(
            status_code=400,
            detail=(
                f"仮名化ダウンロードは csv のみ対応しています（指定: {format!r}）。"
                "xlsx はタイトル・分析条件の自由記述にサイト名や時刻が入るため対象外です。"
            ),
        )
    name = hangap_router._result_name(name)
    path = archive.member_path(hangap_router.RESULTS_DIR, name, ".csv")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"保存済みの結果が見つかりません: {name}.csv")

    return _csv_response(_run([path])[0])


# ---------------------------------------------------------------------------
# 復元（再識別）
# ---------------------------------------------------------------------------


def _report_header(report: restore_mod.RestoreReport) -> str:
    """レポートをヘッダーに載せられる形にする（UTF-8 JSON → base64）。"""
    raw = json.dumps(report.to_json(), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _restore_response(
    outputs: list[restore_service.RestoredFile], report: restore_mod.RestoreReport
) -> Response:
    headers = {RESTORE_REPORT_HEADER: _report_header(report)}
    if len(outputs) == 1:
        out = outputs[0]
        headers["Content-Disposition"] = _content_disposition(out.filename)
        return Response(
            content=out.content,
            media_type=_MEDIA_TYPES.get(
                Path(out.filename).suffix.lower(), "text/plain; charset=utf-8"
            ),
            headers=headers,
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for out in outputs:
            zf.writestr(out.filename, out.content)
    headers["Content-Disposition"] = _content_disposition(RESTORE_ZIP_NAME)
    return Response(content=buf.getvalue(), media_type="application/zip", headers=headers)


@router.post("/restore")
async def restore_files(
    files: list[UploadFile] = File(..., description="復元するファイル（加工後でも可）"),
    no_time: bool = Query(False, description="時刻を戻さず、識別子だけ戻す"),
):
    """アップロードされたファイルを復元して返す（1 件ならそのまま、複数なら ZIP）。

    **返すファイルは実名（AP名・サイト名・MAC・IP・実時刻）を含む。**

    アップロードは一時ディレクトリで処理し、``data/`` 配下には一切書かない。
    置換件数と「マッピングに無い仮名が残っていないか」は
    :data:`RESTORE_REPORT_HEADER` に載せて返す。
    """
    uploads: list[tuple[str, bytes]] = []
    total = 0
    for upload in files:
        data = await upload.read()
        total += len(data)
        if total > restore_service.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"アップロードできるのは合計 "
                    f"{restore_service.MAX_UPLOAD_BYTES // (1024 * 1024)}MB までです。"
                    "ファイルを分けてください。"
                ),
            )
        uploads.append((upload.filename or "", data))

    try:
        outputs, report = restore_service.restore_uploads(uploads, time_restore=not no_time)
    except UnsupportedFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except MissingMaterialError as e:
        # まだ一度も仮名化していないサーバ、または別環境のファイルを渡された場合。
        raise HTTPException(
            status_code=400,
            detail=f"復元に必要なソルト／マッピングがこのサーバにありません。\n{e}",
        ) from None
    except restore_service.RestoreInputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except (RestoreError, SaltError, PseudonymizeError) as e:
        logger.warning("restore: failed: %s", e)
        raise HTTPException(status_code=500, detail=f"復元に失敗しました: {e}") from None

    return _restore_response(outputs, report)
