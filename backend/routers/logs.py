import csv
import io
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import scheduler as sched_module
from database import get_db
from models import Snapshot
from scheduler import FLOORMAP_SUMMARY_CSV_COLUMNS

router = APIRouter()

LOGS_DIR = "/app/data/logs"
_SAFE_FILENAME = re.compile(
    r"^ap_metrics_\d{8}_\d{6}(_[A-Z]{2,6})?_manual\.csv$"
    r"|^ap_metrics_\d{8}_\d{4}(_[A-Z]{2,6})?\.csv$"
    r"|^ap_metrics_\d{8}_\d{6}(_[A-Z]{2,6})?\.csv$"
    r"|^floormap_\d{8}_\d{4}(_[A-Z]{2,6})?_summary\.csv$"
    r"|^floormap_\d{8}_\d{6}(_[A-Z]{2,6})?_manual_summary\.csv$"
    r"|^sle_metrics_\d{8}_\d{4}(_[A-Z]{2,6})?\.csv$"
    r"|^sle_metrics_\d{8}_\d{6}(_[A-Z]{2,6})?\.csv$"
    r"|^client_metrics_\d{8}_\d{4}(_[A-Z]{2,6})?\.csv$"
    r"|^client_metrics_\d{8}_\d{6}(_[A-Z]{2,6})?\.csv$"
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


class FloorMapSaveRow(BaseModel):
    site_id: Optional[str] = None
    site_name: Optional[str] = None
    map_id: Optional[str] = None
    map_name: Optional[str] = None
    ap_name: Optional[str] = None
    mac: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    band_24_channel: Optional[int] = None
    band_24_bandwidth: Optional[int] = None
    band_24_power: Optional[float] = None
    band_24_noise_floor: Optional[float] = None
    band_5_channel: Optional[int] = None
    band_5_bandwidth: Optional[int] = None
    band_5_power: Optional[float] = None
    band_5_noise_floor: Optional[float] = None
    band_6_channel: Optional[int] = None
    band_6_bandwidth: Optional[int] = None
    band_6_power: Optional[float] = None
    band_6_noise_floor: Optional[float] = None
    num_clients: Optional[int] = 0
    x_m: Optional[float] = None
    y_m: Optional[float] = None


# NOTE: この2つの route は /api/logs/{filename} より前に定義すること（パスの競合を避けるため）
@router.post("/api/logs/floormap/save")
async def save_floormap_from_frontend(rows: list[FloorMapSaveRow]) -> dict[str, Any]:
    if not rows:
        return {"filename": None, "record_count": 0}

    now = datetime.now(timezone.utc)
    tz_obj = ZoneInfo(sched_module._app_timezone)
    now_local = now.astimezone(tz_obj)
    tz_abbr = now_local.strftime("%Z")
    ts_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

    # (site_name, map_name, band, channel) -> [ap_name, ...]
    groups: dict[tuple, list[str]] = {}
    for row in rows:
        site_name = row.site_name or ""
        map_name = row.map_name or ""
        ap_name = row.ap_name or ""
        for band, ch in [
            ("2.4G", row.band_24_channel),
            ("5G",   row.band_5_channel),
            ("6G",   row.band_6_channel),
        ]:
            if ch is None:
                continue
            key = (site_name, map_name, band, ch)
            groups.setdefault(key, []).append(ap_name)

    summary_rows = []
    for (site_name, map_name, band, channel), ap_names in groups.items():
        ap_count = len(ap_names)
        summary_rows.append({
            "timestamp": ts_str,
            "site_name": site_name,
            "map_name": map_name,
            "band": band,
            "channel": channel,
            "ap_count": ap_count,
            "ap_list": ",".join(ap_names),
            "has_interference": ap_count >= 2,
        })

    filename = f"floormap_{now_local.strftime('%Y%m%d_%H%M%S')}_{tz_abbr}_manual_summary.csv"
    filepath = os.path.join(LOGS_DIR, filename)
    os.makedirs(LOGS_DIR, exist_ok=True)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FLOORMAP_SUMMARY_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summary_rows)

    return {"filename": filename, "record_count": len(summary_rows)}


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
