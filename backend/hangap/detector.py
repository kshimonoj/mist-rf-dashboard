"""ゼロクライアント区間（ハングAP 候補）の検出エンジン。

入力は :func:`hangap.loader.load` の戻り値（``metrics`` / ``events`` / ``gaps``）。
本モジュールは純粋なデータ処理のみを行う（ネットワークアクセス・LLM 呼び出しはしない）。

設計方針:
- 手作業で行っていた分析（先頭 18 列）を機械的に再現できることを最優先とする。
- 「連続ゼロ」を数えるうえで最も危険なのは **欠測（ギャップ）を跨いで連結してしまう** こと。
  エラーにならず連続ゼロ回数だけが過大になるため、ローダの ``gaps`` を使って必ず打ち切る。
- 判定材料（サイト全体の増減など）は列として足すだけで、**行は落とさない**。
  絞り込みは利用者側の責務とする。
- 周辺 AP の判定（距離・RF 隣接・近傍集合）はここには置かない。

.. warning::
   ``min_zero_samples`` は **サンプル数** であって時間ではない。サンプリング間隔は環境で
   異なり（実測でデモ環境 30 秒 / 顧客環境 5 分）、既定の ``min_zero_samples=5`` は
   5 分間隔なら 25 分だが 30 秒間隔ではわずか 2.5 分にしかならない。
   間隔の異なる環境を見るときは ``min_zero_duration``（時間指定）を使うこと。
   指定された場合は ``min_zero_samples`` より優先される。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Sequence

import pandas as pd

# ---------------------------------------------------------------------------
# 既定値
# ---------------------------------------------------------------------------

#: 採用する連続ゼロの最小サンプル数（時間ではない。上の warning を参照）
DEFAULT_MIN_ZERO_SAMPLES: int = 5

#: イベント相関の窓（ゼロ終了 ± この時間）
DEFAULT_EVENT_WINDOW: timedelta = timedelta(minutes=30)

#: 「退場疑い」と判定するサイト全体変化率のしきい値（区間中に全体が半減）
DEFAULT_EXODUS_THRESHOLD: float = -0.5

#: ゼロ区間の構成要素として認める status
CONNECTED: str = "connected"

# 回復状況の 3 値（+ 打ち切り 2 種）
STATUS_RECOVERED = "回復"
STATUS_ONGOING = "継続中"
STATUS_CUT_GAP = "打ち切り(欠測)"
STATUS_CUT_AP_DOWN = "打ち切り(AP停止)"

#: 出力列（先頭 18 列は手作業の分析結果と同一の名前・順序。19 列目以降が自動化での追加分）
#: 「AP Event（±30分）」は手作業の分析と同じ列名を保つため、``event_window`` を変えても固定。
RESULT_COLUMNS: tuple[str, ...] = (
    "ap_name",
    "site_name",
    "区間番号",
    "AP内区間数",
    "ゼロ直前時刻",
    "直前clients",
    "直後clients（回復時）",
    "ゼロ開始",
    "ゼロ終了",
    "連続ゼロ回数",
    "回復状況",
    "回復時刻",
    "AP最大clients",
    "AP Event（±30分）",
    "Event時刻",
    "ゼロ終了との差(分)",
    "Event種別",
    "Event詳細",
    "サイト合計clients(ゼロ開始時)",
    "サイト合計clients(ゼロ終了時)",
    "サイト全体変化率",
    "退場疑い",
)

#: イベント列の区切り（4 列すべて同じ順序・同じ件数で並ぶ）
EVENT_SEPARATOR: str = " | "

#: metrics に必須の列
_REQUIRED_METRICS_COLUMNS: tuple[str, ...] = (
    "ap_id", "ap_name", "site_name", "timestamp", "num_clients", "status",
)

#: Event詳細 に載せるイベント列（値があるものだけを ", " で連結する）
_DETAIL_PAIRS: tuple[tuple[str, str], ...] = (
    ("channel", "pre_channel"),
    ("bandwidth", "pre_bandwidth"),
)


# ---------------------------------------------------------------------------
# 小さなヘルパ
# ---------------------------------------------------------------------------


def _as_timedelta(value: timedelta | pd.Timedelta | float | int | str) -> pd.Timedelta:
    """timedelta / 秒数 / 文字列を Timedelta へ揃える。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return pd.Timedelta(seconds=float(value))
    return pd.Timedelta(value)


def _fmt_ts(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_minutes(delta: pd.Timedelta) -> str:
    """ゼロ終了との差を符号付きの分で表す（0 は符号なし）。"""
    minutes = delta.total_seconds() / 60.0
    # 0.5 分は 0 から遠い側へ丸める（銀行丸めだと ±0.5 が 0 に寄って符号が消えるため）
    rounded = int(minutes + (0.5 if minutes >= 0 else -0.5))
    if rounded == 0:
        return "0"
    return f"{rounded:+d}"


def _fmt_number(value: object) -> str:
    """channel / bandwidth を整数として見せる（36.0 → "36"）。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if pd.isna(value):
        return ""
    if isinstance(value, float) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _event_detail(row: pd.Series) -> str:
    """``reason=...`` / ``band=...`` / ``channel=A→B`` / ``bandwidth=A→B`` を連結する。

    値の無いものは出さない（すべて空なら空文字）。重み付け・分類・フィルタは行わない。
    """
    parts: list[str] = []
    for key in ("reason", "band"):
        value = _text(row.get(key)) if hasattr(row, "get") else ""
        if value:
            parts.append(f"{key}={value}")
    for key, pre_key in _DETAIL_PAIRS:
        cur = _fmt_number(row.get(key))
        pre = _fmt_number(row.get(pre_key))
        if cur or pre:
            parts.append(f"{key}={pre}→{cur}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# 前処理
# ---------------------------------------------------------------------------


def _prepare_metrics(
    metrics: pd.DataFrame,
    window_start: datetime | None,
    window_end: datetime | None,
) -> pd.DataFrame:
    """窓 ``[window_start, window_end)`` で切り出し、型を揃えた metrics を返す。

    窓は「走査対象のサンプル」を決める。窓の外のサンプルは回復判定にも使わない
    （手作業の分析が窓の右端で切っていたのと同じ振る舞いにする）。
    """
    missing = [c for c in _REQUIRED_METRICS_COLUMNS if c not in metrics.columns]
    if missing:
        raise KeyError(f"metrics に必要な列がありません: {missing}")

    df = metrics.loc[:, list(_REQUIRED_METRICS_COLUMNS)].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df[df["timestamp"].notna()]
    if window_start is not None:
        df = df[df["timestamp"] >= pd.Timestamp(window_start)]
    if window_end is not None:
        df = df[df["timestamp"] < pd.Timestamp(window_end)]

    df["num_clients"] = pd.to_numeric(df["num_clients"], errors="coerce")
    df["status"] = df["status"].astype("string").fillna("").str.strip().str.lower()
    df["ap_id"] = df["ap_id"].astype("string").fillna("")
    df["ap_name"] = df["ap_name"].astype("string").fillna("")
    df["site_name"] = df["site_name"].astype("string").fillna("")
    return df.sort_values(["ap_id", "timestamp"], kind="stable").reset_index(drop=True)


def _gap_boundaries(gaps: pd.DataFrame | None) -> dict[str, set[tuple[pd.Timestamp, pd.Timestamp]]]:
    """ap_id ごとの (gap_start, gap_end) 集合。連続 2 サンプルの間の欠測を表す。"""
    out: dict[str, set[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    if gaps is None or len(gaps) == 0:
        return out
    for ap_id, start, end in zip(gaps["ap_id"], gaps["gap_start"], gaps["gap_end"]):
        out.setdefault(str(ap_id), set()).add((pd.Timestamp(start), pd.Timestamp(end)))
    return out


def _site_totals(metrics: pd.DataFrame) -> dict[str, pd.Series]:
    """site_name ごとの「その時刻の全 AP の num_clients 合計」系列。

    AP ごとに数秒〜数十秒のずれがあるため、直前の値を持ち回って（ffill）合計する。
    欠測を跨いで古い値を引きずる点は承知のうえ（合計は判定材料であって検出条件ではない）。
    """
    totals: dict[str, pd.Series] = {}
    if metrics.empty:
        return totals
    for site, grp in metrics.groupby("site_name", sort=False):
        pivot = grp.pivot_table(
            index="timestamp", columns="ap_id", values="num_clients", aggfunc="max"
        )
        totals[str(site)] = pivot.ffill().sum(axis=1, min_count=1)
    return totals


def _events_by_ap(events: pd.DataFrame | None) -> dict[str, pd.DataFrame]:
    """ap_name ごとのイベント（時刻昇順）。"""
    out: dict[str, pd.DataFrame] = {}
    if events is None or len(events) == 0:
        return out
    if "ap_name" not in events.columns or "event_timestamp" not in events.columns:
        return out
    df = events.copy()
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"], errors="coerce")
    df = df[df["event_timestamp"].notna()]
    df["ap_name"] = df["ap_name"].astype("string").fillna("")
    for ap_name, grp in df.groupby("ap_name", sort=False):
        out[str(ap_name)] = grp.sort_values("event_timestamp", kind="stable").reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# 区間の走査
# ---------------------------------------------------------------------------


def _scan_ap(
    timestamps: Sequence[pd.Timestamp],
    clients: Sequence[float],
    statuses: Sequence[str],
    gap_before: Sequence[bool],
) -> list[dict]:
    """1 AP 分の系列からゼロ区間を切り出す（しきい値の適用前）。

    区間の開始条件は次のいずれか:
      - 直前サンプルの num_clients >= 1、当該サンプルが 0（本来の 1→0 の遷移）
      - 直前の区間が打ち切られた直後で、まだ 0 が続いている（欠測／AP停止の向こう側）

    区間は次のいずれかで終わる:
      - 次のサンプルが無い                 → 継続中
      - 次のサンプルとの間に欠測がある     → 打ち切り(欠測)
      - 次のサンプルが connected でない    → 打ち切り(AP停止)
      - 次のサンプルの num_clients >= 1    → 回復
    """
    n = len(timestamps)
    intervals: list[dict] = []
    if n < 2:
        return intervals

    def is_zero(i: int) -> bool:
        c = clients[i]
        return c is not None and not pd.isna(c) and float(c) == 0.0

    def has_clients(i: int) -> bool:
        c = clients[i]
        return c is not None and not pd.isna(c) and float(c) >= 1.0

    resume_pending = False  # 直前の区間が打ち切られ、まだ 0 が続いている
    i = 1
    while i < n:
        connected = statuses[i] == CONNECTED
        if not (connected and is_zero(i) and (has_clients(i - 1) or resume_pending)):
            if connected and has_clients(i):
                resume_pending = False
            i += 1
            continue

        resume_pending = False
        last = i
        while True:
            nxt = last + 1
            if nxt >= n:
                end_status, recovery = STATUS_ONGOING, None
                break
            if gap_before[nxt]:
                end_status, recovery = STATUS_CUT_GAP, None
                break
            if statuses[nxt] != CONNECTED:
                end_status, recovery = STATUS_CUT_AP_DOWN, None
                break
            if has_clients(nxt):
                end_status, recovery = STATUS_RECOVERED, nxt
                break
            if not is_zero(nxt):
                # num_clients が欠けている行。0 とも回復とも言えないので欠測扱いで打ち切る
                end_status, recovery = STATUS_CUT_GAP, None
                break
            last = nxt

        intervals.append(
            {
                "start": i,
                "last": last,
                "end_status": end_status,
                "recovery": recovery,
                "samples": last - i + 1,
            }
        )
        if end_status in (STATUS_CUT_GAP, STATUS_CUT_AP_DOWN):
            resume_pending = True
        i = last + 1
    return intervals


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def detect(
    metrics: pd.DataFrame,
    events: pd.DataFrame | None = None,
    gaps: pd.DataFrame | None = None,
    *,
    window_start: datetime | None,
    window_end: datetime | None,
    min_zero_samples: int = DEFAULT_MIN_ZERO_SAMPLES,
    min_zero_duration: timedelta | float | str | None = None,
    event_window: timedelta | float | str = DEFAULT_EVENT_WINDOW,
    exodus_threshold: float = DEFAULT_EXODUS_THRESHOLD,
) -> pd.DataFrame:
    """ゼロクライアント区間を検出して DataFrame（列は :data:`RESULT_COLUMNS`）で返す。

    :param metrics: ローダの ``metrics``（ap_id / ap_name / site_name / timestamp /
        num_clients / status を含むこと）
    :param events: ローダの ``events``。``ap_name`` で突合する。None ならイベント列は空。
    :param gaps: ローダの ``gaps``。**必ず渡すこと。** None だと欠測を跨いだ区間を
        連結してしまい、連続ゼロ回数が過大になる（エラーにならないため気づけない）。
    :param window_start: 走査する窓の開始（含む）。None なら制限なし。
    :param window_end: 走査する窓の終わり（含まない）。None なら制限なし。
    :param min_zero_samples: 採用する連続ゼロの最小 **サンプル数**（時間ではない）。
        サンプリング間隔は環境で異なる（実測でデモ 30 秒 / 顧客 5 分）ため、
        既定の 5 は 5 分間隔なら 25 分、30 秒間隔ではわずか 2.5 分にしかならない。
    :param min_zero_duration: 採用する連続ゼロの最小 **時間**（timedelta / 秒数 / 文字列）。
        長さは「最初のゼロサンプル → 最後のゼロサンプル」で測る（1 サンプルなら 0）。
        指定された場合は ``min_zero_samples`` より優先する。
    :param event_window: イベント相関の窓（ゼロ終了 ± この時間）。既定 30 分。
    :param exodus_threshold: 退場疑いのしきい値。サイト全体変化率がこれ以下なら True。

    結果からは「継続中」も「退場疑い」も除外しない（進行中のハングを取りこぼさないため）。
    """
    prepared = _prepare_metrics(metrics, window_start, window_end)
    boundaries = _gap_boundaries(gaps)
    totals = _site_totals(prepared)
    events_by_ap = _events_by_ap(events)
    ev_window = _as_timedelta(event_window)
    min_duration = _as_timedelta(min_zero_duration) if min_zero_duration is not None else None

    rows: list[dict] = []
    for ap_id, grp in prepared.groupby("ap_id", sort=False):
        timestamps = list(grp["timestamp"])
        clients = list(grp["num_clients"])
        statuses = list(grp["status"])
        ap_names = list(grp["ap_name"])
        site_names = list(grp["site_name"])

        gap_pairs = boundaries.get(str(ap_id), set())
        gap_before = [False] * len(timestamps)
        if gap_pairs:
            for k in range(1, len(timestamps)):
                if (timestamps[k - 1], timestamps[k]) in gap_pairs:
                    gap_before[k] = True

        found = _scan_ap(timestamps, clients, statuses, gap_before)

        # しきい値の適用（時間指定があればそちらを優先）
        adopted = []
        for iv in found:
            if min_duration is not None:
                length = timestamps[iv["last"]] - timestamps[iv["start"]]
                if length >= min_duration:
                    adopted.append(iv)
            elif iv["samples"] >= min_zero_samples:
                adopted.append(iv)
        if not adopted:
            continue

        ap_max_clients = grp["num_clients"].max()

        for number, iv in enumerate(adopted, start=1):
            start_i, last_i = iv["start"], iv["last"]
            zero_start, zero_end = timestamps[start_i], timestamps[last_i]
            recovery = iv["recovery"]
            site = str(site_names[start_i])
            total = totals.get(site)
            ap_events = events_by_ap.get(str(ap_names[start_i]))

            row = {
                "ap_name": str(ap_names[start_i]),
                "site_name": site,
                "区間番号": number,
                "AP内区間数": len(adopted),
                "ゼロ直前時刻": timestamps[start_i - 1],
                "直前clients": clients[start_i - 1],
                "直後clients（回復時）": clients[recovery] if recovery is not None else pd.NA,
                "ゼロ開始": zero_start,
                "ゼロ終了": zero_end,
                "連続ゼロ回数": iv["samples"],
                "回復状況": iv["end_status"],
                "回復時刻": timestamps[recovery] if recovery is not None else pd.NaT,
                "AP最大clients": ap_max_clients,
            }
            row.update(_event_columns(ap_events, zero_end, ev_window))
            row.update(_site_columns(total, zero_start, zero_end, exodus_threshold))
            rows.append(row)

    return _to_frame(rows)


def _event_columns(
    ap_events: pd.DataFrame | None,
    zero_end: pd.Timestamp,
    event_window: pd.Timedelta,
) -> dict[str, str]:
    """ゼロ終了 ± event_window のイベントを時刻昇順で列挙する（4 列とも同じ並び・件数）。"""
    empty = {
        "AP Event（±30分）": "",
        "Event時刻": "",
        "ゼロ終了との差(分)": "",
        "Event種別": "",
        "Event詳細": "",
    }
    if ap_events is None or ap_events.empty:
        return empty
    lo, hi = zero_end - event_window, zero_end + event_window
    hits = ap_events[
        (ap_events["event_timestamp"] >= lo) & (ap_events["event_timestamp"] <= hi)
    ]
    if hits.empty:
        return empty

    times, deltas, types, details = [], [], [], []
    for _, ev in hits.iterrows():
        ts = pd.Timestamp(ev["event_timestamp"])
        times.append(_fmt_ts(ts))
        deltas.append(_fmt_minutes(ts - zero_end))
        types.append(_text(ev.get("event_type")))
        details.append(_event_detail(ev))
    return {
        "AP Event（±30分）": "あり",
        "Event時刻": EVENT_SEPARATOR.join(times),
        "ゼロ終了との差(分)": EVENT_SEPARATOR.join(deltas),
        "Event種別": EVENT_SEPARATOR.join(types),
        "Event詳細": EVENT_SEPARATOR.join(details),
    }


def _site_columns(
    total: pd.Series | None,
    zero_start: pd.Timestamp,
    zero_end: pd.Timestamp,
    exodus_threshold: float,
) -> dict[str, object]:
    """サイト全体の増減（退場疑いの判定材料）。行を落とすためには使わない。"""
    start_total = float(total.asof(zero_start)) if total is not None and len(total) else float("nan")
    end_total = float(total.asof(zero_end)) if total is not None and len(total) else float("nan")
    if pd.isna(start_total) or pd.isna(end_total) or start_total == 0:
        ratio: object = pd.NA
        exodus = False
    else:
        ratio = (end_total - start_total) / start_total
        exodus = bool(ratio <= exodus_threshold)
    return {
        "サイト合計clients(ゼロ開始時)": start_total,
        "サイト合計clients(ゼロ終了時)": end_total,
        "サイト全体変化率": ratio,
        "退場疑い": exodus,
    }


def _to_frame(rows: Iterable[dict]) -> pd.DataFrame:
    """列順・dtype を揃えた DataFrame にする。"""
    df = pd.DataFrame(list(rows), columns=list(RESULT_COLUMNS))
    for col in ("ゼロ直前時刻", "ゼロ開始", "ゼロ終了", "回復時刻"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in (
        "区間番号", "AP内区間数", "直前clients", "直後clients（回復時）",
        "連続ゼロ回数", "AP最大clients",
        "サイト合計clients(ゼロ開始時)", "サイト合計clients(ゼロ終了時)",
    ):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["サイト全体変化率"] = pd.to_numeric(df["サイト全体変化率"], errors="coerce")
    df["退場疑い"] = df["退場疑い"].fillna(False).astype(bool)
    for col in ("ap_name", "site_name", "回復状況", "AP Event（±30分）",
                "Event時刻", "ゼロ終了との差(分)", "Event種別", "Event詳細"):
        df[col] = df[col].astype("string").fillna("")
    if df.empty:
        return df
    return df.sort_values(["ap_name", "区間番号"], kind="stable").reset_index(drop=True)
