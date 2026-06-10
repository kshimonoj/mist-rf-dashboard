import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import ClientMetrics
from utils import fmt_dt

router = APIRouter()
logger = logging.getLogger(__name__)


def _norm_mac(mac: str | None) -> str:
    """MAC アドレスをコロンなし小文字に正規化する。"""
    if not mac:
        return ""
    return mac.replace(":", "").replace("-", "").lower()


@router.get("/api/clients/{mac}/metrics")
async def get_client_metrics(
    mac: str,
    hours: int = 24,
    site_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """指定クライアント（MAC）の過去 N 時間の client_metrics を返す。
    MAC はコロンなし形式で照合する。"""
    norm = _norm_mac(mac)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    q = db.query(ClientMetrics).filter(
        func.lower(func.replace(func.replace(ClientMetrics.mac, ":", ""), "-", "")) == norm,
        ClientMetrics.timestamp >= since,
    )
    if site_id:
        q = q.filter(ClientMetrics.site_id == site_id)

    rows = q.order_by(ClientMetrics.timestamp.asc()).all()

    return [
        {
            "timestamp": fmt_dt(r.timestamp),
            "rssi": r.rssi,
            "snr": r.snr,
            "tx_rate": r.tx_rate,
            "rx_rate": r.rx_rate,
            "tx_bps": r.tx_bps,
            "rx_bps": r.rx_bps,
            "tx_bytes": r.tx_bytes,
            "rx_bytes": r.rx_bytes,
            "idle_time": r.idle_time,
            "band": r.band,
            "channel": r.channel,
            "ap_name": r.ap_name,
        }
        for r in rows
    ]


@router.get("/api/clients/list")
async def list_clients_for_filter(
    site_id: Optional[str] = None,
    ap_mac: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """History の Client Filter 用に、保存済み client_metrics から
    （任意で site_id / ap_mac で絞り込んだ）クライアント一覧を返す。"""
    q = db.query(
        ClientMetrics.mac,
        func.max(ClientMetrics.hostname).label("hostname"),
    )
    if site_id:
        q = q.filter(ClientMetrics.site_id == site_id)
    if ap_mac:
        norm = _norm_mac(ap_mac)
        q = q.filter(
            func.lower(func.replace(func.replace(ClientMetrics.ap_mac, ":", ""), "-", "")) == norm
        )

    rows = (
        q.filter(ClientMetrics.mac.isnot(None))
        .group_by(ClientMetrics.mac)
        .order_by(func.max(ClientMetrics.hostname))
        .all()
    )
    return [{"mac": r.mac, "hostname": r.hostname or ""} for r in rows]
