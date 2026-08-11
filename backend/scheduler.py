import asyncio
import csv
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from database import SessionLocal, engine
from mist.client import MistClient, SLE_METRICS, parse_sle_metric
from models import AppSettings, ApEvent, ApMetrics, ClientMetrics, RadioConfigChange, RadioConfigCurrent, Snapshot
from radio_helpers import detect_band_source, overall_source
from utils import fmt_dt, fmt_dt_tz

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
_log_interval_minutes: int = 60
_log_retention_days: int = 30
_app_timezone: str = "Asia/Tokyo"
_monitored_site_ids: list[str] = []
_client_polling_interval_seconds: int = 600
_metrics_retention_days: int = 7
_long_history_enabled: bool = False
last_log_saved_at: datetime = datetime.now(timezone.utc)
last_client_log_saved_at: datetime = datetime.now(timezone.utc)


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

SLE_CSV_COLUMNS = [
    "timestamp", "site_id", "site_name", "ap_id", "ap_name",
    "capacity_score",
    "capacity_wifi_interference", "capacity_non_wifi_interference",
    "capacity_client_count", "capacity_client_usage",
    "capacity_impact_users", "capacity_total_users",
    "throughput_score", "throughput_impact_users", "throughput_total_users",
    "coverage_score", "coverage_impact_users", "coverage_total_users",
    "time_to_connect_score", "time_to_connect_avg_sec",
    "ttc_impact_users", "ttc_total_users",
    "roaming_score", "roaming_impact_users", "roaming_total_users",
    "ap_availability_score", "ap_availability_impact_users", "ap_availability_total_users",
]

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


CLIENT_FIELDS = [
    "mac", "hostname", "ip", "manufacture", "family", "model", "os",
    "band", "channel", "proto", "ssid", "bssid", "rssi", "snr",
    "idle_time", "uptime", "tx_rate", "rx_rate", "tx_bytes", "rx_bytes",
    "tx_pkts", "rx_pkts", "tx_retries", "rx_retries", "tx_bps", "rx_bps",
    "vlan_id", "key_mgmt", "dual_band", "is_guest",
]

CLIENT_CSV_COLUMNS = [
    "timestamp", "site_id", "site_name", "ap_id", "ap_name", "ap_mac",
    *CLIENT_FIELDS,
]

AP_EVENTS_CSV_COLUMNS = [
    "event_timestamp", "site_name", "ap_name", "ap_mac", "event_type", "reason",
    "band", "channel", "pre_channel", "bandwidth", "pre_bandwidth",
]


def _extract_client_fields(client: dict) -> dict:
    """Mist client レコードから ClientMetrics 用のフィールドを抽出する。"""
    out: dict = {}
    for key in CLIENT_FIELDS:
        out[key] = client.get(key)
    # vlan_id は数値で返ることがあるため文字列化
    if out.get("vlan_id") is not None:
        out["vlan_id"] = str(out["vlan_id"])
    return out


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


def prune_old_metrics() -> None:
    """保持日数を超えた ap_metrics / client_metrics を削除し、VACUUM で容量を回収する。
    long_history_enabled=True なら _metrics_retention_days（通常30）、False なら 7 日を使用。"""
    retention_days = _metrics_retention_days if _long_history_enabled else 7
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    started = time.monotonic()
    db: Session = SessionLocal()
    try:
        deleted_ap = (
            db.query(ApMetrics)
            .filter(ApMetrics.timestamp < cutoff)
            .delete(synchronize_session=False)
        )
        deleted_client = (
            db.query(ClientMetrics)
            .filter(ClientMetrics.timestamp < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
    except Exception as e:
        logger.error(f"[PRUNE] Failed: {e}")
        db.rollback()
        return
    finally:
        db.close()

    try:
        # VACUUM はトランザクション外でのみ実行可能
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("VACUUM"))
    except Exception as e:
        logger.error(f"[PRUNE] VACUUM failed: {e}")

    elapsed = time.monotonic() - started
    logger.info(
        f"[PRUNE] retention={retention_days}d "
        f"deleted ap_metrics={deleted_ap} client_metrics={deleted_client} "
        f"({elapsed:.1f}s)"
    )


async def poll_all_sites():
    logger.info("Starting Mist polling...")
    client = MistClient()
    org_id = client.org_id
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


async def poll_clients():
    """全監視対象サイトの無線クライアント一覧を取得し client_metrics へ保存する。
    AP metrics のポーリングとは完全に分離した別 job。"""
    logger.info("Starting client polling...")
    client = MistClient()
    org_id = client.org_id
    now = datetime.now(timezone.utc)

    sites = await client.get_sites(org_id)
    if not sites:
        logger.warning("[CLIENT POLL] No sites returned from Mist API")
        return

    if _monitored_site_ids:
        sites = [s for s in sites if s.get("id") in _monitored_site_ids]
        if not sites:
            logger.warning("[CLIENT POLL] No sites match monitored_site_ids filter")
            return

    semaphore = asyncio.Semaphore(5)

    async def poll_site(site: dict):
        async with semaphore:
            site_id = site.get("id", "")
            site_name = site.get("name", "")
            # ap_mac -> ap_name / ap_id 解決用のキャッシュを devices stats から構築
            devices, clients = await asyncio.gather(
                client.get_site_devices_stats(site_id),
                client.get_site_clients(site_id),
            )
            ap_by_mac: dict = {
                (d.get("mac") or "").lower(): {"id": d.get("id", ""), "name": d.get("name", "")}
                for d in devices
            }
            logger.info(f"[CLIENT POLL] site {site_name}: {len(clients)} clients")

            db: Session = SessionLocal()
            try:
                for c in clients:
                    ap_mac = (c.get("ap_mac") or "").lower()
                    ap_info = ap_by_mac.get(ap_mac, {})
                    fields = _extract_client_fields(c)
                    db.add(ClientMetrics(
                        timestamp=now,
                        site_id=site_id,
                        site_name=site_name,
                        ap_id=c.get("ap_id") or ap_info.get("id"),
                        ap_name=ap_info.get("name"),
                        ap_mac=c.get("ap_mac"),
                        **fields,
                    ))
                db.commit()
            except Exception as e:
                logger.error(f"[CLIENT POLL] DB error for site {site_id}: {e}")
                db.rollback()
            finally:
                db.close()

    await asyncio.gather(*[poll_site(s) for s in sites])
    logger.info("Client polling complete.")


async def save_floormap_log(
    now: datetime,
    tz_obj,
    tz_abbr: str,
    filename_suffix: str = "",
) -> str | None:
    """全サイト・全フロアのチャンネル使用状況を要約CSVに書き出す（Mist API からリアルタイム取得）。"""
    client = MistClient()
    org_id = client.org_id
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


def _build_sle_csv_row(ts_str: str, site_id: str, site_name: str,
                       ap_id: str, ap_name: str, metric_data: dict) -> dict:
    cap = metric_data.get("capacity", {})
    thr = metric_data.get("throughput", {})
    cov = metric_data.get("coverage", {})
    ttc = metric_data.get("time-to-connect", {})
    roam = metric_data.get("roaming", {})
    apav = metric_data.get("ap-availability", {})
    clf = cap.get("classifiers", {}) or {}
    return {
        "timestamp": ts_str,
        "site_id": site_id,
        "site_name": site_name,
        "ap_id": ap_id,
        "ap_name": ap_name,
        "capacity_score": cap.get("score"),
        "capacity_wifi_interference": clf.get("wifi_interference"),
        "capacity_non_wifi_interference": clf.get("non_wifi_interference"),
        "capacity_client_count": clf.get("client_count"),
        "capacity_client_usage": clf.get("client_usage"),
        "capacity_impact_users": cap.get("impact_users"),
        "capacity_total_users": cap.get("total_users"),
        "throughput_score": thr.get("score"),
        "throughput_impact_users": thr.get("impact_users"),
        "throughput_total_users": thr.get("total_users"),
        "coverage_score": cov.get("score"),
        "coverage_impact_users": cov.get("impact_users"),
        "coverage_total_users": cov.get("total_users"),
        "time_to_connect_score": ttc.get("score"),
        "time_to_connect_avg_sec": ttc.get("avg_sec"),
        "ttc_impact_users": ttc.get("impact_users"),
        "ttc_total_users": ttc.get("total_users"),
        "roaming_score": roam.get("score"),
        "roaming_impact_users": roam.get("impact_users"),
        "roaming_total_users": roam.get("total_users"),
        "ap_availability_score": apav.get("score"),
        "ap_availability_impact_users": apav.get("impact_users"),
        "ap_availability_total_users": apav.get("total_users"),
    }


async def save_sle_log(now: datetime, tz_obj, tz_abbr: str) -> None:
    """全APの6 SLEメトリクスを取得してCSVに保存する。"""
    client = MistClient()
    org_id = client.org_id
    sites = await client.get_sites(org_id)
    if not sites:
        logger.info("[SLE SAVE] No sites.")
        return
    if _monitored_site_ids:
        sites = [s for s in sites if s.get("id") in _monitored_site_ids]

    site_names = {s.get("id", ""): s.get("name", "") for s in sites}
    now_local = now.astimezone(tz_obj)
    ts_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

    # 全サイトの AP 一覧を収集
    ap_list: list[tuple[str, str, str]] = []
    for site in sites:
        site_id = site.get("id", "")
        try:
            devices = await client.get_site_devices_stats(site_id)
            for d in devices:
                ap_list.append((site_id, d.get("id", ""), d.get("name", "")))
        except Exception as e:
            logger.error(f"[SLE SAVE] Failed to get devices for site {site_id}: {e}")

    if not ap_list:
        logger.info("[SLE SAVE] No APs found.")
        return

    semaphore = asyncio.Semaphore(10)

    async def fetch_ap(site_id: str, ap_id: str, ap_name: str) -> dict | None:
        async with semaphore:
            try:
                results = await asyncio.gather(
                    *[client.get_ap_sle(site_id, ap_id, m) for m in SLE_METRICS],
                    return_exceptions=True,
                )
                metric_data = {
                    m: (parse_sle_metric(r, m) if not isinstance(r, Exception) else {})
                    for m, r in zip(SLE_METRICS, results)
                }
                return _build_sle_csv_row(
                    ts_str, site_id, site_names.get(site_id, ""),
                    ap_id, ap_name, metric_data,
                )
            except Exception as e:
                logger.error(f"[SLE SAVE] Failed for AP {ap_id}: {e}")
                return None

    rows_or_none = await asyncio.gather(*[fetch_ap(s, a, n) for s, a, n in ap_list])
    rows = [r for r in rows_or_none if r is not None]

    if not rows:
        logger.info("[SLE SAVE] No SLE data, skipping.")
        return

    os.makedirs(LOGS_DIR, exist_ok=True)
    filename = f"sle_metrics_{now_local.strftime('%Y%m%d_%H%M')}_{tz_abbr}.csv"
    filepath = os.path.join(LOGS_DIR, filename)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SLE_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"[SLE SAVE] Saved: {filepath} ({len(rows)} records)")


async def save_client_log(now: datetime, tz_obj, tz_abbr: str) -> None:
    """前回保存以降の client_metrics 全件を CSV に書き出す（期間ログ方式）。"""
    global last_client_log_saved_at
    since = last_client_log_saved_at
    now_local = now.astimezone(tz_obj)

    db: Session = SessionLocal()
    try:
        rows = (
            db.query(ClientMetrics)
            .filter(ClientMetrics.timestamp >= since)
            .order_by(ClientMetrics.site_id, ClientMetrics.ap_id, ClientMetrics.timestamp)
            .all()
        )
        if not rows:
            logger.info("[CLIENT SAVE] No new client metrics since last save, skipping CSV write.")
        else:
            os.makedirs(LOGS_DIR, exist_ok=True)
            filename = f"client_metrics_{now_local.strftime('%Y%m%d_%H%M')}_{tz_abbr}.csv"
            filepath = os.path.join(LOGS_DIR, filename)
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CLIENT_CSV_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                for r in rows:
                    row = {
                        "timestamp": fmt_dt_tz(r.timestamp, _app_timezone),
                        "site_id": r.site_id,
                        "site_name": r.site_name or "",
                        "ap_id": r.ap_id or "",
                        "ap_name": r.ap_name or "",
                        "ap_mac": r.ap_mac or "",
                    }
                    for field in CLIENT_FIELDS:
                        row[field] = getattr(r, field)
                    writer.writerow(row)
            logger.info(f"[CLIENT SAVE] Saved: {filepath} ({len(rows)} records)")

        last_client_log_saved_at = now
    except Exception as e:
        logger.error(f"[CLIENT SAVE] Failed: {e}")
        db.rollback()
    finally:
        db.close()


def _store_ap_events(db: Session, events: list[dict], devices: list[dict],
                      site_id: str, site_name: str, now: datetime) -> tuple[list[dict], int]:
    """イベントを ap_events へ INSERT OR IGNORE する。(新規CSV行リスト, 重複件数) を返す。
    重複は (site_id, ap_mac, event_type, event_timestamp) の一意制約で判定される。"""
    ap_by_mac = {
        (d.get("mac") or "").lower(): {"id": d.get("id", ""), "name": d.get("name", "")}
        for d in devices
    }
    new_rows: list[dict] = []
    duplicate_count = 0

    for ev in events:
        ts = ev.get("timestamp")
        mac = (ev.get("mac") or ev.get("ap") or "").replace(":", "").lower()
        if ts is None or not mac:
            continue
        event_ts = datetime.fromtimestamp(ts, tz=timezone.utc)
        ap_info = ap_by_mac.get(mac, {})
        event_type = ev.get("type", "")

        stmt = sqlite_insert(ApEvent).values(
            event_timestamp=event_ts,
            fetched_at=now,
            site_id=site_id,
            site_name=site_name,
            ap_mac=mac,
            ap_id=ap_info.get("id") or None,
            ap_name=ap_info.get("name") or None,
            event_type=event_type,
            reason=ev.get("reason"),
            band=ev.get("band"),
            channel=ev.get("channel"),
            pre_channel=ev.get("pre_channel"),
            bandwidth=ev.get("bandwidth"),
            pre_bandwidth=ev.get("pre_bandwidth"),
            raw_json=json.dumps(ev),
        ).on_conflict_do_nothing(
            index_elements=["site_id", "ap_mac", "event_type", "event_timestamp"]
        )
        result = db.execute(stmt)
        if result.rowcount:
            new_rows.append({
                "event_timestamp": fmt_dt_tz(event_ts, _app_timezone),
                "site_name": site_name,
                "ap_name": ap_info.get("name") or "",
                "ap_mac": mac,
                "event_type": event_type,
                "reason": ev.get("reason"),
                "band": ev.get("band"),
                "channel": ev.get("channel"),
                "pre_channel": ev.get("pre_channel"),
                "bandwidth": ev.get("bandwidth"),
                "pre_bandwidth": ev.get("pre_bandwidth"),
            })
        else:
            duplicate_count += 1

    return new_rows, duplicate_count


async def save_ap_events_log(now: datetime, tz_obj, tz_abbr: str) -> None:
    """全サイトのAPイベント（再起動・DFS等）を ap_events テーブルへ保存し、新規分をCSVに書き出す。
    重複は (site_id, ap_mac, event_type, event_timestamp) の一意制約で INSERT OR IGNORE される。"""
    client = MistClient()
    org_id = client.org_id
    sites = await client.get_sites(org_id)
    if not sites:
        logger.info("[AP EVENTS SAVE] No sites.")
        return
    if _monitored_site_ids:
        sites = [s for s in sites if s.get("id") in _monitored_site_ids]

    now_local = now.astimezone(tz_obj)
    new_rows: list[dict] = []

    db: Session = SessionLocal()
    try:
        for site in sites:
            site_id = site.get("id", "")
            site_name = site.get("name", "")
            try:
                events, devices = await asyncio.gather(
                    client.get_site_device_events(site_id, duration="1h"),
                    client.get_site_devices_stats(site_id),
                )
            except Exception as e:
                logger.error(f"[AP EVENTS SAVE] Failed to fetch for site {site_id}: {e}")
                continue

            rows, _ = _store_ap_events(db, events, devices, site_id, site_name, now)
            new_rows.extend(rows)
        db.commit()
    except Exception as e:
        logger.error(f"[AP EVENTS SAVE] Failed: {e}")
        db.rollback()
        return
    finally:
        db.close()

    if not new_rows:
        logger.info("[AP EVENTS SAVE] No new events, skipping CSV write.")
        return

    os.makedirs(LOGS_DIR, exist_ok=True)
    filename = f"ap_events_{now_local.strftime('%Y%m%d_%H%M')}_{tz_abbr}.csv"
    filepath = os.path.join(LOGS_DIR, filename)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=AP_EVENTS_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(new_rows)

    logger.info(f"[AP EVENTS SAVE] Saved: {filepath} ({len(new_rows)} records)")


async def backfill_ap_events(days: int = 7) -> dict:
    """過去N日分のAPイベントを一括取得し ap_events へ保存する（手動実行用のバックフィル）。
    サイトごとに順次処理し、あるサイトでエラーが出ても他サイトの処理は継続する。"""
    client = MistClient()
    org_id = client.org_id
    now = datetime.now(timezone.utc)
    tz_obj = ZoneInfo(_app_timezone)
    now_local = now.astimezone(tz_obj)
    tz_abbr = now_local.strftime("%Z")

    sites = await client.get_sites(org_id)
    if _monitored_site_ids:
        sites = [s for s in sites if s.get("id") in _monitored_site_ids]

    new_rows: list[dict] = []
    errors: list[dict] = []
    sites_processed = 0
    skipped_existing_count = 0

    db: Session = SessionLocal()
    try:
        for site in sites:
            site_id = site.get("id", "")
            site_name = site.get("name", "")
            try:
                events, devices = await asyncio.gather(
                    client.get_site_device_events(site_id, duration=f"{days}d", limit=100),
                    client.get_site_devices_stats(site_id),
                )
                rows, skipped = _store_ap_events(db, events, devices, site_id, site_name, now)
                db.commit()
                new_rows.extend(rows)
                skipped_existing_count += skipped
                sites_processed += 1
            except Exception as e:
                db.rollback()
                logger.error(f"[AP EVENTS BACKFILL] Failed for site {site_name} ({site_id}): {e}")
                errors.append({"site_name": site_name, "error": str(e)})
    finally:
        db.close()

    csv_file: str | None = None
    if new_rows:
        os.makedirs(LOGS_DIR, exist_ok=True)
        csv_file = f"ap_events_backfill_{now_local.strftime('%Y%m%d_%H%M')}_{tz_abbr}.csv"
        filepath = os.path.join(LOGS_DIR, csv_file)
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=AP_EVENTS_CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(new_rows)
        logger.info(f"[AP EVENTS BACKFILL] Saved: {filepath} ({len(new_rows)} new events)")

    return {
        "sites_processed": sites_processed,
        "new_events": len(new_rows),
        "skipped_existing": skipped_existing_count,
        "csv_file": csv_file,
        "errors": errors,
    }


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
    org_id = client.org_id
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

    try:
        await save_sle_log(now, tz_obj, tz_abbr)
    except Exception as e:
        logger.error(f"[SLE SAVE] Auto save failed: {e}")

    try:
        await save_client_log(now, tz_obj, tz_abbr)
    except Exception as e:
        logger.error(f"[CLIENT SAVE] Auto save failed: {e}")

    try:
        await save_ap_events_log(now, tz_obj, tz_abbr)
    except Exception as e:
        logger.error(f"[AP EVENTS SAVE] Auto save failed: {e}")

    rotate_logs(_log_retention_days)

    # ログ保存後に Insights 分析を実行（ローカルDBのみ参照・API コールなし）
    try:
        from analysis.engine import run_analysis
        db = SessionLocal()
        try:
            run_analysis(db)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[INSIGHTS] Auto analysis failed: {e}")
