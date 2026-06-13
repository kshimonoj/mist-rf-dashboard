import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from analysis.engine import CATEGORIES, run_analysis
from analysis.impact import compute_config_impact
from analysis.recommendations import build_recommendations
from database import get_db
from models import AppSettings, Insight, RadioConfigChange
from utils import fmt_dt

router = APIRouter()
logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"critical": 0, "warning": 1}


def _issue_dict(r: Insight) -> dict[str, Any]:
    return {
        "id": r.id,
        "first_detected_at": fmt_dt(r.first_detected_at),
        "last_detected_at": fmt_dt(r.last_detected_at),
        "resolved_at": fmt_dt(r.resolved_at),
        "status": r.status,
        "category": r.category,
        "severity": r.severity,
        "site_id": r.site_id,
        "site_name": r.site_name,
        "target_type": r.target_type,
        "target_id": r.target_id,
        "target_name": r.target_name,
        "detail": r.detail,
        "recommendation": r.recommendation,
        "metrics_json": r.metrics_json,
    }


def _build_response(db: Session, view: str | None = None) -> dict[str, Any]:
    settings = db.query(AppSettings).first()
    analyzed_at = fmt_dt(settings.last_insights_analyzed_at) if settings else None

    active = db.query(Insight).filter(Insight.status == "active").all()
    active.sort(key=lambda r: (_SEVERITY_ORDER.get(r.severity, 9), r.category or "", r.site_name or ""))

    # summary は常に active 件数
    summary = {cat: 0 for cat in CATEGORIES}
    for r in active:
        if r.category in summary:
            summary[r.category] += 1

    if view == "history":
        rows = (
            db.query(Insight)
            .order_by(Insight.last_detected_at.desc())
            .all()
        )
        issues = [_issue_dict(r) for r in rows]
    else:
        issues = [_issue_dict(r) for r in active]

    return {
        "analyzed_at": analyzed_at,
        "summary": summary,
        "recommendations": build_recommendations(db),
        "issues": issues,
    }


@router.get("/api/insights")
async def get_insights(view: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """最新の分析結果を返す。view=history で resolved を含む全履歴を返す。"""
    return _build_response(db, view)


@router.post("/api/insights/analyze")
async def analyze_insights(db: Session = Depends(get_db)) -> dict[str, Any]:
    """オンデマンドで分析を実行し、実行後の結果を返す。"""
    run_analysis(db)
    return _build_response(db)


@router.get("/api/insights/config-changes")
async def list_recent_config_changes(days: int = 7, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """直近 N 日間の radio_config_changes を新しい順に返す（Insights ページ用）。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    changes = (
        db.query(RadioConfigChange)
        .filter(RadioConfigChange.detected_at >= since)
        .order_by(RadioConfigChange.detected_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": c.id,
            "ap_id": c.ap_id,
            "ap_name": c.ap_name,
            "site_id": c.site_id,
            "band": c.band,
            "changed_field": c.changed_field,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "detected_at": fmt_dt(c.detected_at),
        }
        for c in changes
    ]


@router.get("/api/insights/config-impact")
async def get_config_impact(change_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """指定の設定変更について変更前6h vs 後6h の影響分析結果を返す。"""
    change = db.query(RadioConfigChange).filter_by(id=change_id).first()
    if not change:
        raise HTTPException(status_code=404, detail="config change not found")
    return compute_config_impact(db, change)
