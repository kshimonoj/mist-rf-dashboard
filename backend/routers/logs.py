import io
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Snapshot

router = APIRouter()

LOGS_DIR = "/app/data/logs"
_SAFE_FILENAME = re.compile(
    r"^ap_metrics_\d{8}_\d{6}(_[A-Z]{2,6})?_manual\.csv$"
    r"|^ap_metrics_\d{8}_\d{4}(_[A-Z]{2,6})?\.csv$"
    r"|^ap_metrics_\d{8}_\d{6}(_[A-Z]{2,6})?\.csv$"
)


def _validate_filename(filename: str) -> None:
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")


@router.get("/api/logs")
async def list_logs() -> dict[str, Any]:
    if not os.path.isdir(LOGS_DIR):
        return {"files": [], "total_bytes": 0}
    files = []
    total_bytes = 0
    for f in sorted(os.listdir(LOGS_DIR), reverse=True):
        if not _SAFE_FILENAME.match(f):
            continue
        path = os.path.join(LOGS_DIR, f)
        stat = os.stat(path)
        size = stat.st_size
        files.append({
            "filename": f,
            "size_bytes": size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
        total_bytes += size
    return {"files": files, "total_bytes": total_bytes}


# NOTE: この route は /api/logs/{filename} より前に定義すること（パスの競合を避けるため）
@router.get("/api/logs/download-zip")
async def download_zip(files: str = Query(...)) -> StreamingResponse:
    filenames = [f.strip() for f in files.split(",") if f.strip()]
    if not filenames:
        raise HTTPException(status_code=400, detail="No files specified")
    for fn in filenames:
        _validate_filename(fn)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in filenames:
            path = os.path.join(LOGS_DIR, fn)
            if os.path.isfile(path):
                zf.write(path, fn)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ap_metrics_export.zip"'},
    )


@router.get("/api/logs/{filename}")
async def download_log(filename: str) -> FileResponse:
    _validate_filename(filename)
    path = os.path.join(LOGS_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        media_type="text/csv",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class DeleteLogsRequest(BaseModel):
    filenames: list[str]


@router.delete("/api/logs")
async def delete_logs(body: DeleteLogsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    for fn in body.filenames:
        _validate_filename(fn)
    deleted = 0
    for fn in body.filenames:
        path = os.path.join(LOGS_DIR, fn)
        if os.path.isfile(path):
            os.remove(path)
        db.query(Snapshot).filter_by(filename=fn).delete()
        deleted += 1
    db.commit()
    return {"deleted": deleted}
