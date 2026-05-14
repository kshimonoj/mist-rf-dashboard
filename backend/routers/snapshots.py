import csv
import io
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, FileResponse
from sqlalchemy.orm import Session

import scheduler as sched_module
from database import SessionLocal, get_db
from mist.client import MistClient
from models import ApMetrics, Snapshot
from scheduler import ALL_CSV_COLUMNS
from utils import fmt_dt, fmt_dt_tz

router = APIRouter()
logger = logging.getLogger(__name__)
LOGS_DIR = "/app/data/logs"
_SAFE = re.compile(
    r"^ap_metrics_\d{8}_\d{6}(_[A-Z]{2,6})?_manual\.csv$"
    r"|^ap_metrics_\d{8}_\d{4}(_[A-Z]{2,6})?\.csv$"
    r"|^ap_metrics_\d{8}_\d{6}(_[A-Z]{2,6})?\.csv$"
)



@router.post("/api/snapshots")
async def create_snapshot() -> dict[str, Any]:
    since = sched_module.last_log_saved_at
    now = datetime.now(timezone.utc)
    tz_obj = ZoneInfo(sched_module._app_timezone)
    now_local = now.astimezone(tz_obj)
    tz_abbr = now_local.strftime("%Z")

    client = MistClient()
    org_id = os.getenv("MIST_ORG_ID", "")
    sites = await client.get_sites(org_id)
    site_names = {s.get("id", ""): s.get("name", "") for s in sites}

    db: Session = SessionLocal()
    try:
        rows = (
            db.query(ApMetrics)
            .filter(ApMetrics.timestamp >= since)
            .order_by(ApMetrics.site_id, ApMetrics.ap_id, ApMetrics.timestamp)
            .all()
        )

        os.makedirs(LOGS_DIR, exist_ok=True)
        filename = f"ap_metrics_{now_local.strftime('%Y%m%d_%H%M%S')}_{tz_abbr}_manual.csv"
        filepath = os.path.join(LOGS_DIR, filename)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ALL_CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow({
                    "timestamp": fmt_dt_tz(r.timestamp, sched_module._app_timezone),
                    "site_id": r.site_id,
                    "site_name": site_names.get(r.site_id, ""),
                    "ap_id": r.ap_id,
                    "ap_name": r.ap_name,
                    "mac": r.mac,
                    "status": r.status,
                    "num_clients": r.num_clients,
                    "radio_24_channel": r.radio_24_channel,
                    "radio_24_bandwidth": r.radio_24_bandwidth,
                    "radio_24_tx_power": r.radio_24_tx_power,
                    "radio_24_utilization": r.radio_24_utilization,
                    "radio_24_util_tx": r.radio_24_util_tx,
                    "radio_24_util_rx_in_bss": r.radio_24_util_rx_in_bss,
                    "radio_24_util_non_wifi": r.radio_24_util_non_wifi,
                    "radio_24_noise_floor": r.radio_24_noise_floor,
                    "radio_5_channel": r.radio_5_channel,
                    "radio_5_bandwidth": r.radio_5_bandwidth,
                    "radio_5_tx_power": r.radio_5_tx_power,
                    "radio_5_utilization": r.radio_5_utilization,
                    "radio_5_util_tx": r.radio_5_util_tx,
                    "radio_5_util_rx_in_bss": r.radio_5_util_rx_in_bss,
                    "radio_5_util_non_wifi": r.radio_5_util_non_wifi,
                    "radio_5_noise_floor": r.radio_5_noise_floor,
                    "radio_6_channel": r.radio_6_channel,
                    "radio_6_bandwidth": r.radio_6_bandwidth,
                    "radio_6_tx_power": r.radio_6_tx_power,
                    "radio_6_utilization": r.radio_6_utilization,
                    "radio_6_util_tx": r.radio_6_util_tx,
                    "radio_6_util_rx_in_bss": r.radio_6_util_rx_in_bss,
                    "radio_6_util_non_wifi": r.radio_6_util_non_wifi,
                    "radio_6_noise_floor": r.radio_6_noise_floor,
                })

        stat = os.stat(filepath)
        site_count = len({r.site_id for r in rows})
        record_count = len(rows)

        snap = Snapshot(
            filename=filename,
            saved_at=now,
            triggered_by="manual",
            site_count=site_count,
            ap_count=record_count,
        )
        db.add(snap)
        db.commit()
        db.refresh(snap)

        sched_module.last_log_saved_at = now
        sched_module._persist_last_log_saved_at(now)

        return {
            "id": snap.id,
            "filename": snap.filename,
            "saved_at": fmt_dt(snap.saved_at),
            "triggered_by": snap.triggered_by,
            "site_count": snap.site_count,
            "ap_count": snap.ap_count,
            "size_bytes": stat.st_size,
        }
    except Exception as e:
        logger.error(f"Manual snapshot error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/api/snapshots")
async def list_snapshots(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    snaps = db.query(Snapshot).order_by(Snapshot.saved_at.desc()).all()
    result = []
    for s in snaps:
        path = os.path.join(LOGS_DIR, s.filename)
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        result.append({
            "id": s.id,
            "filename": s.filename,
            "saved_at": fmt_dt(s.saved_at),
            "triggered_by": s.triggered_by,
            "site_count": s.site_count,
            "ap_count": s.ap_count,
            "size_bytes": size,
        })
    return result


@router.get("/api/snapshots/{filename}/download")
async def download_snapshot(
    filename: str,
    site_id: Optional[str] = None,
    ap_id: Optional[str] = None,
) -> Response:
    if not _SAFE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(LOGS_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")

    if not site_id and not ap_id:
        return FileResponse(path, media_type="text/csv", filename=filename,
                            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            r for r in reader
            if (not site_id or r.get("site_id") == site_id)
            and (not ap_id or r.get("ap_id") == ap_id)
        ]
        fieldnames = reader.fieldnames or ALL_CSV_COLUMNS

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    suffix = ""
    if site_id:
        suffix += f"_{site_id[:8]}"
    if ap_id:
        suffix += f"_{ap_id[:8]}"
    dl_name = filename.replace(".csv", f"{suffix}_filtered.csv")

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
    )
