import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from mist.client import MistClient, RESTART_EVENT_TYPES
from models import ApEvent, ApMetrics, RadioConfigChange, RadioConfigCurrent
from radio_helpers import detect_band_source, overall_source
from scheduler import backfill_ap_events
from utils import fmt_dt

router = APIRouter()
logger = logging.getLogger(__name__)

async def _empty_list() -> list:
    return []


# hours がこの値以上（実質 30d ボタンのみ）は生データではなく1時間バケット集計を返す
AGGREGATE_THRESHOLD_HOURS = 168

# 1時間平均を取る数値系フィールド
_AVG_FIELDS = [
    "num_clients",
    "radio_24_utilization", "radio_24_util_tx", "radio_24_util_rx_in_bss",
    "radio_24_util_non_wifi", "radio_24_noise_floor", "radio_24_tx_power",
    "radio_5_utilization", "radio_5_util_tx", "radio_5_util_rx_in_bss",
    "radio_5_util_non_wifi", "radio_5_noise_floor", "radio_5_tx_power",
    "radio_6_utilization", "radio_6_util_tx", "radio_6_util_rx_in_bss",
    "radio_6_util_non_wifi", "radio_6_noise_floor", "radio_6_tx_power",
]

# バケット内の最新レコードの値をそのまま使う設定値系フィールド
_LAST_FIELDS = [
    "radio_24_channel", "radio_24_bandwidth",
    "radio_5_channel", "radio_5_bandwidth",
    "radio_6_channel", "radio_6_bandwidth",
    "status",
]


def _aggregate_hourly(rows: list[ApMetrics]) -> list[dict[str, Any]]:
    """timestamp 昇順の生レコードを1時間バケットに集計する。
    数値系は AVG、channel/bandwidth/status はバケット内最新値。キー構成は生データと同一。"""
    buckets: dict[datetime, list[ApMetrics]] = {}
    for r in rows:
        b = r.timestamp.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(b, []).append(r)

    out: list[dict[str, Any]] = []
    for b, rs in buckets.items():
        item: dict[str, Any] = {"timestamp": fmt_dt(b)}
        for f in _AVG_FIELDS:
            vals = [getattr(r, f) for r in rs if getattr(r, f) is not None]
            item[f] = round(sum(vals) / len(vals), 1) if vals else None
        last = rs[-1]
        for f in _LAST_FIELDS:
            item[f] = getattr(last, f)
        out.append(item)
    return out


@router.get("/api/aps/{ap_id}/metrics")
async def get_ap_metrics(ap_id: str, hours: int = 24, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(ApMetrics)
        .filter(ApMetrics.ap_id == ap_id, ApMetrics.timestamp >= since)
        .order_by(ApMetrics.timestamp.asc())
        .all()
    )
    if hours >= AGGREGATE_THRESHOLD_HOURS:
        return _aggregate_hourly(rows)
    return [
        {
            "timestamp": fmt_dt(r.timestamp),
            "num_clients": r.num_clients,
            "radio_24_channel": r.radio_24_channel,
            "radio_24_bandwidth": r.radio_24_bandwidth,
            "radio_24_utilization": r.radio_24_utilization,
            "radio_24_util_tx": r.radio_24_util_tx,
            "radio_24_util_rx_in_bss": r.radio_24_util_rx_in_bss,
            "radio_24_util_non_wifi": r.radio_24_util_non_wifi,
            "radio_24_noise_floor": r.radio_24_noise_floor,
            "radio_24_tx_power": r.radio_24_tx_power,
            "radio_5_channel": r.radio_5_channel,
            "radio_5_bandwidth": r.radio_5_bandwidth,
            "radio_5_utilization": r.radio_5_utilization,
            "radio_5_util_tx": r.radio_5_util_tx,
            "radio_5_util_rx_in_bss": r.radio_5_util_rx_in_bss,
            "radio_5_util_non_wifi": r.radio_5_util_non_wifi,
            "radio_5_noise_floor": r.radio_5_noise_floor,
            "radio_5_tx_power": r.radio_5_tx_power,
            "radio_6_channel": r.radio_6_channel,
            "radio_6_bandwidth": r.radio_6_bandwidth,
            "radio_6_utilization": r.radio_6_utilization,
            "radio_6_util_tx": r.radio_6_util_tx,
            "radio_6_util_rx_in_bss": r.radio_6_util_rx_in_bss,
            "radio_6_util_non_wifi": r.radio_6_util_non_wifi,
            "radio_6_noise_floor": r.radio_6_noise_floor,
            "radio_6_tx_power": r.radio_6_tx_power,
            "status": r.status,
        }
        for r in rows
    ]


@router.get("/api/aps/{ap_id}/clients")
async def get_ap_clients(ap_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """指定 AP に接続中の無線クライアント一覧をリアルタイムで返す。
    ap_id から site_id を DB で引き、サイトの stats/clients を取得して ap_id 一致分を返す。"""
    # site_id を DB から解決
    rec = db.query(RadioConfigCurrent).filter_by(ap_id=ap_id).first()
    site_id = rec.site_id if rec else None
    if not site_id:
        row = (
            db.query(ApMetrics)
            .filter_by(ap_id=ap_id)
            .order_by(ApMetrics.timestamp.desc())
            .first()
        )
        site_id = row.site_id if row else None
    if not site_id:
        return []

    client = MistClient()
    devices, clients = await asyncio.gather(
        client.get_site_devices_stats(site_id),
        client.get_site_clients(site_id),
    )
    ap_by_mac = {
        (d.get("mac") or "").lower(): {"id": d.get("id", ""), "name": d.get("name", "")}
        for d in devices
    }
    ap_name = next((d.get("name", "") for d in devices if d.get("id") == ap_id), "")

    # フロント（ApClientsSection）が利用するフィールドのみ allowlist で返す。
    # Mist の生オブジェクトをそのまま返さない（不要な上流フィールドの漏洩防止）。
    allowed = (
        "mac", "hostname", "manufacture", "os", "family", "model",
        "band", "channel", "rssi", "snr", "tx_rate", "rx_rate",
        "tx_bytes", "rx_bytes", "uptime", "ssid", "vlan_id", "key_mgmt",
    )
    result = []
    for c in clients:
        ap_info = ap_by_mac.get((c.get("ap_mac") or "").lower(), {})
        resolved_ap_id = c.get("ap_id") or ap_info.get("id", "")
        if resolved_ap_id != ap_id:
            continue
        item = {k: c.get(k) for k in allowed}
        item["ap_id"] = resolved_ap_id
        item["ap_name"] = ap_info.get("name", "") or ap_name
        result.append(item)
    return result


@router.get("/api/aps/{ap_id}/events")
async def get_ap_events(ap_id: str, hours: int = 24, db: Session = Depends(get_db)) -> dict[str, Any]:
    """該当 AP のイベント（再起動・DFS等）を ap_events テーブルから過去N時間分・新しい順で返す。
    mac は DB（ap_metrics 最新レコード）から解決する。Mist側の検索APIは約30日しか遡れないため、
    自前で蓄積した ap_events を参照する。"""
    row = (
        db.query(ApMetrics)
        .filter_by(ap_id=ap_id)
        .order_by(ApMetrics.timestamp.desc())
        .first()
    )
    if not row or not row.mac:
        return {"events": []}

    norm_mac = row.mac.replace(":", "").replace("-", "").lower()
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = (
        db.query(ApEvent)
        .filter(ApEvent.ap_mac == norm_mac, ApEvent.event_timestamp >= since)
        .order_by(ApEvent.event_timestamp.desc())
        .all()
    )
    return {
        "events": [
            {
                "timestamp": fmt_dt(e.event_timestamp),
                "type": e.event_type,
                "reason": e.reason,
                "band": e.band,
                "channel": e.channel,
                "pre_channel": e.pre_channel,
                "bandwidth": e.bandwidth,
                "pre_bandwidth": e.pre_bandwidth,
                "is_restart": e.event_type in RESTART_EVENT_TYPES,
            }
            for e in events
        ]
    }


@router.post("/api/ap-events/backfill")
async def backfill_ap_events_endpoint(days: int = Query(7, ge=1, le=30)) -> dict[str, Any]:
    """過去N日分のAPイベントを Mist API から一括取得し ap_events へ保存する（手動実行）。"""
    return await backfill_ap_events(days)


def _build_band_dict(b: dict) -> dict:
    return {
        "channel": b.get("channel"),
        "bandwidth": b.get("bandwidth"),
        "tx_power": b.get("power"),
        "disabled": bool(b.get("disabled", False)),
    }


@router.get("/api/aps/{ap_id}/radio-config")
async def get_ap_radio_config(
    ap_id: str,
    site_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not site_id:
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

    current_data: dict | None = None

    if site_id:
        try:
            client = MistClient()
            org_id = client.org_id
            dp_fut = client.get_org_device_profiles(org_id) if org_id else _empty_list()
            rf_fut = client.get_org_rf_templates(org_id) if org_id else _empty_list()
            ap_config, dp_list, rf_list, site_setting = await asyncio.gather(
                client.get_ap_radio_config(site_id, ap_id),
                dp_fut,
                rf_fut,
                client.get_site_setting(site_id),
            )

            if ap_config:
                radio_cfg = ap_config.get("radio_config", {}) or {}
                b24_raw = radio_cfg.get("band_24", {}) or {}
                b5_raw = radio_cfg.get("band_5", {}) or {}
                b6_raw = radio_cfg.get("band_6", {}) or {}
                ap_name = ap_config.get("name", "")
                dp_id = ap_config.get("deviceprofile_id") or ""

                dp_cache = {
                    d["id"]: {"name": d.get("name", ""), "radio_config": d.get("radio_config") or {}}
                    for d in (dp_list or []) if "id" in d
                }
                rftemplate_cache = {r["id"]: r.get("name", "") for r in (rf_list or []) if "id" in r}

                rftemplate_id = site_setting.get("rftemplate_id") or site_setting.get("rf_template_id") or ""
                dp_name = dp_cache.get(dp_id, {}).get("name", "") if dp_id else ""
                rf_name = rftemplate_cache.get(rftemplate_id, "") if rftemplate_id else ""

                source_24 = detect_band_source(ap_config, "24", dp_cache, rftemplate_cache, rftemplate_id or None)
                source_5 = detect_band_source(ap_config, "5", dp_cache, rftemplate_cache, rftemplate_id or None)
                source_6 = detect_band_source(ap_config, "6", dp_cache, rftemplate_cache, rftemplate_id or None)
                config_source = overall_source(source_24, source_5, source_6)

                b24, b5, b6 = b24_raw, b5_raw, b6_raw
                if not b24_raw and not b5_raw and not b6_raw:
                    stat_devices = await client.get_site_devices_stats(site_id)
                    for sd in stat_devices:
                        if sd.get("id") == ap_id:
                            rs = sd.get("radio_stat", {}) or {}
                            b24 = rs.get("band_24", {}) or {}
                            b5 = rs.get("band_5", {}) or {}
                            b6 = rs.get("band_6", {}) or {}
                            break

                existing = db.query(RadioConfigCurrent).filter_by(ap_id=ap_id).first()
                now = datetime.now(timezone.utc)
                new_vals = dict(
                    ap_name=ap_name,
                    site_id=site_id,
                    band_24_channel=b24.get("channel"),
                    band_24_bandwidth=b24.get("bandwidth"),
                    band_24_tx_power=b24.get("power"),
                    band_24_disabled=int(b24.get("disabled", False)),
                    band_5_channel=b5.get("channel"),
                    band_5_bandwidth=b5.get("bandwidth"),
                    band_5_tx_power=b5.get("power"),
                    band_5_disabled=int(b5.get("disabled", False)),
                    band_6_channel=b6.get("channel"),
                    band_6_bandwidth=b6.get("bandwidth"),
                    band_6_tx_power=b6.get("power"),
                    band_6_disabled=int(b6.get("disabled", False)),
                    config_source=config_source,
                    config_source_24=source_24,
                    config_source_5=source_5,
                    config_source_6=source_6,
                    deviceprofile_id=dp_id or None,
                    deviceprofile_name=dp_name or None,
                    rftemplate_id=rftemplate_id or None,
                    rftemplate_name=rf_name or None,
                    updated_at=now,
                )
                if existing:
                    for k, v in new_vals.items():
                        setattr(existing, k, v)
                else:
                    db.add(RadioConfigCurrent(ap_id=ap_id, **new_vals))
                db.commit()

                current_data = {
                    "ap_id": ap_id,
                    "ap_name": ap_name,
                    "site_id": site_id,
                    "config_source": config_source,
                    "config_source_24": source_24,
                    "config_source_5": source_5,
                    "config_source_6": source_6,
                    "deviceprofile_name": dp_name or None,
                    "rftemplate_name": rf_name or None,
                    "band_24": _build_band_dict(b24),
                    "band_5": _build_band_dict(b5),
                    "band_6": _build_band_dict(b6),
                }
        except Exception as e:
            logger.error(f"Failed to fetch radio config from Mist API for {ap_id}: {e}")

    if current_data is None:
        current = db.query(RadioConfigCurrent).filter_by(ap_id=ap_id).first()
        if current:
            current_data = {
                "ap_id": current.ap_id,
                "ap_name": current.ap_name,
                "site_id": current.site_id,
                "config_source": current.config_source,
                "config_source_24": current.config_source_24,
                "config_source_5": current.config_source_5,
                "config_source_6": current.config_source_6,
                "deviceprofile_name": current.deviceprofile_name,
                "rftemplate_name": current.rftemplate_name,
                "band_24": _build_band_dict({
                    "channel": current.band_24_channel,
                    "bandwidth": current.band_24_bandwidth,
                    "power": current.band_24_tx_power,
                    "disabled": current.band_24_disabled,
                }),
                "band_5": _build_band_dict({
                    "channel": current.band_5_channel,
                    "bandwidth": current.band_5_bandwidth,
                    "power": current.band_5_tx_power,
                    "disabled": current.band_5_disabled,
                }),
                "band_6": _build_band_dict({
                    "channel": current.band_6_channel,
                    "bandwidth": current.band_6_bandwidth,
                    "power": current.band_6_tx_power,
                    "disabled": current.band_6_disabled,
                }),
            }

    changes = (
        db.query(RadioConfigChange)
        .filter(RadioConfigChange.ap_id == ap_id)
        .order_by(RadioConfigChange.detected_at.desc())
        .limit(50)
        .all()
    )

    return {
        "current": current_data,
        "changes": [
            {
                "id": c.id,
                "detected_at": fmt_dt(c.detected_at),
                "band": c.band,
                "changed_field": c.changed_field,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "old_source": c.old_source,
                "new_source": c.new_source,
            }
            for c in changes
        ],
    }
