"""``ap_events`` の分類（RADAR / POST_RADAR / RRM）とレーダー検知の突合。

このモジュールは **イベント側だけ** を扱う。メトリクスとの突合
（:mod:`rrm.metrics`）や集計（:mod:`rrm.analysis`）は持ち込まない。

設計上の要点:

- チャネル変更として数えるのは ``AP_RRM_ACTION`` かつ ``pre_channel != channel``
  のものだけ。``pre_channel == channel`` は「定期 RRM が評価して現状維持と判断した」
  正常な no-op であり、**異常ではない**。除外して見えなくせず、件数を別に数える。
- ``AP_RADAR_DETECTED`` は ``AP_RRM_ACTION`` とは **独立に** 数える。
  ``AP_RRM_ACTION`` だけを数えるとレーダーを取りこぼす（対応する ACTION が
  記録されていない検知が実データに存在する）。
- 1 つの ``AP_RADAR_DETECTED`` の近傍に ``AP_RRM_ACTION`` が複数並ぶことがある。
  対応は 1:1 ではないので、**検知の側を主語にして** 「対応する ACTION があるか」
  だけを見る（ACTION の件数でレーダーを数え直さない＝二重計上しない）。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: チャネル変更の主データ源
ACTION_EVENT_TYPE = "AP_RRM_ACTION"

#: レーダー検知。それ自体が ``pre_channel -> channel`` を持つ
RADAR_EVENT_TYPE = "AP_RADAR_DETECTED"

#: 参考としてのみ数える種別（本分析では使わない。§「使わないイベント」）
CONFIG_RRM_EVENT_TYPE = "AP_CONFIG_CHANGED_BY_RRM"

#: 「同一 AP の別のチャネル変更」とみなす種別（汚染検知に使う）
CHANNEL_CHANGE_EVENT_TYPES: tuple[str, ...] = (ACTION_EVENT_TYPE, RADAR_EVENT_TYPE)

REASON_RADAR = "radar-detected"
REASON_POST_RADAR = "post-radar"

CLASS_RADAR = "RADAR"
CLASS_POST_RADAR = "POST_RADAR"
CLASS_RRM = "RRM"

#: 表示・集計での並び順（この順で固定する）
CLASSIFICATIONS: tuple[str, ...] = (CLASS_RADAR, CLASS_POST_RADAR, CLASS_RRM)

#: 分類ごとの色（RRGGBB）。**ここが唯一の定義**。フロント／xlsx で定義し直さない
CLASS_COLORS: dict[str, str] = {
    CLASS_RADAR: "D62728",
    CLASS_POST_RADAR: "FF7F0E",
    CLASS_RRM: "1F77B4",
}

#: ``AP_RADAR_DETECTED`` と ``AP_RRM_ACTION``(radar-detected) を対応付ける時間差（秒）
RADAR_MATCH_SECONDS: float = 300.0

#: :func:`action_frame` が必ず返す列
ACTION_COLUMNS: tuple[str, ...] = (
    "event_timestamp", "site_name", "ap_name", "ap_mac", "event_type", "reason",
    "band", "classification", "pre_channel_num", "post_channel_num",
    "channel_changed", "channel_known",
)


def classify(reason: object) -> str:
    """``reason`` から分類を決める。

    ``post-radar`` を RADAR にも RRM にも入れないのは、どちらに入れても実態と
    合わないため（レーダー起因の後処理であることが分かる形で分けて数える）。
    """
    text = ("" if reason is None else str(reason)).strip()
    if text == REASON_RADAR:
        return CLASS_RADAR
    if text == REASON_POST_RADAR:
        return CLASS_POST_RADAR
    return CLASS_RRM


def _empty_actions() -> pd.DataFrame:
    empty = pd.DataFrame(columns=list(ACTION_COLUMNS))
    empty["event_timestamp"] = pd.to_datetime(empty["event_timestamp"])
    empty["channel_changed"] = empty["channel_changed"].astype(bool)
    empty["channel_known"] = empty["channel_known"].astype(bool)
    return empty


def action_frame(events: pd.DataFrame) -> pd.DataFrame:
    """``AP_RRM_ACTION`` を抜き出し、分類とチャネル変更の有無を付けて返す。

    ``pre_channel`` / ``channel`` のどちらかが読めない行は ``channel_known=False``
    にする。**「変更なし（no-op）」と混ぜないこと**（評価した結果の現状維持と、
    そもそも記録が欠けているのは別の状態である）。
    """
    if events.empty:
        return _empty_actions()
    df = events[events["event_type"].astype(str) == ACTION_EVENT_TYPE].copy()
    if df.empty:
        return _empty_actions()

    df["classification"] = [classify(r) for r in df["reason"]]
    pre = pd.to_numeric(df.get("pre_channel"), errors="coerce")
    post = pd.to_numeric(df.get("channel"), errors="coerce")
    df["pre_channel_num"] = pre
    df["post_channel_num"] = post
    df["channel_known"] = pre.notna() & post.notna()
    df["channel_changed"] = df["channel_known"] & (pre != post)

    df = df.sort_values(
        ["event_timestamp", "site_name", "ap_name", "reason"], kind="stable"
    )
    return df.reset_index(drop=True)


@dataclass(frozen=True)
class RadarSummary:
    """``AP_RADAR_DETECTED`` の集計。``AP_RRM_ACTION`` とは独立に数える。"""

    #: 期間内の検知回数
    detected: int = 0
    #: そのうち検知イベント自体が ``pre_channel != channel`` だったもの
    with_channel_change: int = 0
    #: 対応する ``AP_RRM_ACTION``(radar-detected) が見つからなかったもの
    without_action: int = 0
    #: 突合に使った時間差（秒）
    match_seconds: float = RADAR_MATCH_SECONDS


def radar_summary(
    events_in_window: pd.DataFrame,
    events_all: pd.DataFrame,
    *,
    match_seconds: float = RADAR_MATCH_SECONDS,
) -> RadarSummary:
    """レーダー検知を数える。

    :param events_in_window: 期間で絞った ap_events（**数える対象**）
    :param events_all: 期間で絞る **前** の ap_events（対応する ACTION を探す先）。
        窓の端にある検知が、窓の外にある ACTION を取りこぼして
        「ACTION 未記録」に化けるのを防ぐ。

    検知 1 件につき判定は 1 回だけ行う。近傍に ACTION が複数あっても
    検知の数は増えない（**二重計上しない**）。
    """
    if events_in_window.empty:
        return RadarSummary(match_seconds=match_seconds)
    radar = events_in_window[events_in_window["event_type"].astype(str) == RADAR_EVENT_TYPE]
    if radar.empty:
        return RadarSummary(match_seconds=match_seconds)

    pre = pd.to_numeric(radar.get("pre_channel"), errors="coerce")
    post = pd.to_numeric(radar.get("channel"), errors="coerce")
    with_change = int((pre.notna() & post.notna() & (pre != post)).sum())

    # 対応候補（radar-detected の ACTION）を AP ごとに時刻の配列にしておく
    by_ap: dict[str, list[pd.Timestamp]] = {}
    if not events_all.empty:
        hits = events_all[
            (events_all["event_type"].astype(str) == ACTION_EVENT_TYPE)
            & (events_all["reason"].astype(str) == REASON_RADAR)
        ]
        for mac, ts in zip(hits.get("ap_mac", []), hits.get("event_timestamp", [])):
            by_ap.setdefault(str(mac), []).append(ts)

    without = 0
    for mac, ts in zip(radar["ap_mac"], radar["event_timestamp"]):
        candidates = by_ap.get(str(mac), ())
        matched = any(
            abs((cand - ts).total_seconds()) <= match_seconds for cand in candidates
        )
        if not matched:
            without += 1

    return RadarSummary(
        detected=int(len(radar)),
        with_channel_change=with_change,
        without_action=without,
        match_seconds=match_seconds,
    )


def count_event_type(events: pd.DataFrame, event_type: str) -> int:
    """指定種別の件数（``AP_CONFIG_CHANGED_BY_RRM`` の参考カウント用）。"""
    if events.empty:
        return 0
    return int((events["event_type"].astype(str) == event_type).sum())
