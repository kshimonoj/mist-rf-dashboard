import asyncio
import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from database import SessionLocal
from mist.client import MistClient
from models import AppSettings, ApMetrics, RadioConfigChange, RadioConfigCurrent, Snapshot
from radio_helpers import detect_band_source, overall_source
from utils import fmt_dt, fmt_dt_tz

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
_log_interval_minutes: int = 60
_log_retention_days: int = 30
_app_timezone: str = "Asia/Tokyo"
_monitored_site_ids: list[str] = []
last_log_saved_at: datetime = datetime.now(timezone.utc)


def _persist_last_log_saved_at(dt: datetime) -> None:
    """last_log_saved_at を AppSettings テーブルに永続化する。"""
    db: Session = SessionLocal()
    try:
        row = db.query(AppSettings).first()
        if row:
            row.last_log_saved_at = dt
            db.commit()
    except Exception as e:
        logger.error(f"Failed to persist last_log_saved_at: {e}")
    finally:
        db.close()

LOGS_DIR = "/app/data/logs"
_MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500 MB

FLOORMAP_SUMMARY_CSV_COLUMNS = [
    "timestamp", "site_name", "map_name", "band", "channel",
    "ap_count", "ap_list", "has_interference",
]

ALL_CSV_COLUMNS = [
    "timestamp", "site_id", "site_name", "ap_id", "ap_name", "model", "mac", "status",
    "num_clients",
    "radio_24_channel", "radio_24_bandwidth", "radio_24_tx_power",
    "radio_24_utilization", "radio_24_util_tx", "radio_24_util_rx_in_bss", "radio_24_util_non_wifi",
    "radio_24_noise_floor",
    "radio_5_channel", "radio_5_bandwidth", "radio_5_tx_power",
    "radio_5_utilization", "radio_5_util_tx", "radio_5_util_rx_in_bss", "radio_5_util_non_wifi",
    "radio_5_noise_floor",
    "radio_6_channel", "radio_6_bandwidth", "radio_6_tx_power",
    "radio_6_utilization", "radio_6_util_tx", "radio_6_util_rx_in_bss", "radio_6_util_non_wifi",
    "radio_6_noise_floor",
]


def _extract_radio_stats(device: dict, band_key: str) -> dict:
    stats = device.get("radio_stat", {}) or {}
    band = stats.get(band_key, {}) or {}
    return {
        "channel": band.get("channel"),
        "bandwidth": band.get("bandwidth"),
        "util": band.get("util_all"),
        "util_tx": band.get("util_tx"),
        "util_rx_in_bss": band.get("util_rx_in_bss"),
        "util_non_wifi": band.get("util_non_wifi"),
        "noise_floor": band.get("noise_floor"),
        "tx_power": band.get("power"),
    }


def _detect_and_record_changes(
    db,
    existing: RadioConfigCurrent,
    ap_id: str,
    site_id: str,
    ap_name: str,
    detected_at: datetime,
    b24: dict,
    b5: dict,
    b6: dict,
    source_24: str,
    source_5: str,
    source_6: str,
) -> None:
    """前回の設定と比較し、変化があれば radio_config_changes に記録する。"""
    bands = [
        ("2.4G",
         existing.band_24_channel, b24.get("channel"),
         existing.band_24_bandwidth, b24.get("bandwidth"),
         existing.band_24_tx_power, b24.get("power"),
         existing.config_source_24, source_24),
        ("5G",
         existing.band_5_channel, b5.get("channel"),
         existing.band_5_bandwidth, b5.get("bandwidth"),
         existing.band_5_tx_power, b5.get("power"),
         existing.config_source_5, source_5),
        ("6G",
         existing.band_6_channel, b6.get("channel"),
         existing.band_6_bandwidth, b6.get("bandwidth"),
         existing.band_6_tx_power, b6.get("power"),
         existing.config_source_6, source_6),
    ]
    for (band_lbl, old_ch, new_ch, old_bw, new_bw, old_tx, new_tx, old_src, new_src) in bands:
        # config_source の変化（初回: old_src が None の場合はスキップ）
        if old_src is not None and old_src != new_src:
            db.add(RadioConfigChange(
                ap_id=ap_id, site_id=site_id, ap_name=ap_name,
                detected_at=detected_at, band=band_lbl,
                changed_field="config_source",
                old_value=old_src, new_value=new_src,
                old_source=old_src, new_source=new_src,
            ))
        # channel の変化
        if old_ch is not None and old_ch != new_ch:
            db.add(RadioConfigChange(
                ap_id=ap_id, site_id=site_id, ap_name=ap_name,
                detected_at=detected_at, band=band_lbl,
                changed_field="channel",
                old_value=str(old_ch), new_value=str(new_ch) if new_ch is not None else None,
                old_source=old_src, new_source=new_src,
            ))
        # bandwidth の変化
        if old_bw is not None and old_bw != new_bw:
            db.add(RadioConfigChange(
                ap_id=ap_id, site_id=site_id, ap_name=ap_name,
                detected_at=detected_at, band=band_lbl,
                changed_field="bandwidth",
                old_value=str(old_bw), new_value=str(new_bw) if new_bw is not None else None,
                old_source=old_src, new_source=new_src,
            ))
        # tx_power の変化
        if old_tx is not None and old_tx != new_tx:
            db.add(RadioConfigChange(
                ap_id=ap_id, site_id=site_id, ap_name=ap_name,
                detected_at=detected_at, band=band_lbl,
                changed_field="tx_power",
                old_value=str(old_tx), new_value=str(new_tx) if new_tx is not None else None,
                old_source=old_src, new_source=new_src,
            ))


def rotate_logs(retention_days: int) -> None:
    """古いログを日数基準と500MBキャップで削除する"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    db: Session = SessionLocal()
    try:
        old_snaps = db.query(Snapshot).filter(Snapshot.saved_at < cutoff).all()
        for snap in old_snaps:
            path = os.path.join(LOGS_DIR, snap.filename)
            if os.path.isfile(path):
                os.remove(path)
                logger.info(f"[ROTATE] Deleted (age): {snap.filename}")
            db.delete(snap)
        if old_snaps:
            db.commit()

        if not os.path.isdir(LOGS_DIR):
            return
        total = sum(
            os.path.getsize(os.path.join(LOGS_DIR, f))
            for f in os.listdir(LOGS_DIR)
            if os.path.isfile(os.path.join(LOGS_DIR, f))
        )
        if total <= _MAX_TOTAL_BYTES:
            return
        excess = db.query(Snapshot).order_by(Snapshot.saved_at.asc()).all()
        for snap in excess:
            if total <= _MAX_TOTAL_BYTES:
                break
            path = os.path.join(LOGS_DIR, snap.filename)
            if os.path.isfile(path):
                total -= os.path.getsize(path)
                os.remove(path)
                logger.info(f"[ROTATE] Deleted (size cap): {snap.filename}")
            db.delete(snap)
        db.commit()
    except Exception as e:
        logger.error(f"[ROTATE] Failed: {e}")
        db.rollback()
    finally:
        db.close()


async def poll_all_sites():
    logger.info("Starting Mist polling...")
    client = MistClient()
    org_id = os.getenv("MIST_ORG_ID", "")
    now = datetime.now(timezone.utc)

    sites = await client.get_sites(org_id)
    if not sites:
        logger.warning("No sites returned from Mist API")
        return

    if _monitored_site_ids:
        sites = [s for s in sites if s.get("id") in _monitored_site_ids]
        if not sites:
            logger.warning("No sites match monitored_site_ids filter")
            return

    # Org レベルのデータはサイクル毎に 1 回だけ取得
    device_profiles, rf_templates = await asyncio.gather(
        client.get_org_device_profiles(org_id),
        client.get_org_rf_templates(org_id),
    )

    # dp_cache: {deviceprofile_id: {"name": str, "radio_config": dict}}
    dp_cache: dict = {
        dp.get("id", ""): {
            "name": dp.get("name", ""),
            "radio_config": dp.get("radio_config") or {},
        }
        for dp in device_profiles
    }
    # rftemplate_cache: {rftemplate_id: rf_name}
    rftemplate_cache: dict = {rf.get("id", ""): rf.get("name", "") for rf in rf_templates}
    # site_rftemplate_map: {site_id: rftemplate_id}  ← sites API のレスポンスから作成
    site_rftemplate_map: dict = {s.get("id", ""): s.get("rftemplate_id") for s in sites}

    semaphore = asyncio.Semaphore(5)

    async def poll_site(site: dict):
        async with semaphore:
            site_id = site.get("id", "")
            site_name = site.get("name", "")
            rftemplate_id = site_rftemplate_map.get(site_id)
            rftemplate_name = rftemplate_cache.get(rftemplate_id) if rftemplate_id else None

            logger.info(f"Polling site: {site_name} ({site_id})")
            devices, devices_config = await asyncio.gather(
                client.get_site_devices_stats(site_id),
                client.get_site_devices_all(site_id),
            )
            config_by_id = {d.get("id", ""): d for d in devices_config}
            logger.info(f"  site {site_name}: {len(devices)} stats, {len(devices_config)} configs")

            db: Session = SessionLocal()
            try:
                for device in devices:
                    ap_id = device.get("id", "")
                    ap_name = device.get("name", "")
                    model = device.get("model", "")
                    mac = device.get("mac", "")
                    status = device.get("status", "connected")
                    num_clients = device.get("num_clients", 0) or 0

                    r24 = _extract_radio_stats(device, "band_24")
                    r5 = _extract_radio_stats(device, "band_5")
                    r6 = _extract_radio_stats(device, "band_6")

                    db.add(ApMetrics(
                        site_id=site_id, ap_id=ap_id, ap_name=ap_name, model=model, mac=mac,
                        timestamp=now, num_clients=num_clients, status=status,
                        radio_24_channel=r24["channel"],
                        radio_24_bandwidth=r24["bandwidth"],
                        radio_24_utilization=r24["util"],
                        radio_24_util_tx=r24["util_tx"],
                        radio_24_util_rx_in_bss=r24["util_rx_in_bss"],
                        radio_24_util_non_wifi=r24["util_non_wifi"],
                        radio_24_noise_floor=r24["noise_floor"],
                        radio_24_tx_power=r24["tx_power"],
                        radio_5_channel=r5["channel"],
                        radio_5_bandwidth=r5["bandwidth"],
                        radio_5_utilization=r5["util"],
                        radio_5_util_tx=r5["util_tx"],
                        radio_5_util_rx_in_bss=r5["util_rx_in_bss"],
                        radio_5_util_non_wifi=r5["util_non_wifi"],
                        radio_5_noise_floor=r5["noise_floor"],
                        radio_5_tx_power=r5["tx_power"],
                        radio_6_channel=r6["channel"],
                        radio_6_bandwidth=r6["bandwidth"],
                        radio_6_utilization=r6["util"],
                        radio_6_util_tx=r6["util_tx"],
                        radio_6_util_rx_in_bss=r6["util_rx_in_bss"],
                        radio_6_util_non_wifi=r6["util_non_wifi"],
                        radio_6_noise_floor=r6["noise_floor"],
                        radio_6_tx_power=r6["tx_power"],
                    ))

                    # AP の radio_config はソース判定にのみ使用（一括取得済み）
                    ap_config = config_by_id.get(ap_id, {})
                    dp_id = ap_config.get("deviceprofile_id")
                    dp_name = dp_cache.get(dp_id, {}).get("name") if dp_id else None

                    source_24 = detect_band_source(ap_config, "24", dp_cache, rftemplate_cache, rftemplate_id)
                    source_5 = detect_band_source(ap_config, "5", dp_cache, rftemplate_cache, rftemplate_id)
                    source_6 = detect_band_source(ap_config, "6", dp_cache, rftemplate_cache, rftemplate_id)
                    config_source = overall_source(source_24, source_5, source_6)

                    # 表示用の稼働値は radio_stat から取得（実際の動作チャンネル等）
                    rs = device.get("radio_stat", {}) or {}
                    b24 = rs.get("band_24", {}) or {}
                    b5 = rs.get("band_5", {}) or {}
                    b6 = rs.get("band_6", {}) or {}

                    # disabled フラグのみ radio_config から取得
                    radio_cfg = ap_config.get("radio_config", {}) or {}
                    b24_cfg = radio_cfg.get("band_24", {}) or {}
                    b5_cfg = radio_cfg.get("band_5", {}) or {}
                    b6_cfg = radio_cfg.get("band_6", {}) or {}

                    existing = db.query(RadioConfigCurrent).filter_by(ap_id=ap_id).first()

                    if existing:
                        _detect_and_record_changes(
                            db, existing, ap_id, site_id, ap_name, now,
                            b24, b5, b6, source_24, source_5, source_6,
                        )

                    new_vals = dict(
                        ap_name=ap_name,
                        site_id=site_id,
                        band_24_channel=b24.get("channel"),
                        band_24_bandwidth=b24.get("bandwidth"),
                        band_24_tx_power=b24.get("power"),
                        band_24_disabled=int(b24_cfg.get("disabled", False)),
                        band_5_channel=b5.get("channel"),
                        band_5_bandwidth=b5.get("bandwidth"),
                        band_5_tx_power=b5.get("power"),
                        band_5_disabled=int(b5_cfg.get("disabled", False)),
                        band_6_channel=b6.get("channel"),
                        band_6_bandwidth=b6.get("bandwidth"),
                        band_6_tx_power=b6.get("power"),
                        band_6_disabled=int(b6_cfg.get("disabled", False)),
                        config_source=config_source,
                        config_source_24=source_24,
                        config_source_5=source_5,
                        config_source_6=source_6,
                        deviceprofile_id=dp_id,
                        deviceprofile_name=dp_name,
                        rftemplate_id=rftemplate_id,
                        rftemplate_name=rftemplate_name,
                        updated_at=now,
                    )
                    if existing:
                        for k, v in new_vals.items():
                            setattr(existing, k, v)
                    else:
                        db.add(RadioConfigCurrent(ap_id=ap_id, **new_vals))

                db.commit()
            except Exception as e:
                logger.error(f"DB error for site {site_id}: {e}")
                db.rollback()
            finally:
                db.close()

    await asyncio.gather(*[poll_site(s) for s in sites])
    logger.info("Polling complete.")


async def save_floormap_log(
    now: datetime,
    tz_obj,
    tz_abbr: str,
    filename_suffix: str = "",
) -> str | None:
    """全サイト・全フロアのチャンネル使用状況を要約CSVに書き出す（Mist API からリアルタイム取得）。"""
    client = MistClient()
    org_id = os.getenv("MIST_ORG_ID", "")
    sites = await client.get_sites(org_id)
    if not sites:
        logger.info("[FLOORMAP SAVE] No sites.")
        return None
    if _monitored_site_ids:
        sites = [s for s in sites if s.get("id") in _monitored_site_ids]

    now_local = now.astimezone(tz_obj)
    ts_str = now_local.strftime("%Y-%m-%d %H:%M:%S")
    sem = asyncio.Semaphore(5)

    async def _fetch_site(site: dict) -> list[dict]:
        async with sem:
            site_id = site.get("id", "")
            site_name = site.get("name", "")
            maps_raw, devices = await asyncio.gather(
                client._get(f"/sites/{site_id}/maps"),
                client.get_site_devices_stats(site_id),
            )
            if not isinstance(maps_raw, list):
                maps_raw = []
            map_meta = {m.get("id", ""): m.get("name", "") for m in maps_raw}

            # (site_name, map_name, band, channel) -> [ap_name, ...]
            groups: dict[tuple, list[str]] = {}
            for d in devices:
                map_id = d.get("map_id") or ""
                map_name = map_meta.get(map_id, "")
                ap_name = d.get("name", "")
                rs = d.get("radio_stat", {}) or {}
                for band, ch in [
                    ("2.4G", (rs.get("band_24", {}) or {}).get("channel")),
                    ("5G",   (rs.get("band_5",  {}) or {}).get("channel")),
                    ("6G",   (rs.get("band_6",  {}) or {}).get("channel")),
                ]:
                    if ch is None:
                        continue
                    key = (site_name, map_name, band, ch)
                    groups.setdefault(key, []).append(ap_name)

            result = []
            for (sname, mname, band, channel), ap_names in groups.items():
                ap_count = len(ap_names)
                result.append({
                    "timestamp": ts_str,
                    "site_name": sname,
                    "map_name": mname,
                    "band": band,
                    "channel": channel,
                    "ap_count": ap_count,
                    "ap_list": ",".join(ap_names),
                    "has_interference": ap_count >= 2,
                })
            return result

    site_results = await asyncio.gather(*[_fetch_site(s) for s in sites])
    rows = [r for rs in site_results for r in rs]

    if not rows:
        logger.info("[FLOORMAP SAVE] No AP data, skipping.")
        return None

    os.makedirs(LOGS_DIR, exist_ok=True)
    if filename_suffix:
        filename = f"floormap_{now_local.strftime('%Y%m%d_%H%M%S')}_{tz_abbr}_{filename_suffix}_summary.csv"
    else:
        filename = f"floormap_{now_local.strftime('%Y%m%d_%H%M')}_{tz_abbr}_summary.csv"
    filepath = os.path.join(LOGS_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FLOORMAP_SUMMARY_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"[FLOORMAP SAVE] Saved: {filepath} ({len(rows)} records)")
    return filename


async def save_hourly_logs():
    """前回保存以降の ap_metrics 全件を CSV に書き出す（期間ログ方式）。"""
    global last_log_saved_at
    since = last_log_saved_at
    now = datetime.now(timezone.utc)
    tz_obj = ZoneInfo(_app_timezone)
    now_local = now.astimezone(tz_obj)
    tz_abbr = now_local.strftime("%Z")
    logger.info(f"[AUTO SAVE] Saving log since {since.isoformat()} ...")

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
        if not rows:
            logger.info("[AUTO SAVE] No new metrics since last save, skipping CSV write.")
        else:
            os.makedirs(LOGS_DIR, exist_ok=True)
            filename = f"ap_metrics_{now_local.strftime('%Y%m%d_%H%M')}_{tz_abbr}.csv"
            filepath = os.path.join(LOGS_DIR, filename)

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=ALL_CSV_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                for r in rows:
                    writer.writerow({
                        "timestamp": fmt_dt_tz(r.timestamp, _app_timezone),
                        "site_id": r.site_id,
                        "site_name": site_names.get(r.site_id, ""),
                        "ap_id": r.ap_id,
                        "ap_name": r.ap_name,
                        "model": r.model or "",
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

            site_count = len({r.site_id for r in rows})
            record_count = len(rows)

            existing = db.query(Snapshot).filter_by(filename=filename).first()
            if not existing:
                db.add(Snapshot(
                    filename=filename, saved_at=now, triggered_by="auto",
                    site_count=site_count, ap_count=record_count,
                ))
                db.commit()

            logger.info(f"[AUTO SAVE] Saved: {filepath} ({record_count} records, {site_count} sites)")

        last_log_saved_at = now
        _persist_last_log_saved_at(now)
    except Exception as e:
        logger.error(f"[AUTO SAVE] Failed: {e}")
        db.rollback()
    finally:
        db.close()

    try:
        await save_floormap_log(now, tz_obj, tz_abbr)
    except Exception as e:
        logger.error(f"[FLOORMAP SAVE] Auto save failed: {e}")

    rotate_logs(_log_retention_days)
