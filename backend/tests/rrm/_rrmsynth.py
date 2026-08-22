"""rrm テスト用の合成ログ生成ヘルパ。

実データ・実データ由来の値は一切使わない。AP 名・MAC・ID は一目で偽物と
分かる値（``test-ap-0001`` / ``aabbccddee01`` 等）に固定する。

``tests/hangap/_synth.py`` / ``tests/floorpeak/_fpsynth.py`` とは **別モジュール**
（同名にすると、pytest がテストディレクトリを sys.path に足す都合で先に import
された方だけが使われる）。hangap / floorpeak 側のテスト資産は書き換えない。
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from pseudonymizer.schemas import AP_EVENTS_COLUMNS, AP_METRICS_COLUMNS

SITE_ID = "test-site-id-0001"
SITE_NAME = "TestSite"
OTHER_SITE_ID = "test-site-id-0002"
OTHER_SITE_NAME = "OtherSite"

#: 既定のサンプリング間隔（秒）。3 倍 = 900 秒が「照合不可（too_far）」の境界になる
INTERVAL = 300

AP1 = {"ap_id": "test-ap-0001", "ap_name": "TEST-AP-01", "mac": "aabbccddee01"}
AP2 = {"ap_id": "test-ap-0002", "ap_name": "TEST-AP-02", "mac": "aabbccddee02"}
AP3 = {"ap_id": "test-ap-0003", "ap_name": "TEST-AP-03", "mac": "aabbccddee03"}


def _ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def metrics_row(
    ts: datetime,
    *,
    ap_id: str = "test-ap-0001",
    ap_name: str = "TEST-AP-01",
    mac: str = "aabbccddee01",
    model: str = "AP45",
    site_id: str = SITE_ID,
    site_name: str = SITE_NAME,
    num_clients: int = 0,
    status: str = "connected",
    util_24: object = 0,
    util_5: object = 0,
    util_6: object = 0,
    **extra,
) -> dict[str, object]:
    row: dict[str, object] = {c: "" for c in AP_METRICS_COLUMNS}
    row.update(
        timestamp=_ts(ts),
        site_id=site_id,
        site_name=site_name,
        ap_id=ap_id,
        ap_name=ap_name,
        model=model,
        mac=mac,
        status=status,
        num_clients=num_clients,
        radio_24_channel=1,
        radio_5_channel=36,
        radio_6_channel=37,
        radio_24_utilization=util_24,
        radio_5_utilization=util_5,
        radio_6_utilization=util_6,
        map_id="test-map-id-1f",
        x_m=1.0,
        y_m=2.0,
    )
    row.update(extra)
    return row


def series(
    start: datetime,
    samples: Sequence[dict[str, object]],
    *,
    interval_seconds: int = INTERVAL,
    ap: dict[str, str] | None = None,
    **kwargs,
) -> list[dict[str, object]]:
    """等間隔のメトリクス行を作る。``samples`` の各要素が 1 サンプル分の値。"""
    base = dict(kwargs)
    if ap:
        base.update(ap)
    return [
        metrics_row(start + timedelta(seconds=interval_seconds * i), **{**base, **values})
        for i, values in enumerate(samples)
    ]


def at_times(
    times: Sequence[datetime],
    samples: Sequence[dict[str, object]],
    *,
    ap: dict[str, str] | None = None,
    **kwargs,
) -> list[dict[str, object]]:
    """時刻を明示してメトリクス行を作る（欠測を作りたいとき用）。"""
    base = dict(kwargs)
    if ap:
        base.update(ap)
    return [
        metrics_row(ts, **{**base, **values}) for ts, values in zip(times, samples)
    ]


def event_row(
    ts: datetime,
    *,
    event_type: str,
    reason: str = "",
    ap_name: str = "TEST-AP-01",
    ap_mac: str = "aabbccddee01",
    site_name: str = SITE_NAME,
    band: str = "5",
    channel: object = "",
    pre_channel: object = "",
    bandwidth: object = 20,
    pre_bandwidth: object = 20,
) -> dict[str, object]:
    row: dict[str, object] = {c: "" for c in AP_EVENTS_COLUMNS}
    row.update(
        event_timestamp=_ts(ts),
        site_name=site_name,
        ap_name=ap_name,
        ap_mac=ap_mac,
        event_type=event_type,
        reason=reason,
        band=band,
        channel=channel,
        pre_channel=pre_channel,
        bandwidth=bandwidth,
        pre_bandwidth=pre_bandwidth,
    )
    return row


def rrm_action(
    ts: datetime,
    *,
    reason: str = "scheduled-site-rrm",
    pre_channel: object = 36,
    channel: object = 44,
    ap: dict[str, str] | None = None,
    **kwargs,
) -> dict[str, object]:
    if ap:
        kwargs.setdefault("ap_name", ap["ap_name"])
        kwargs.setdefault("ap_mac", ap["mac"])
    return event_row(
        ts, event_type="AP_RRM_ACTION", reason=reason,
        pre_channel=pre_channel, channel=channel, **kwargs,
    )


def radar_detected(
    ts: datetime,
    *,
    pre_channel: object = 64,
    channel: object = 36,
    ap: dict[str, str] | None = None,
    **kwargs,
) -> dict[str, object]:
    if ap:
        kwargs.setdefault("ap_name", ap["ap_name"])
        kwargs.setdefault("ap_mac", ap["mac"])
    return event_row(
        ts, event_type="AP_RADAR_DETECTED", reason="radar-detected",
        pre_channel=pre_channel, channel=channel, **kwargs,
    )


def config_changed_by_rrm(
    ts: datetime, *, ap: dict[str, str] | None = None, **kwargs
) -> dict[str, object]:
    """``reason`` を持たない参考イベント（本分析では未使用）。"""
    if ap:
        kwargs.setdefault("ap_name", ap["ap_name"])
        kwargs.setdefault("ap_mac", ap["mac"])
    return event_row(
        ts, event_type="AP_CONFIG_CHANGED_BY_RRM", band="", bandwidth="", pre_bandwidth="", **kwargs,
    )


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def write_metrics(path: Path, rows: Iterable[dict[str, object]]) -> Path:
    return write_csv(path, AP_METRICS_COLUMNS, rows)


def write_events(path: Path, rows: Iterable[dict[str, object]]) -> Path:
    return write_csv(path, AP_EVENTS_COLUMNS, rows)
