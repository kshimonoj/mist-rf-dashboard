"""イベント時刻の **直前 1 サンプル / 直後 1 サンプル** を ``ap_metrics`` から取る。

設計上の要点:

- **平均は取らない。** 前後 1 サンプルずつをそのまま出す。平均にすると、変更の
  前後で何が起きたのかが均されて読めなくなる。
- 突合は ``ap_mac``（= ap_metrics の ``mac``。コロン無し小文字）で行う。
- 「直前」は **イベント時刻より厳密に前** の最後のサンプル、「直後」は
  **イベント時刻以降** の最初のサンプル。同秒のサンプルは「直後」に入れる
  （どちらに寄せるかを決めておかないと結果が再現しない）。
- 前後どちらかが無い、または推定間隔の :data:`MAX_GAP_FACTOR` 倍以上離れている
  場合は「照合不可」とし、理由（``match_status``）を残す。**行は捨てない。**
- 汚染: 直前サンプルから直後サンプルまでの区間に、同一 AP の別のチャネル変更
  イベントがあれば印を付ける。**ただし同一 AP・:data:`CONTAMINATION_GROUP_SECONDS`
  秒以内の「別バンド」のイベントは汚染とみなさない。** 1 回の RRM トリガーが
  複数バンドに同時に及んだだけであり、互いを汚染し合う関係ではないため
  （実データで、同一 AP・同秒の他バンド変更が汚染フラグの大半を占めていた）。
  同一バンドの別イベントは、時間窓の内外を問わず汚染のまま扱う（その帯域だけ
  短時間に 2 回目の変更が入ったことを意味し、前後比較の信頼性を損なうため）。
  **汚染した行も除外しない。**

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

from .events import CHANNEL_CHANGE_EVENT_TYPES

#: 照合の状態（``match_status`` 列に出る列挙値）
MATCH_OK = "ok"
MATCH_NO_BEFORE = "no_before"
MATCH_NO_AFTER = "no_after"
MATCH_TOO_FAR = "too_far"
#: ap_metrics にその AP のサンプルが 1 件も無い（前後どころか AP が無い）
MATCH_NO_AP = "no_ap"

MATCH_STATUSES: tuple[str, ...] = (
    MATCH_OK, MATCH_NO_BEFORE, MATCH_NO_AFTER, MATCH_TOO_FAR, MATCH_NO_AP,
)

#: 推定間隔の何倍以上離れたら「照合不可（too_far）」とみなすか
MAX_GAP_FACTOR: float = 3.0

#: 汚染判定で「同一 RRM アクショングループ」とみなす時間窓（秒）。定義はここ 1 箇所のみ。
CONTAMINATION_GROUP_SECONDS: float = 5.0

#: 前後で拾う値の列（ap_metrics の列名）。利用率は **3 バンドすべて** を出す
#: （イベントの band に関わらず。他バンドの影響を隠さないため）
SAMPLE_COLUMNS: tuple[str, ...] = (
    "num_clients",
    "radio_24_utilization",
    "radio_5_utilization",
    "radio_6_utilization",
)


@dataclass(frozen=True)
class SampleMatch:
    """1 イベント分の突合結果。"""

    status: str
    before_timestamp: pd.Timestamp | None = None
    after_timestamp: pd.Timestamp | None = None
    #: 見つかった範囲で埋める（``status != ok`` でも値は残す。差分だけを空にする）
    before: dict[str, float | None] = field(default_factory=dict)
    after: dict[str, float | None] = field(default_factory=dict)
    #: 前後区間に同一 AP の別のチャネル変更イベントがあった
    contaminated: bool = False

    @property
    def ok(self) -> bool:
        return self.status == MATCH_OK


def _value(array: np.ndarray, index: int) -> float | None:
    value = float(array[index])
    return None if np.isnan(value) else value


class MetricIndex:
    """AP（mac）ごとに時刻順のサンプルを持ち、二分探索で前後 1 件を返す。"""

    def __init__(
        self,
        metrics: pd.DataFrame,
        *,
        columns: Sequence[str] = SAMPLE_COLUMNS,
    ) -> None:
        self.columns = tuple(columns)
        self._by_mac: dict[str, tuple[np.ndarray, dict[str, np.ndarray]]] = {}
        if metrics is None or metrics.empty:
            return
        df = metrics.sort_values("timestamp", kind="stable")
        macs = df["mac"].astype("string").fillna("").astype(str)
        for mac, group in df.groupby(macs, sort=False):
            if not mac:
                continue
            times = group["timestamp"].to_numpy(dtype="datetime64[ns]")
            values = {
                col: pd.to_numeric(group.get(col), errors="coerce").to_numpy(dtype=float)
                for col in self.columns
            }
            self._by_mac[mac] = (times, values)

    def __contains__(self, mac: object) -> bool:
        return str(mac) in self._by_mac

    @property
    def ap_count(self) -> int:
        return len(self._by_mac)

    def match(
        self,
        mac: object,
        timestamp: pd.Timestamp,
        *,
        interval_seconds: float,
        gap_factor: float = MAX_GAP_FACTOR,
    ) -> SampleMatch:
        """``timestamp`` の直前・直後 1 サンプルを返す。"""
        entry = self._by_mac.get(str(mac))
        if entry is None:
            return SampleMatch(status=MATCH_NO_AP)
        times, values = entry

        key = np.datetime64(pd.Timestamp(timestamp))
        idx = int(np.searchsorted(times, key, side="left"))
        before_i = idx - 1 if idx > 0 else None
        after_i = idx if idx < len(times) else None

        before_ts = pd.Timestamp(times[before_i]) if before_i is not None else None
        after_ts = pd.Timestamp(times[after_i]) if after_i is not None else None
        before = (
            {col: _value(values[col], before_i) for col in self.columns}
            if before_i is not None else {}
        )
        after = (
            {col: _value(values[col], after_i) for col in self.columns}
            if after_i is not None else {}
        )

        if before_i is None:
            status = MATCH_NO_BEFORE
        elif after_i is None:
            status = MATCH_NO_AFTER
        else:
            limit = float(interval_seconds) * float(gap_factor)
            gap_before = (pd.Timestamp(timestamp) - before_ts).total_seconds()
            gap_after = (after_ts - pd.Timestamp(timestamp)).total_seconds()
            status = MATCH_TOO_FAR if (gap_before >= limit or gap_after >= limit) else MATCH_OK

        return SampleMatch(
            status=status,
            before_timestamp=before_ts,
            after_timestamp=after_ts,
            before=before,
            after=after,
        )


class ChangeEventIndex:
    """汚染判定用。AP（mac）ごとのチャネル変更イベント時刻とバンド。"""

    def __init__(self, events: pd.DataFrame) -> None:
        self._by_mac: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        if events is None or events.empty:
            return
        hits = events[events["event_type"].astype(str).isin(CHANNEL_CHANGE_EVENT_TYPES)]
        if hits.empty:
            return
        hits = hits.sort_values("event_timestamp", kind="stable")
        macs = hits["ap_mac"].astype("string").fillna("").astype(str)
        bands = hits["band"].astype("string").fillna("").astype(str)
        for mac, group in hits.groupby(macs, sort=False):
            if not mac:
                continue
            times = group["event_timestamp"].to_numpy(dtype="datetime64[ns]")
            group_bands = bands.loc[group.index].to_numpy(dtype=object)
            self._by_mac[mac] = (times, group_bands)

    def count_between(
        self, mac: object, start: pd.Timestamp, end: pd.Timestamp
    ) -> int:
        """``[start, end]``（両端を含む）にある同一 AP のチャネル変更イベント数。"""
        entry = self._by_mac.get(str(mac))
        if entry is None:
            return 0
        times, _ = entry
        lo = int(np.searchsorted(times, np.datetime64(pd.Timestamp(start)), side="left"))
        hi = int(np.searchsorted(times, np.datetime64(pd.Timestamp(end)), side="right"))
        return max(0, hi - lo)

    def is_contaminated(
        self,
        mac: object,
        event_timestamp: pd.Timestamp,
        band: object,
        match: SampleMatch,
        *,
        group_seconds: float = CONTAMINATION_GROUP_SECONDS,
    ) -> bool:
        """前後区間に **自分の RRM アクショングループ以外** のチャネル変更イベントがあるか。

        同一 AP・``group_seconds`` 秒以内の「別バンド」のイベントは、1 回のトリガーが
        複数バンドに同時に及んだだけとみなして汚染扱いにしない。それ以外
        （時間窓の外、または同一バンドの別イベント）は汚染とする。

        前後どちらかのサンプルが無い行は区間を確定できないので判定しない
        （照合不可の行に汚染の印は付けない）。
        """
        if match.before_timestamp is None or match.after_timestamp is None:
            return False
        entry = self._by_mac.get(str(mac))
        if entry is None:
            return False
        times, bands = entry
        lo = int(np.searchsorted(times, np.datetime64(pd.Timestamp(match.before_timestamp)), side="left"))
        hi = int(np.searchsorted(times, np.datetime64(pd.Timestamp(match.after_timestamp)), side="right"))

        own_ts = pd.Timestamp(event_timestamp)
        own_band = str(band)
        skipped_self = False
        for t, b in zip(times[lo:hi], bands[lo:hi]):
            t_ts = pd.Timestamp(t)
            b_str = str(b)
            if not skipped_self and t_ts == own_ts and b_str == own_band:
                # 区間には必ず自分自身が 1 件含まれる（before < event <= after）。
                # 最初に見つかった一致だけを自分自身として除く
                skipped_self = True
                continue
            same_group = abs((t_ts - own_ts).total_seconds()) <= group_seconds and b_str != own_band
            if same_group:
                continue
            return True
        return False
