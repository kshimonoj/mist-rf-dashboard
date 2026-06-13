"""最適化レコメンデーション生成（Insights Phase 2）。

insights テーブルの検知結果を AP 単位に集約し、ルールベースで推奨アクションを生成する。
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import ApMetrics, Insight, RadioConfigCurrent

logger = logging.getLogger(__name__)

# チャネル候補（2.4GHz は ch1/6/11 のみから選択）
_CH_CANDIDATES = {
    "24": [1, 6, 11],
    "5": [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120,
          124, 128, 132, 136, 140, 149, 153, 157, 161, 165],
}

_BAND_LABEL_TO_KEY = {"2.4GHz": "24", "5GHz": "5", "6GHz": "6"}
_BAND_KEY_TO_LABEL = {"24": "2.4GHz", "5": "5GHz", "6": "6GHz"}


def _parse_metrics(insight: Insight) -> dict:
    try:
        return json.loads(insight.metrics_json or "{}")
    except Exception:
        return {}


def _latest_ap_metrics(db: Session) -> dict[str, ApMetrics]:
    """直近24時間の ap_metrics から AP 毎の最新レコードを返す。"""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (
        db.query(ApMetrics)
        .filter(ApMetrics.timestamp >= since)
        .order_by(ApMetrics.timestamp.asc())
        .all()
    )
    latest: dict[str, ApMetrics] = {}
    for r in rows:
        latest[r.ap_id] = r  # 昇順なので最後の代入が最新
    return latest


def _used_channels(latest: dict[str, ApMetrics], site_id: str, band: str) -> set[int]:
    used = set()
    for r in latest.values():
        if r.site_id != site_id or r.status != "connected":
            continue
        ch = getattr(r, f"radio_{band}_channel")
        if ch is not None:
            used.add(ch)
    return used


class _RecBuilder:
    """(ap_id または site) 単位のレコメンデーションを集約するヘルパー。"""

    def __init__(self):
        self.entries: dict[str, dict] = {}

    def add(self, ap_id: str | None, ap_name: str | None,
            site_id: str | None, site_name: str | None, action: str) -> None:
        key = ap_id or f"site:{site_id}"
        entry = self.entries.setdefault(key, {
            "ap_id": ap_id,
            "ap_name": ap_name,
            "site_id": site_id,
            "site_name": site_name,
            "actions": [],
        })
        if action not in entry["actions"]:
            entry["actions"].append(action)

    def to_list(self) -> list[dict]:
        return list(self.entries.values())


def build_recommendations(db: Session) -> list[dict]:
    insights = db.query(Insight).filter(Insight.status == "active").all()
    if not insights:
        return []

    latest = _latest_ap_metrics(db)
    builder = _RecBuilder()

    # 1) co_channel 検知 → 同サイトの未使用チャネルを提案
    for ins in [i for i in insights if i.category == "co_channel"]:
        m = _parse_metrics(ins)
        band_label = m.get("band", "")
        band = _BAND_LABEL_TO_KEY.get(band_label)
        channel = m.get("channel")
        ap_ids = m.get("ap_ids", [])
        ap_names = m.get("ap_names", [])
        candidates = _CH_CANDIDATES.get(band or "", [])
        if candidates and ins.site_id:
            used = _used_channels(latest, ins.site_id, band)
            unused = [c for c in candidates if c not in used]
        else:
            unused = []
        if unused:
            action = (f"Co-channel干渉 ({band_label} ch{channel}): "
                      f"未使用ch {', '.join(str(c) for c in unused[:5])} への変更を検討")
        else:
            action = (f"Co-channel干渉 ({band_label} ch{channel}): "
                      f"空きチャネルなし。Tx Power調整を検討")
        for ap_id, ap_name in zip(ap_ids, ap_names):
            builder.add(ap_id, ap_name, ins.site_id, ins.site_name, action)

    # 2) 同一APで sticky_client 2件以上 → Tx Power 引き下げを提案
    sticky_by_ap: dict[str, list[Insight]] = {}
    for ins in [i for i in insights if i.category == "sticky_client"]:
        m = _parse_metrics(ins)
        ap_id = m.get("ap_id")
        if ap_id:
            sticky_by_ap.setdefault(ap_id, []).append(ins)
    for ap_id, items in sticky_by_ap.items():
        if len(items) < 2:
            continue
        m = _parse_metrics(items[0])
        ap_name = m.get("ap_name")
        cfg = db.query(RadioConfigCurrent).filter_by(ap_id=ap_id).first()
        if cfg and (cfg.band_24_tx_power is not None or cfg.band_5_tx_power is not None):
            power = (f"2.4G {cfg.band_24_tx_power if cfg.band_24_tx_power is not None else '-'}dBm"
                     f" / 5G {cfg.band_5_tx_power if cfg.band_5_tx_power is not None else '-'}dBm")
        else:
            power = "不明"
        builder.add(ap_id, ap_name, items[0].site_id, items[0].site_name,
                    f"Sticky Client {len(items)}件: Tx Power引き下げを検討 (現在: {power})")

    # 3) 同一サイトで band24_stuck 3件以上 → Band Steering 設定確認（サイト単位）
    stuck_by_site: dict[str, list[Insight]] = {}
    for ins in [i for i in insights if i.category == "band24_stuck"]:
        if ins.site_id:
            stuck_by_site.setdefault(ins.site_id, []).append(ins)
    for site_id, items in stuck_by_site.items():
        if len(items) < 3:
            continue
        site_name = items[0].site_name or site_id
        builder.add(None, f"{site_name} (サイト全体)", site_id, items[0].site_name,
                    f"2.4GHz滞留 {len(items)}件: WLANのBand Steering設定確認を推奨")

    # 4) high_retry 検知APで util_non_wifi > 15% → 非Wi-Fi干渉によるチャネル変更を提案
    for ins in [i for i in insights if i.category == "high_retry"]:
        m = _parse_metrics(ins)
        ap_id = m.get("ap_id")
        band = m.get("band")
        if not ap_id or band not in ("24", "5", "6"):
            continue
        row = latest.get(ap_id)
        if not row:
            continue
        non_wifi = getattr(row, f"radio_{band}_util_non_wifi")
        if non_wifi is None or non_wifi <= 15:
            continue
        band_label = _BAND_KEY_TO_LABEL[band]
        builder.add(ap_id, m.get("ap_name"), ins.site_id, ins.site_name,
                    f"非Wi-Fi干渉あり ({band_label} util_non_wifi {non_wifi:.0f}%): チャネル変更を検討")

    return builder.to_list()
