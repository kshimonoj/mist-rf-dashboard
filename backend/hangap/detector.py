"""ゼロクライアント区間（ハングAP 候補）の検出エンジン。

入力は :func:`hangap.loader.load` の戻り値（``metrics`` / ``events`` / ``gaps``）。
本モジュールは純粋なデータ処理のみを行う（ネットワークアクセス・LLM 呼び出しはしない）。

設計方針:
- 手作業で行っていた分析（先頭 18 列）を機械的に再現できることを最優先とする。
- 「連続ゼロ」を数えるうえで最も危険なのは **欠測（ギャップ）を跨いで連結してしまう** こと。
  エラーにならず連続ゼロ回数だけが過大になるため、ローダの ``gaps`` を使って必ず打ち切る。
  ただし ``missing_samples == 0`` のギャップ（サンプリング間隔のジッタ。実測で 300 秒間隔に
  対し 506 秒。データは 1 件も欠けていない）は打ち切り対象にしない。
- ``window_start`` / ``window_end`` を指定したら、**分析に使うサンプルをその範囲に限定する**。
  区間の終了・回復判定・直後clients・ゼロ直前時刻・``AP最大clients``・サイト全体トレンドの
  すべてで、窓の外のサンプルは一切参照しない。窓の外を見に行く上限が無いと、区間が指定期間の
  外へ無制限に伸びる（実測で 6 時間の窓を指定して 6 日先まで伸びた）。
  **例外はイベント相関だけ** で、イベントは ``ゼロ終了 ± event_window`` で相関を取るため
  窓の外のものも参照する（``event_window`` で上限が決まるので無制限には伸びない）。
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

#: ログ保存間隔（既定）。History Log は毎正時保存のため、window_end を「現在時刻」に
#: 設定すると必ずこの分だけデータ終端が window_end に届かない。この分の不足は
#: 「取りこぼし」ではなく仕様どおりの遅延なので、カバレッジ不足警告のしきい値に使う。
DEFAULT_LOG_SAVE_INTERVAL: timedelta = timedelta(minutes=60)

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
    """型を揃えた metrics を返す（窓による絞り込みは :func:`_apply_window` で別に行う）。

    カバレッジ警告は「窓に対してデータが足りているか」を見るものなので、
    絞り込む **前** のこの DataFrame を対象にする。
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


def _apply_window(
    prepared: pd.DataFrame,
    window_start: pd.Timestamp | None,
    window_end: pd.Timestamp | None,
) -> pd.DataFrame:
    """分析対象のサンプルを窓 ``[window_start, window_end)`` に限定する。

    窓を指定していない側は制限しない（両方省略なら読み込んだ全サンプルが対象）。

    連続するサンプルの組（ギャップ判定に使う）は窓の内側でもそのまま保たれるため、
    :func:`_scan_ap` のアルゴリズムはこの絞り込みの影響を受けない。窓の先頭の
    サンプルだけは「直前サンプル」を失うので、そこから始まる区間は検出されなくなる
    （仕様どおりの帰結。窓の外を見に行かないことと引き換えである）。
    """
    if window_start is None and window_end is None:
        return prepared
    mask = pd.Series(True, index=prepared.index)
    if window_start is not None:
        mask &= prepared["timestamp"] >= window_start
    if window_end is not None:
        mask &= prepared["timestamp"] < window_end
    return prepared[mask].reset_index(drop=True)


def _sampling_interval(prepared: pd.DataFrame) -> pd.Timedelta | None:
    """サンプリング間隔の代表値（AP ごとの中央値の中央値）。推定できなければ None。

    window_start 側のカバレッジ警告のしきい値に使う。ログの 1 サンプル目が窓の開始から
    数秒ずれているだけ（毎正時保存のログを窓ぴったりに指定すれば普通に起きる）で警告を
    出すと毎回鳴る警告になり、本当にデータが欠けているときの警告まで読み飛ばされる。
    """
    if prepared.empty:
        return None
    per_ap = [
        grp["timestamp"].diff().dropna().median()
        for _, grp in prepared.groupby("ap_id", sort=False)
        if len(grp) >= 2
    ]
    if not per_ap:
        return None
    return pd.Series(per_ap).median()


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
    log_save_interval: pd.Timedelta,
) -> None:
    """読み込んだデータが窓に対して足りない場合に警告する（エラーにはしない）。

    History Log は 1 時間単位のため、任意の時間帯を分析するには複数ファイルの結合が
    必要になり、窓の外側のデータが欠けやすい。欠けたまま検出すると、窓の右端付近の
    区間は「継続中」への誤分類やイベント相関の欠落につながる。

    **「窓の先頭で始まる区間が検出されない」ことは警告しない。** 窓を指定したら窓の外の
    サンプルは一切見ないので、それは常に真であり、毎回鳴る警告になる。ここで警告するのは
    「指定した期間の一部にそもそもデータが無い」という別の問題（データ開始が window_start
    より後）だけである。窓の先頭の件は分析条件の説明として出す（:func:`analysis.condition_text`）。

    window_end 側（データ終端 / イベント終端）は、不足が仕様どおりの遅延の範囲内なら警告しない。
    ログ保存は毎正時のため、window_end を現在時刻にすると必ず最大 1 保存間隔分のずれが生じる。
    これは取りこぼしではなく、毎回鳴る警告は読み飛ばされる（本当に取りこぼしがあるときの
    警告まで無視されるようになる）。

    - データ終端側のしきい値は ``log_save_interval`` そのもの。
    - イベント終端側は要求ライン自体が ``window_end + event_window`` なので、
      ``log_save_interval`` に加えて ``event_window`` 分もしきい値に上乗せする
      （そうしないと、収集は追いついているのに event_window の分だけ余分に警告が出る）。

    .. note::
       イベント側の警告は原理的に近似でしかない。「最後のイベントの時刻」を「収集が
       どこまで進んだか」の代理指標にしているが、イベントは疎（実環境で 11 日間に 55 件）
       なので、収集が健全でも最後のイベントが数時間前ということは普通に起こる。しきい値の
       調整で誤検知の頻度は下げられるが、「イベントが無かった」と「収集が止まった」を
       区別できるようにはならない。この警告は「イベント相関が薄いかもしれない」程度の
       参考情報として扱うこと。

    window_start 側は「指定した期間の一部にデータが無い」という別種の情報のため、
    ``log_save_interval`` の対象にしない（しきい値はサンプリング間隔）。
    """
    if prepared.empty:
        return
    data_min = prepared["timestamp"].min()
    data_max = prepared["timestamp"].max()

    if window_start is not None and data_min > window_start:
        deficit = data_min - window_start
        interval = _sampling_interval(prepared)
        # 1 サンプル分にも満たないずれ（毎正時保存のログを窓ぴったりに指定した場合など）は
        # データの欠けではないので警告しない。
        if interval is None or deficit >= interval:
            warnings.warn(
                f"読み込んだデータは window_start（{_fmt_ts(window_start)}）より後から"
                f"始まっています（データ開始: {_fmt_ts(data_min)}、不足 {_fmt_timedelta(deficit)}）。"
                "指定した期間の先頭部分には分析対象のサンプルがありません。",
                stacklevel=3,
            )

    if window_end is None:
        return

    if data_max < window_end:
        deficit = window_end - data_max
        if deficit >= log_save_interval:
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
        # 要求ライン自体が window_end + event_window なので、しきい値にも event_window を
        # 上乗せする（データ終端側の log_save_interval だけだと event_window 分だけ過検知する）。
        event_threshold = log_save_interval + event_window
        if pd.notna(events_max) and events_max < required and required - events_max >= event_threshold:
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

    区間の開始条件は **1 つだけ**:
      - 直前サンプルの num_clients >= 1、当該サンプルが 0（本来の 1→0 の遷移）

    打ち切り（欠測 / AP停止）の向こう側でゼロが続いていても、そこから新しい区間は
    開始しない。「最初から接続端末がゼロなら対象外」という要件は欠測の前後で変わらず、
    ギャップの向こう側の最初のサンプルは「直前サンプルが >= 1」を満たさないためである。
    こうしないと、ログ収集が断続的な環境で「ずっとゼロなだけの AP」が、ギャップの数だけ
    区間として量産されてしまう（実測でデモ環境の検出 2345 区間のうち 1866 件がこれだった）。
    欠測をまたいで続くハング自体は、ギャップ手前の ``打ち切り(欠測)`` 区間として残る。

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

    i = 1
    while i < n:
        if not (statuses[i] == CONNECTED and is_zero(i) and has_clients(i - 1)):
            i += 1
            continue

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
    log_save_interval: timedelta | float | str = DEFAULT_LOG_SAVE_INTERVAL,
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
    :param window_start: 分析対象とする期間の下限（含む）。省略可（省略時は制限なし）。
        **サンプル自体をこの範囲に絞り込む。** 区間の終了・回復判定・直後clients・
        ゼロ直前時刻・``AP最大clients``・サイト全体トレンドで、窓の外のサンプルは参照しない。
        その帰結として、``window_start`` の直後にゼロへ落ちる区間は（直前サンプルが窓の外に
        なるため）検出されない。イベント相関だけは例外で、窓の外のイベントも参照する。
    :param window_end: 分析対象とする期間の上限（含まない）。省略可（省略時は制限なし）。
        この時点でまだゼロが続いている区間は ``継続中`` になる。
    :param min_zero_samples: 採用する連続ゼロの最小 **サンプル数**（時間ではない）。
        サンプリング間隔は環境で異なる（実測でデモ 30 秒 / 顧客 5 分）ため、
        既定の 5 は 5 分間隔なら 25 分、30 秒間隔ではわずか 2.5 分にしかならない。
    :param min_zero_duration: 採用する連続ゼロの最小 **時間**（timedelta / 秒数 / 文字列）。
        長さは「最初のゼロサンプル → 最後のゼロサンプル」で測る（1 サンプルなら 0）。
        指定された場合は ``min_zero_samples`` より優先する。
    :param event_window: イベント相関の窓（ゼロ終了 ± この時間）。既定 30 分。
    :param log_save_interval: ログ保存間隔（timedelta / 秒数 / 文字列）。既定 60 分。
        window_end 側のカバレッジ不足警告のしきい値に使う。ログ保存は毎正時のため、
        window_end を現在時刻にすると必ずこの分だけデータ終端が届かない
        （仕様どおりの遅延であり取りこぼしではない）。実際のログ保存間隔が既定と異なる
        環境では呼び出し側から実際の値を渡すこと。イベント側のしきい値は
        ``log_save_interval + event_window``（要求ライン自体が window_end + event_window
        のため）。
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
    ただし不足が仕様どおりの範囲内なら警告しない（window_start 側はサンプリング間隔未満、
    データ終端は ``log_save_interval`` 未満、イベント終端は
    ``log_save_interval + event_window`` 未満）。窓の先頭で始まる区間が検出されないことは
    仕様であり、警告しない。
    """
    prepared = _prepare_metrics(metrics)
    ws = pd.Timestamp(window_start) if window_start is not None else None
    we = pd.Timestamp(window_end) if window_end is not None else None
    ev_window = _as_timedelta(event_window)
    log_interval = _as_timedelta(log_save_interval)
    # 警告は「窓に対してデータが足りているか」を見るので、絞り込む前のデータで判定する
    _warn_insufficient_coverage(prepared, events, ws, we, ev_window, log_interval)
    prepared = _apply_window(prepared, ws, we)

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

        # grp は既に窓で絞り込まれているので、これが窓内の最大値になる
        # （元の手作業の分析仕様どおり）。
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
        "周辺AP数", "周辺AP RF隣接数", "周辺AP実測なし数",
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
