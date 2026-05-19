import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from mist.client import MistClient, SLE_METRICS, parse_sle_metric
from models import ApMetrics, RadioConfigCurrent

router = APIRouter()
logger = logging.getLogger(__name__)

_METRIC_KEYS = ["capacity", "throughput", "coverage", "time_to_connect", "roaming", "ap_availability"]


async def _fetch_all_sle(fetch_coro_fn) -> dict[str, Any]:
    """6メトリクスを並列取得してパースする共通処理。"""
    results = await asyncio.gather(
        *[fetch_coro_fn(m) for m in SLE_METRICS],
        return_exceptions=True,
    )
    output: dict[str, Any] = {}
    for m_orig, m_key, r in zip(SLE_METRICS, _METRIC_KEYS, results):
        if isinstance(r, Exception):
            logger.warning(f"SLE fetch error for {m_orig}: {r}")
            output[m_key] = {"score": None, "impact_users": 0, "total_users": 0}
        else:
            output[m_key] = parse_sle_metric(r, m_orig)
    return output


@router.get("/api/sites/{site_id}/sle")
async def get_site_sle(site_id: str, duration: str = "1h") -> dict[str, Any]:
    client = MistClient()
    return await _fetch_all_sle(lambda m: client.get_site_sle(site_id, m, duration))


@router.get("/api/aps/{ap_id}/sle")
async def get_ap_sle(
    ap_id: str,
    duration: str = "1h",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    site_id = None
    rec = db.query(RadioConfigCurrent).filter_by(ap_id=ap_id).first()
    if rec:
        site_id = rec.site_id
    if not site_id:
        row = (
            db.query(ApMetrics)
            .filter_by(ap_id=ap_id)
            .order_by(ApMetrics.timestamp.desc())
            .first()
        )
        if row:
            site_id = row.site_id
    if not site_id:
        return {}

    client = MistClient()
    return await _fetch_all_sle(lambda m: client.get_ap_sle(site_id, ap_id, m, duration))
