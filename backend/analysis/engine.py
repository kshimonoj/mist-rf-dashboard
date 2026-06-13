"""Wi-Fi 問題検知エンジン（Insights）。

ローカル DB（client_metrics / ap_metrics）の直近データのみを分析し、
insights テーブルへ UPSERT 方式で蓄積する（category + target_id で同一性判定、
検知されなくなった issue は resolved 化）。Mist API は呼ばない。
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from itertools import combinations

from sqlalchemy.orm import Session

from models import ApMetrics, AppSettings, ClientMetrics, Insight

logger = logging.getLogger(__name__)

ANALYSIS_WINDOW_HOURS = 1

CATEGORIES = ["sticky_client", "band24_stuck", "high_retry", "co_channel", "flapping"]


def _client_display_name(row: ClientMetrics) -> str:
    return row.hostname or row.mac or ""


def _group_clients_by_mac(rows: list[ClientMetrics]) -> dict[str, list[ClientMetrics]]:
    """mac 毎に時系列順（古い→新しい）でグルーピングする。"""
    by_mac: dict[str, list[ClientMetrics]] = {}
    for r in rows:
        if not r.mac:
            continue
        by_mac.setdefault(r.mac, []).append(r)
    return by_mac


# ── (1) Sticky Client 検知 ───────────────────────────────────────────────────

def _detect_sticky_clients(by_mac: dict[str, list[ClientMetrics]], now: datetime) -> list[Insight]:
    insights = []
    for mac, rows in by_mac.items():
        if len(rows) < 3:
            continue
        rssi_vals = [r.rssi for r in rows if r.rssi is not None]
        if not rssi_vals:
            continue
        ap_ids = {r.ap_id for r in rows if r.ap_id}
        if len(ap_ids) != 1:
            continue
        avg_rssi = sum(rssi_vals) / len(rssi_vals)
        if avg_rssi >= -75:
            continue
        latest = rows[-1]
        ap_name = latest.ap_name or latest.ap_id or ""
        severity = "critical" if avg_rssi < -80 else "warning"
        insights.append(Insight(
            category="sticky_client",
            severity=severity,
            site_id=latest.site_id,
            site_name=latest.site_name,
            target_type="client",
            target_id=mac,
            target_name=_client_display_name(latest),
            detail=f"RSSI {avg_rssi:.0f}dBm (1h平均) で {ap_name} に接続継続",
            recommendation="該当APのTx Power引き下げ、またはRSSIベースの切断設定を検討",
            metrics_json=json.dumps({
                "avg_rssi": round(avg_rssi, 1),
                "record_count": len(rows),
                "ap_id": latest.ap_id,
                "ap_name": ap_name,
            }),
        ))
    return insights


# ── (2) 2.4GHz 滞留検知 ─────────────────────────────────────────────────────

def _detect_band24_stuck(by_mac: dict[str, list[ClientMetrics]], now: datetime) -> list[Insight]:
    insights = []
    for mac, rows in by_mac.items():
        if len(rows) < 3:
            continue
        latest = rows[-1]
        if not latest.dual_band:
            continue
        bands = {r.band for r in rows}
        if bands != {"24"}:
            continue
        insights.append(Insight(
            category="band24_stuck",
            severity="warning",
            site_id=latest.site_id,
            site_name=latest.site_name,
            target_type="client",
            target_id=mac,
            target_name=_client_display_name(latest),
            detail=f"デュアルバンド対応端末が2.4GHzのみに接続 ({len(rows)}レコード/1h)",
            recommendation="Band Steering設定の見直し、5GHzカバレッジ確認",
            metrics_json=json.dumps({
                "record_count": len(rows),
                "ap_id": latest.ap_id,
                "ap_name": latest.ap_name,
                "channel": latest.channel,
            }),
        ))
    return insights


# ── (3) High Retry 検知 ─────────────────────────────────────────────────────

def _detect_high_retry(by_mac: dict[str, list[ClientMetrics]], now: datetime) -> list[Insight]:
    insights = []
    for mac, rows in by_mac.items():
        latest = rows[-1]
        tx_pkts = latest.tx_pkts or 0
        tx_retries = latest.tx_retries or 0
        if tx_pkts < 1000:
            continue
        ratio = tx_retries / tx_pkts
        if ratio <= 0.20:
            continue
        severity = "critical" if ratio > 0.30 else "warning"
        insights.append(Insight(
            category="high_retry",
            severity=severity,
            site_id=latest.site_id,
            site_name=latest.site_name,
            target_type="client",
            target_id=mac,
            target_name=_client_display_name(latest),
            detail=f"TX retry率 {ratio * 100:.0f}% (retries {tx_retries:,} / pkts {tx_pkts:,})",
            recommendation="電波品質・干渉の確認。同一chの他APや非Wi-Fi干渉源を確認",
            metrics_json=json.dumps({
                "tx_retry_ratio": round(ratio, 3),
                "tx_pkts": tx_pkts,
                "tx_retries": tx_retries,
                "ap_id": latest.ap_id,
                "ap_name": latest.ap_name,
                "band": latest.band,
                "channel": latest.channel,
            }),
        ))
    return insights


# ── (4) Co-channel 干渉検知 ──────────────────────────────────────────────────

_BANDS = [("24", "2.4GHz"), ("5", "5GHz"), ("6", "6GHz")]


def _other_bss_pct(row: ApMetrics, band: str) -> float | None:
    """other BSS率 = util_all - util_tx - util_rx_in_bss - util_non_wifi（0未満は0に丸め）。"""
    util_all = getattr(row, f"radio_{band}_utilization")
    util_tx = getattr(row, f"radio_{band}_util_tx")
    util_rx = getattr(row, f"radio_{band}_util_rx_in_bss")
    util_non_wifi = getattr(row, f"radio_{band}_util_non_wifi")
    if util_all is None:
        return None
    other = util_all - (util_tx or 0) - (util_rx or 0) - (util_non_wifi or 0)
    return max(other, 0.0)


def _detect_co_channel(
    latest_aps: list[ApMetrics], site_names: dict[str, str], now: datetime
) -> list[Insight]:
    insights = []
    # (site_id, band, channel) -> [(ap_row, other_bss_pct), ...]
    groups: dict[tuple, list[tuple[ApMetrics, float]]] = {}
    for row in latest_aps:
        if row.status != "connected":
            continue
        for band, _label in _BANDS:
            channel = getattr(row, f"radio_{band}_channel")
            if channel is None:
                continue
            other = _other_bss_pct(row, band)
            if other is None or other <= 10:
                continue
            groups.setdefault((row.site_id, band, channel), []).append((row, other))

    for (site_id, band, channel), members in groups.items():
        if len(members) < 2:
            continue
        label = dict(_BANDS)[band]
        for (ap1, o1), (ap2, o2) in combinations(members, 2):
            severity = "critical" if max(o1, o2) > 15 else "warning"
            insights.append(Insight(
                category="co_channel",
                severity=severity,
                site_id=site_id,
                site_name=site_names.get(site_id, site_id),
                target_type="ap_pair",
                target_id=f"{ap1.ap_id}|{ap2.ap_id}",
                target_name=f"{ap1.ap_name} ↔ {ap2.ap_name}",
                detail=f"{label} ch{channel} 重複 / other BSS {o1:.0f}% / {o2:.0f}%",
                recommendation="チャネル分散(2.4GHzはch1/6/11)、またはTx Power調整を検討",
                metrics_json=json.dumps({
                    "band": label,
                    "channel": channel,
                    "other_bss_pct": [round(o1, 1), round(o2, 1)],
                    "ap_ids": [ap1.ap_id, ap2.ap_id],
                    "ap_names": [ap1.ap_name, ap2.ap_name],
                }),
            ))
    return insights


# ── (5) Roaming Flapping 検知 ────────────────────────────────────────────────

def _detect_flapping(by_mac: dict[str, list[ClientMetrics]], now: datetime) -> list[Insight]:
    insights = []
    for mac, rows in by_mac.items():
        seq = [(r.ap_id, r.ap_name) for r in rows if r.ap_id]
        if len(seq) < 2:
            continue
        changes = sum(1 for i in range(1, len(seq)) if seq[i][0] != seq[i - 1][0])
        if changes <= 6:
            continue
        latest = rows[-1]
        ap_names: list[str] = []
        for ap_id, ap_name in seq:
            name = ap_name or ap_id
            if name not in ap_names:
                ap_names.append(name)
        names_label = "↔".join(ap_names[:2]) + ("..." if len(ap_names) > 2 else "")
        severity = "critical" if changes > 10 else "warning"
        insights.append(Insight(
            category="flapping",
            severity=severity,
            site_id=latest.site_id,
            site_name=latest.site_name,
            target_type="client",
            target_id=mac,
            target_name=_client_display_name(latest),
            detail=f"1hに{changes}回AP切替 ({names_label})",
            recommendation="AP間の電波重複確認、Roaming閾値(RSSI)調整を検討",
            metrics_json=json.dumps({
                "change_count": changes,
                "ap_names": ap_names,
            }),
        ))
    return insights


# ── 分析実行 ─────────────────────────────────────────────────────────────────

def run_analysis(db: Session) -> int:
    """5種の検知を実行し insights テーブルを全置換する。検出件数を返す。"""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=ANALYSIS_WINDOW_HOURS)

    client_rows = (
        db.query(ClientMetrics)
        .filter(ClientMetrics.timestamp >= since)
        .order_by(ClientMetrics.timestamp.asc())
        .all()
    )
    by_mac = _group_clients_by_mac(client_rows)

    # 各APの最新レコード（直近1時間内）
    ap_rows = (
        db.query(ApMetrics)
        .filter(ApMetrics.timestamp >= since)
        .order_by(ApMetrics.timestamp.asc())
        .all()
    )
    latest_by_ap: dict[str, ApMetrics] = {}
    for r in ap_rows:
        latest_by_ap[r.ap_id] = r  # 昇順なので最後の代入が最新

    # ap_metrics に site_name が無いため client_metrics から補完する
    site_names: dict[str, str] = {
        r.site_id: r.site_name for r in client_rows if r.site_id and r.site_name
    }

    insights: list[Insight] = []
    insights += _detect_sticky_clients(by_mac, now)
    insights += _detect_band24_stuck(by_mac, now)
    insights += _detect_high_retry(by_mac, now)
    insights += _detect_co_channel(list(latest_by_ap.values()), site_names, now)
    insights += _detect_flapping(by_mac, now)

    # UPSERT: category + target_id が一致する active レコードがあれば更新、なければ新規
    detected: dict[tuple, Insight] = {(i.category, i.target_id): i for i in insights}
    existing_active = db.query(Insight).filter(Insight.status == "active").all()
    existing_map: dict[tuple, Insight] = {(r.category, r.target_id): r for r in existing_active}

    new_count = 0
    for key, new in detected.items():
        row = existing_map.get(key)
        if row:
            row.last_detected_at = now
            row.severity = new.severity
            row.site_id = new.site_id
            row.site_name = new.site_name
            row.target_name = new.target_name
            row.detail = new.detail
            row.recommendation = new.recommendation
            row.metrics_json = new.metrics_json
        else:
            new.first_detected_at = now
            new.last_detected_at = now
            new.status = "active"
            db.add(new)
            new_count += 1

    # 今回検知されなかった active レコードは resolved 化
    resolved_count = 0
    for key, row in existing_map.items():
        if key not in detected:
            row.status = "resolved"
            row.resolved_at = now
            resolved_count += 1

    # ローテーション: resolved_at が30日より古い resolved レコードを削除
    cutoff = now - timedelta(days=30)
    db.query(Insight).filter(
        Insight.status == "resolved", Insight.resolved_at < cutoff
    ).delete()

    row = db.query(AppSettings).first()
    if row:
        row.last_insights_analyzed_at = now
    db.commit()

    logger.info(
        f"[INSIGHTS] Analysis complete: {len(detected)} active "
        f"({new_count} new, {resolved_count} resolved)"
    )
    return len(detected)
