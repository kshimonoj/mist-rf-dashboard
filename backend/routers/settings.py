import json
import os
from typing import Any, Optional
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import AppSettings
from scheduler import scheduler
import scheduler as sched_module

router = APIRouter()

_current_polling: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "300"))
_current_log_minutes: int = 60
_current_retention_days: int = 30
_current_timezone: str = "Asia/Tokyo"
_monitored_site_ids: list[str] = []
_current_client_polling: int = 600

MIN_CLIENT_POLLING = 300


class SettingsUpdate(BaseModel):
    polling_interval_seconds: Optional[int] = Field(None, ge=30, le=3600)
    log_interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
    log_retention_days: Optional[int] = Field(None, ge=1, le=365)
    timezone: Optional[str] = None
    monitored_site_ids: Optional[list[str]] = None
    client_polling_interval_seconds: Optional[int] = None


@router.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return {
        "polling_interval_seconds": _current_polling,
        "log_interval_minutes": _current_log_minutes,
        "log_retention_days": _current_retention_days,
        "timezone": _current_timezone,
        "monitored_site_ids": _monitored_site_ids,
        "client_polling_interval_seconds": _current_client_polling,
    }


@router.post("/api/settings")
async def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    global _current_polling, _current_log_minutes, _current_retention_days, _current_timezone, _monitored_site_ids, _current_client_polling

    if body.client_polling_interval_seconds is not None:
        if body.client_polling_interval_seconds < MIN_CLIENT_POLLING:
            raise HTTPException(status_code=400, detail="クライアントポーリング間隔は最小5分（300秒）です")
        try:
            scheduler.reschedule_job(
                "poll_clients", trigger="interval", seconds=body.client_polling_interval_seconds
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Reschedule poll_clients failed: {e}")
        _current_client_polling = body.client_polling_interval_seconds
        sched_module._client_polling_interval_seconds = body.client_polling_interval_seconds
        row = db.query(AppSettings).first()
        if row:
            row.client_polling_interval_seconds = body.client_polling_interval_seconds
            db.commit()

    if body.polling_interval_seconds is not None:
        try:
            scheduler.reschedule_job("poll_mist", trigger="interval", seconds=body.polling_interval_seconds)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Reschedule poll_mist failed: {e}")
        _current_polling = body.polling_interval_seconds

    if body.log_interval_minutes is not None:
        try:
            scheduler.reschedule_job("hourly_csv_log", trigger="interval", minutes=body.log_interval_minutes)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Reschedule hourly_csv_log failed: {e}")
        _current_log_minutes = body.log_interval_minutes
        sched_module._log_interval_minutes = body.log_interval_minutes

    if body.log_retention_days is not None:
        _current_retention_days = body.log_retention_days
        sched_module._log_retention_days = body.log_retention_days

    if body.timezone is not None:
        if body.timezone not in available_timezones():
            raise HTTPException(status_code=400, detail="無効なタイムゾーンです")
        _current_timezone = body.timezone
        sched_module._app_timezone = body.timezone
        row = db.query(AppSettings).first()
        if row:
            row.timezone = body.timezone
            db.commit()

    if body.monitored_site_ids is not None:
        _monitored_site_ids = body.monitored_site_ids
        sched_module._monitored_site_ids = body.monitored_site_ids
        row = db.query(AppSettings).first()
        if row:
            row.monitored_site_ids = json.dumps(body.monitored_site_ids)
            db.commit()

    return {
        "polling_interval_seconds": _current_polling,
        "log_interval_minutes": _current_log_minutes,
        "log_retention_days": _current_retention_days,
        "timezone": _current_timezone,
        "monitored_site_ids": _monitored_site_ids,
        "client_polling_interval_seconds": _current_client_polling,
    }
