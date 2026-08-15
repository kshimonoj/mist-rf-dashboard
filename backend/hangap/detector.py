"""ゼロクライアント区間（ハングAP 候補）の検出エンジン。

入力は :func:`hangap.loader.load` の戻り値（``metrics`` / ``events`` / ``gaps``）。
本モジュールは純粋なデータ処理のみを行う（ネットワークアクセス・LLM 呼び出しはしない）。

設計方針:
- 手作業で行っていた分析（先頭 18 列）を機械的に再現できることを最優先とする。
- 「連続ゼロ」を数えるうえで最も危険なのは **欠測（ギャップ）を跨いで連結してしまう** こと。
  エラーにならず連続ゼロ回数だけが過大になるため、ローダの ``gaps`` を使って必ず打ち切る。
  ただし ``missing_samples == 0`` のギャップ（サンプリング間隔のジッタ。実測で 300 秒間隔に
  対し 506 秒。データは 1 件も欠けていない）は打ち切り対象にしない。
- ``window_start`` / ``window_end`` は **「区間のゼロ開始がその範囲内にあるか」の判定にだけ使う**。
  区間の終了・回復判定・直後clients・イベント相関・ゼロ直前時刻には、窓の外を含む
  読み込み済みの全サンプルを使う（サンプル自体を窓で切り落とさない）。
- 判定材料（サイト全体の増減など）は列として足すだけで、**行は落とさない**。
  絞り込みは利用者側の責務とする。``周辺AP判定`` も同じで、行のフィルタには使わない。
- 周辺 AP の判定ロジック（距離・近傍集合）そのものは :mod:`hangap.neighbors` に置き、
  ここでは区間ごとに列を足すだけにする。

.. warning::
   ``min_zero_samples`` は **サンプル数** であって時間ではない。サンプリング間隔は環境で
   異なり（実測でデモ環境 30 秒 / 顧客環境 5 分）、既定の ``min_zero_samples=5`` は
   5 分間隔なら 25 分だが 30 秒間隔ではわずか 2.5 分にしかならない。
   間隔の異なる環境を見るときは ``min_zero_duration``（時間指定）を使うこと。
   指定された場合は ``min_zero_samples`` より優先される。
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta
from typing import Iterable, Sequence

import pandas as pd

from . import neighbors as _neighbors
from .neighbors import (  # 再エクスポート（呼び出し側が neighbors を import せずに済むように）
    DEFAULT_MAX_DISTANCE_M,
    DEFAULT_NEIGHBOR_CLIENT_THRESHOLD,
    DEFAULT_NEIGHBOR_COUNT,
    NEIGHBOR_COLUMNS,
    NeighborContext,
)

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

#: 打ち切り(欠測)がこの割合を超えたら「結果が分析に耐えない」と警告する
DEFAULT_TRUNCATED_WARN_RATIO: float = 0.3

#: 出力列の先頭 22 列。**名前も順序も変えないこと**（手作業の分析結果との照合に使う）。
#: 先頭 18 列は手作業の分析結果と同一。19 列目以降が自動化での追加分。
#: 「AP Event（±30分）」は手作業の分析と同じ列名を保つため、``event_window`` を変えても固定。
CORE_RESULT_COLUMNS: tuple[str, ...] = (
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

#: 出力列（先頭 22 列 + 周辺AP判定の 7 列）。追加は必ず末尾に行う。
RESULT_COLUMNS: tuple[str, ...] = (*CORE_RESULT_COLUMNS, *NEIGHBOR_COLUMNS)

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


def _fmt_timedelta(td: pd.Timedelta) -> str:
    total_min = int(round(td.total_seconds() / 60))
    if total_min < 60:
        return f"{total_min}分"
    h, m = divmod(total_min, 60)
    return f"{h}時間{m}分" if m else f"{h}時間"


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


def _prepare_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    """型を揃えた metrics を返す（窓による絞り込みはしない）。

    区間の終了・回復判定・イベント相関・ゼロ直前時刻には、窓の外を含む
    読み込み済みの全サンプルを使う。窓は候補区間の選別（ゼロ開始が窓内か）にだけ使う。
    """
    missing = [c for c in _REQUIRED_METRICS_COLUMNS if c not in metrics.columns]
    if missing:
        raise KeyError(f"metrics に必要な列がありません: {missing}")

    df = metrics.loc[:, list(_REQUIRED_METRICS_COLUMNS)].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df[df["timestamp"].notna()]

    df["num_clients"] = pd.to_numeric(df["num_clients"], errors="coerce")
    df["status"] = df["status"].astype("string").fillna("").str.strip().str.lower()
    df["ap_id"] = df["ap_id"].astype("string").fillna("")
    df["ap_name"] = df["ap_name"].astype("string").fillna("")
    df["site_name"] = df["site_name"].astype("string").fillna("")
    return df.sort_values(["ap_id", "timestamp"], kind="stable").reset_index(drop=True)


def _gap_boundaries(gaps: pd.DataFrame | None) -> dict[str, set[tuple[pd.Timestamp, pd.Timestamp]]]:
    """ap_id ごとの (gap_start, gap_end) 集合。連続 2 サンプルの間の欠測を表す。

    ``missing_samples == 0`` のギャップ（サンプリング間隔のジッタ。1 件も欠けていない）は
    打ち切り対象から除外する。
    """
    out: dict[str, set[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    if gaps is None or len(gaps) == 0:
        return out
    real_gaps = gaps[gaps["missing_samples"] >= 1] if "missing_samples" in gaps.columns else gaps
    for ap_id, start, end in zip(real_gaps["ap_id"], real_gaps["gap_start"], real_gaps["gap_end"]):
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


def _warn_insufficient_coverage(
    prepared: pd.DataFrame,
    events: pd.DataFrame | None,
    window_start: pd.Timestamp | None,
    window_end: pd.Timestamp | None,
    event_window: pd.Timedelta,
) -> None:
    """読み込んだデータが窓に対して足りない場合に警告する（エラーにはしない）。

    History Log は 1 時間単位のため、任意の時間帯を分析するには複数ファイルの結合が
    必要になり、窓の外側のデータが欠けやすい。欠けたまま検出すると、窓の先頭付近の
    区間は検出漏れに、窓の右端付近の区間は「継続中」への誤分類やイベント相関の欠落に
    つながる。
    """
    if prepared.empty:
        return
    data_min = prepared["timestamp"].min()
    data_max = prepared["timestamp"].max()

    if window_start is not None and data_min >= window_start:
        warnings.warn(
            f"読み込んだデータに window_start（{_fmt_ts(window_start)}）より前のサンプルが"
            f"ありません（データ開始: {_fmt_ts(data_min)}）。"
            "窓の先頭付近で始まる区間は直前clientsが取得できず、検出されない可能性があります。",
            stacklevel=3,
        )

    if window_end is None:
        return

    if data_max < window_end:
        deficit = window_end - data_max
        warnings.warn(
            f"読み込んだデータは window_end（{_fmt_ts(window_end)}）に届いていません"
            f"（データ終端: {_fmt_ts(data_max)}、不足 {_fmt_timedelta(deficit)}）。"
            "窓の右端付近の区間が、実際は回復していても「継続中」と誤分類される可能性があります。",
            stacklevel=3,
        )

    if events is not None and len(events) and "event_timestamp" in events.columns:
        event_ts = pd.to_datetime(events["event_timestamp"], errors="coerce")
        events_max = event_ts.max()
        required = window_end + event_window
        if pd.notna(events_max) and events_max < required:
            deficit = required - events_max
            warnings.warn(
                f"読み込んだイベントは window_end + event_window（{_fmt_ts(required)}）に"
                f"届いていません（イベント終端: {_fmt_ts(events_max)}、不足 {_fmt_timedelta(deficit)}）。"
                "窓の右端付近の区間でイベント相関が欠ける可能性があります。",
                stacklevel=3,
            )


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
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    min_zero_samples: int = DEFAULT_MIN_ZERO_SAMPLES,
    min_zero_duration: timedelta | float | str | None = None,
    event_window: timedelta | float | str = DEFAULT_EVENT_WINDOW,
    exodus_threshold: float = DEFAULT_EXODUS_THRESHOLD,
    rf_neighbors: pd.DataFrame | None = None,
    neighbor_context: NeighborContext | None = None,
    neighbor_count: int = DEFAULT_NEIGHBOR_COUNT,
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M,
    neighbor_client_threshold: float = DEFAULT_NEIGHBOR_CLIENT_THRESHOLD,
) -> pd.DataFrame:
    """ゼロクライアント区間を検出して DataFrame（列は :data:`RESULT_COLUMNS`）で返す。

    :param metrics: ローダの ``metrics``（ap_id / ap_name / site_name / timestamp /
        num_clients / status を含むこと）
    :param events: ローダの ``events``。``ap_name`` で突合する。None ならイベント列は空。
    :param gaps: ローダの ``gaps``。**必ず渡すこと。** None だと欠測を跨いだ区間を
        連結してしまい、連続ゼロ回数が過大になる（エラーにならないため気づけない）。
        ``missing_samples == 0`` のギャップ（サンプリング間隔のジッタ）は打ち切り対象にしない。
    :param window_start: 対象とする区間の「ゼロ開始」の下限（含む）。省略可（省略時は制限なし）。
        **サンプル自体は絞り込まない。** 区間の終了・回復判定・直後clients・イベント相関・
        ゼロ直前時刻には、窓の外を含む読み込み済みの全サンプルを使う。
    :param window_end: 対象とする区間の「ゼロ開始」の上限（含まない）。省略可（省略時は制限なし）。
    :param min_zero_samples: 採用する連続ゼロの最小 **サンプル数**（時間ではない）。
        サンプリング間隔は環境で異なる（実測でデモ 30 秒 / 顧客 5 分）ため、
        既定の 5 は 5 分間隔なら 25 分、30 秒間隔ではわずか 2.5 分にしかならない。
    :param min_zero_duration: 採用する連続ゼロの最小 **時間**（timedelta / 秒数 / 文字列）。
        長さは「最初のゼロサンプル → 最後のゼロサンプル」で測る（1 サンプルなら 0）。
        指定された場合は ``min_zero_samples`` より優先する。
    :param event_window: イベント相関の窓（ゼロ終了 ± この時間）。既定 30 分。
    :param exodus_threshold: 退場疑いのしきい値。サイト全体変化率がこれ以下なら True。
    :param rf_neighbors: ローダの ``rf_neighbors``。``周辺AP RF隣接数``（参考列）の算出に
        しか使わない。**周辺AP判定には一切影響しない。** 省略すればその列が空になるだけ。
    :param neighbor_context: :func:`hangap.neighbors.build_context` の戻り値。省略時は
        ``metrics`` から自動で作る（CLI は explain と共有するため自分で作って渡す）。
    :param neighbor_count: 近傍として採用する最大台数（距離が近い順）。
    :param max_distance_m: 近傍として認める最大距離（m）。
    :param neighbor_client_threshold: ``周辺AP端末数合計`` がこれ以上なら「周辺に端末あり」。

    結果からは「継続中」も「退場疑い」も除外しない（進行中のハングを取りこぼさないため）。
    ``周辺AP判定`` も同様で、「周辺も端末なし」の区間を落としたりはしない（絞り込みは利用者側）。

    ``window_start`` / ``window_end`` に対して読み込み済みデータが不足している場合
    （History Log の結合漏れ等）は ``UserWarning`` を出す（エラーにはしない）。
    """
    prepared = _prepare_metrics(metrics)
    ws = pd.Timestamp(window_start) if window_start is not None else None
    we = pd.Timestamp(window_end) if window_end is not None else None
    ev_window = _as_timedelta(event_window)
    _warn_insufficient_coverage(prepared, events, ws, we, ev_window)

    boundaries = _gap_boundaries(gaps)
    totals = _site_totals(prepared)
    events_by_ap = _events_by_ap(events)
    min_duration = _as_timedelta(min_zero_duration) if min_zero_duration is not None else None
    # 座標は元の metrics（map_id / x_m / y_m を含む）から取る。prepared は必須列だけに絞られている。
    ctx = neighbor_context or _neighbors.build_context(
        metrics,
        rf_neighbors,
        neighbor_count=neighbor_count,
        max_distance_m=max_distance_m,
    )

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

        # しきい値の適用（時間指定があればそちらを優先）。判定は全サンプル基準で行う。
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

        # 窓は「ゼロ開始が範囲内か」の選別にのみ使う。区間の解決自体は全サンプルで行う。
        windowed = [
            iv for iv in adopted
            if (ws is None or timestamps[iv["start"]] >= ws)
            and (we is None or timestamps[iv["start"]] < we)
        ]
        if not windowed:
            continue

        # AP最大clients は窓内の最大値（元の手作業の分析仕様どおり）。
        if ws is None and we is None:
            ap_max_clients = grp["num_clients"].max()
        else:
            mask = pd.Series(True, index=grp.index)
            if ws is not None:
                mask &= grp["timestamp"] >= ws
            if we is not None:
                mask &= grp["timestamp"] < we
            windowed_clients = grp.loc[mask, "num_clients"]
            ap_max_clients = windowed_clients.max() if len(windowed_clients) else pd.NA

        for number, iv in enumerate(windowed, start=1):
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
                "AP内区間数": len(windowed),
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
            row.update(
                ctx.columns_for(str(ap_id), zero_start, zero_end, neighbor_client_threshold)
            )
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


def truncated_warning(
    result: pd.DataFrame,
    warn_ratio: float = DEFAULT_TRUNCATED_WARN_RATIO,
) -> str | None:
    """打ち切り(欠測)の比率が高すぎる場合の警告文（該当しなければ None）。

    デモ環境では検出 2341 区間のうち 1861 件（79%）が ``打ち切り(欠測)`` だった。
    ログの収集が断続的だったことを意味し、その結果は分析に耐えない。件数だけを見ていると
    気づけないため、割合を含む警告として明示する。
    """
    if result is None or len(result) == 0 or "回復状況" not in result.columns:
        return None
    total = len(result)
    truncated = int((result["回復状況"] == STATUS_CUT_GAP).sum())
    ratio = truncated / total
    if ratio <= float(warn_ratio):
        return None
    return (
        f"検出区間の {ratio * 100:.1f}%（{truncated}/{total} 件）が"
        "データ欠測により打ち切られています。ログの収集が断続的だった可能性があります。"
        "結果の解釈に注意してください。"
    )


def _to_frame(rows: Iterable[dict]) -> pd.DataFrame:
    """列順・dtype を揃えた DataFrame にする。"""
    df = pd.DataFrame(list(rows), columns=list(RESULT_COLUMNS))
    for col in ("ゼロ直前時刻", "ゼロ開始", "ゼロ終了", "回復時刻"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in (
        "区間番号", "AP内区間数", "直前clients", "直後clients（回復時）",
        "連続ゼロ回数", "AP最大clients",
        "サイト合計clients(ゼロ開始時)", "サイト合計clients(ゼロ終了時)",
        "周辺AP数", "周辺AP RF隣接数",
    ):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ("サイト全体変化率", "周辺AP端末数合計"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["退場疑い"] = df["退場疑い"].fillna(False).astype(bool)
    for col in ("ap_name", "site_name", "回復状況", "AP Event（±30分）",
                "Event時刻", "ゼロ終了との差(分)", "Event種別", "Event詳細",
                "周辺AP名", "周辺AP距離", "周辺AP端末数", "周辺AP判定"):
        df[col] = df[col].astype("string").fillna("")
    if df.empty:
        return df
    return df.sort_values(["ap_name", "区間番号"], kind="stable").reset_index(drop=True)
