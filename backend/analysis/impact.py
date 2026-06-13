"""設定変更 Before/After 影響分析（Insights Phase 2）。

radio_config_changes の 1 件の変更について、変更前 6h と変更後 6h の
ap_metrics / client_metrics を比較し improved / degraded / neutral を判定する。
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import ApMetrics, ClientMetrics, RadioConfigChange
from utils import fmt_dt

logger = logging.getLogger(__name__)

WINDOW_HOURS = 6
MIN_AFTER_HOURS = 1
CHANGE_THRESHOLD_PCT = 10  # 相対変化率がこれ以下なら neutral

# RadioConfigChange.band ('2.4G'/'5G'/'6G') → カラム接尾辞 / client_metrics の band 値
_BAND_MAP = {"2.4G": "24", "5G": "5", "6G": "6"}

# (key, label, unit, higher_is_better)
# util / noise / retry は低下 = improved、num_clients は増加 = improved
_METRIC_DEFS = [
    ("util_all", "util_all", "%", False),
    ("util_non_wifi", "util_non_wifi", "%", False),
    ("noise_floor", "noise_floor", "dBm", False),
    ("num_clients", "num_clients", "", True),
    ("retry_rate", "retry率", "%", False),
]


def _avg(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _collect_ap_window(db: Session, ap_id: str, band: str, start: datetime, end: datetime) -> dict:
    rows = (
        db.query(ApMetrics)
        .filter(ApMetrics.ap_id == ap_id, ApMetrics.timestamp >= start, ApMetrics.timestamp < end)
        .all()
    )
    return {
        "util_all": _avg([getattr(r, f"radio_{band}_utilization") for r in rows]),
        "util_non_wifi": _avg([getattr(r, f"radio_{band}_util_non_wifi") for r in rows]),
        "noise_floor": _avg([getattr(r, f"radio_{band}_noise_floor") for r in rows]),
        "num_clients": _avg([r.num_clients for r in rows]),
        "row_count": len(rows),
    }


def _retry_rate(db: Session, ap_id: str, band: str, start: datetime, end: datetime) -> float | None:
    """該当 AP・該当 band の接続クライアントの tx_retries / tx_pkts 合算（%）。"""
    rows = (
        db.query(ClientMetrics.tx_pkts, ClientMetrics.tx_retries)
        .filter(
            ClientMetrics.ap_id == ap_id,
            ClientMetrics.band == band,
            ClientMetrics.timestamp >= start,
            ClientMetrics.timestamp < end,
        )
        .all()
    )
    pkts = sum(r.tx_pkts or 0 for r in rows)
    retries = sum(r.tx_retries or 0 for r in rows)
    return retries / pkts * 100 if pkts > 0 else None


def _judge(before: float | None, after: float | None, higher_is_better: bool) -> tuple[float | None, str]:
    """相対変化率(%)と improved / degraded / neutral / no_data を返す。"""
    if before is None or after is None:
        return None, "no_data"
    if before == 0:
        if after == 0:
            return 0.0, "neutral"
        return None, "no_data"  # 相対変化率を定義できない
    change_pct = (after - before) / abs(before) * 100
    if abs(change_pct) <= CHANGE_THRESHOLD_PCT:
        return change_pct, "neutral"
    improved = (change_pct > 0) == higher_is_better
    return change_pct, "improved" if improved else "degraded"


def compute_config_impact(db: Session, change: RadioConfigChange) -> dict:
    """1 件の設定変更について before/after 比較結果を返す。"""
    band = _BAND_MAP.get(change.band or "")
    detected = change.detected_at
    if detected.tzinfo is None:
        detected = detected.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    before_start = detected - timedelta(hours=WINDOW_HOURS)
    after_end = min(now, detected + timedelta(hours=WINDOW_HOURS))
    after_hours = max((after_end - detected).total_seconds() / 3600, 0)

    base = {
        "change_id": change.id,
        "ap_id": change.ap_id,
        "ap_name": change.ap_name,
        "site_id": change.site_id,
        "band": change.band,
        "changed_field": change.changed_field,
        "old_value": change.old_value,
        "new_value": change.new_value,
        "detected_at": fmt_dt(change.detected_at),
        "before_hours": float(WINDOW_HOURS),
        "after_hours": round(after_hours, 1),
    }

    if band is None or after_hours < MIN_AFTER_HOURS:
        return {**base, "verdict": "insufficient_data", "metrics": []}

    before = _collect_ap_window(db, change.ap_id, band, before_start, detected)
    after = _collect_ap_window(db, change.ap_id, band, detected, after_end)
    if before["row_count"] == 0 or after["row_count"] == 0:
        return {**base, "verdict": "insufficient_data", "metrics": []}

    before["retry_rate"] = _retry_rate(db, change.ap_id, band, before_start, detected)
    after["retry_rate"] = _retry_rate(db, change.ap_id, band, detected, after_end)

    metrics = []
    improved_count = 0
    degraded_count = 0
    for key, label, unit, higher_is_better in _METRIC_DEFS:
        b, a = before[key], after[key]
        change_pct, judgment = _judge(b, a, higher_is_better)
        if judgment == "improved":
            improved_count += 1
        elif judgment == "degraded":
            degraded_count += 1
        metrics.append({
            "key": key,
            "label": label,
            "unit": unit,
            "before": round(b, 1) if b is not None else None,
            "after": round(a, 1) if a is not None else None,
            "change_pct": round(change_pct, 1) if change_pct is not None else None,
            "judgment": judgment,
        })

    if improved_count > degraded_count:
        verdict = "improved"
    elif degraded_count > improved_count:
        verdict = "degraded"
    else:
        verdict = "neutral"

    return {**base, "verdict": verdict, "metrics": metrics}
