"""ピーク時点（サイト全体の接続端末数が最大になるバケット）の選定。

**なぜバケット化するか**

AP 間のタイムスタンプは実測でほぼ揃っているが数秒のジッタがある。生の
タイムスタンプで ``groupby().sum()`` すると「たまたま多くの AP が同一秒に並んだ
サンプル」の合計が最大になり、真のピークを外す。1 サンプリング周期を 1 バケットに
まとめてから合計することで、この取りこぼしを消す。

**バケットの区切り方と、その限界**

区切りは epoch 基準（``00:00:00`` から幅ごと）に揃える。収集は毎正時・毎 5 分に
走るので、実データのサンプルは境界の直後に固まる。ただし 1 回の収集が境界を
またいだ場合（例: 幅 300 秒で 09:59:58 と 10:00:02 に分かれた）、その回の AP は
2 つのバケットに割れる。割れたことは :data:`PARTIAL_BUCKET_RATIO` の警告と
``sample_timestamp_min/max`` から読み取れる（黙って隠さない）。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

#: ピークの選び方
SELECTED_AUTO = "auto"
SELECTED_MANUAL = "manual"

#: 手動指定の時刻とバケットのずれがこの倍率 × バケット幅を超えたら警告する
MANUAL_OFFSET_WARN_FACTOR: float = 3.0

#: 選んだバケットの AP 数が「全期間で見えた AP 数 × この比率」を下回ったら警告する。
#: バケットの境界をサンプリング周期がまたいだ場合と、収集が部分的に欠けた場合の
#: どちらもここに出る。**この比率は暫定値**（実データを見ながら調整する前提）。
PARTIAL_BUCKET_RATIO: float = 0.8


@dataclass
class PeakResult:
    """選ばれたピーク時点。"""

    #: バケットの代表時刻（バケットの左端）。「いつのピークか」として表示する値
    peak_bucket: pd.Timestamp
    #: そのバケットのサイト合計端末数
    peak_total_clients: int
    #: バケットに含まれる **実タイムスタンプ** の範囲（代表時刻とのずれを見るため）
    sample_timestamp_min: pd.Timestamp
    sample_timestamp_max: pd.Timestamp
    bucket_seconds: float
    #: ``"auto"``（最大を自動選択） / ``"manual"``（時点を指定）
    selected_by: str
    #: 手動指定時のみ。指定時刻とバケットの実サンプル範囲とのずれ（秒）
    manual_offset_seconds: float | None = None
    #: バケット内の AP 行（AP ごとに最も遅い 1 行だけ）
    ap_rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    #: バケットの数（分析条件の説明用）
    bucket_count: int = 0
    warnings: list[str] = field(default_factory=list)


def _bucket_width(bucket_seconds: float) -> int:
    """バケット幅を 1 秒以上の整数にする（epoch 秒での切り下げに使う）。"""
    return max(1, int(round(float(bucket_seconds))))


def bucket_index(timestamps: pd.Series, bucket_seconds: float) -> pd.Series:
    """タイムスタンプをバケットの左端へ切り下げる。

    ``astype("int64")`` で epoch を取り出すと、datetime64 の分解能（ns / us / s）に
    よって単位が変わり、1000 倍ずれた時刻を掴む。分解能に依存しない
    :meth:`pandas.Series.dt.floor` を使う。
    """
    width = _bucket_width(bucket_seconds)
    return timestamps.dt.floor(pd.Timedelta(seconds=width))


def _ap_key(metrics: pd.DataFrame) -> pd.Series:
    """AP の同一性。``ap_id`` を主に使い、空なら ap_name → mac の順で代替する。"""
    key = metrics.get("ap_id", pd.Series("", index=metrics.index)).astype("string").fillna("")
    for column in ("ap_name", "mac"):
        if column not in metrics.columns:
            continue
        alt = metrics[column].astype("string").fillna("")
        key = key.where(key.str.strip() != "", alt)
    return key.fillna("").astype(str)


def _numeric_clients(metrics: pd.DataFrame) -> pd.Series:
    """``num_clients`` を数値にする。

    ``status`` が down の AP は num_clients が 0 か欠損なので、そのまま 0 として
    合計に寄与しない。**特別扱いしない**（down を除外すると「ピーク時点に何台の AP が
    生きていたか」が結果から読めなくなる）。
    """
    return pd.to_numeric(metrics["num_clients"], errors="coerce").fillna(0.0)


def find_peak(
    metrics: pd.DataFrame,
    bucket_seconds: float,
    *,
    at: pd.Timestamp | None = None,
) -> PeakResult:
    """ピーク時点のバケットを選ぶ。

    :param metrics: サイト・期間で絞り込み済みの ap_metrics
    :param bucket_seconds: バケット幅（秒）。通常はローダの推定サンプリング間隔
    :param at: 指定するとその時刻に **最も近いバケット** を選ぶ（``selected_by="manual"``）

    - 合計が同点のバケットが複数あるときは **最も早いバケット** を選ぶ。
    - 同一バケット内に同じ AP の行が複数あるときは **最も遅い行** を採る。
    """
    if metrics.empty:
        raise ValueError("metrics が空です（ピークを選べません）")

    df = metrics[metrics["timestamp"].notna()].copy()
    if df.empty:
        raise ValueError("timestamp を解釈できる行がありません（ピークを選べません）")

    df["_bucket"] = bucket_index(df["timestamp"], bucket_seconds)
    df["_ap_key"] = _ap_key(df)
    df["_clients"] = _numeric_clients(df)

    # 同じバケット内に同じ AP の行が複数あれば最も遅い行を採る
    df = df.sort_values(["_bucket", "_ap_key", "timestamp"], kind="stable")
    df = df[~df.duplicated(subset=["_bucket", "_ap_key"], keep="last")]

    totals = df.groupby("_bucket", sort=True)["_clients"].sum()
    buckets = list(totals.index)

    warnings: list[str] = []
    manual_offset: float | None = None

    if at is None:
        selected_by = SELECTED_AUTO
        peak_value = totals.max()
        # 同点は最も早いバケット（totals は index 昇順なので先頭が最古）
        chosen = totals[totals == peak_value].index[0]
    else:
        selected_by = SELECTED_MANUAL
        ranges = df.groupby("_bucket", sort=True)["timestamp"].agg(["min", "max"])
        # 指定時刻とバケットの **実サンプル範囲** との距離。範囲の中なら 0
        offsets = [
            _distance_to_range(at, ranges.loc[b, "min"], ranges.loc[b, "max"])
            for b in buckets
        ]
        best = min(range(len(buckets)), key=lambda i: (offsets[i], buckets[i]))
        chosen = buckets[best]
        manual_offset = float(offsets[best])
        limit = float(bucket_seconds) * MANUAL_OFFSET_WARN_FACTOR
        if manual_offset > limit:
            warnings.append(
                f"指定した時点 {_fmt(at)} に対して、最も近いサンプルは "
                f"{_fmt(ranges.loc[chosen, 'min'])} 〜 {_fmt(ranges.loc[chosen, 'max'])} で、"
                f"{manual_offset:.0f} 秒ずれています"
                f"（バケット幅 {float(bucket_seconds):g} 秒 × {MANUAL_OFFSET_WARN_FACTOR:g} = {limit:.0f} 秒 超）。"
                "指定した時点のデータは存在しません"
            )

    rows = df[df["_bucket"] == chosen].copy()
    total = int(round(float(totals.loc[chosen])))

    ap_total = int(df["_ap_key"].nunique())
    if ap_total and len(rows) < ap_total * PARTIAL_BUCKET_RATIO:
        warnings.append(
            f"選ばれた時点 {_fmt(pd.Timestamp(chosen))} に記録があるのは {len(rows)} 台で、"
            f"期間中に現れた {ap_total} 台を下回っています"
            f"（{PARTIAL_BUCKET_RATIO:.0%} 未満）。収集が欠けているか、"
            "サンプリング周期がバケットの境界をまたいだ可能性があります"
        )
    return PeakResult(
        peak_bucket=pd.Timestamp(chosen),
        peak_total_clients=total,
        sample_timestamp_min=pd.Timestamp(rows["timestamp"].min()),
        sample_timestamp_max=pd.Timestamp(rows["timestamp"].max()),
        bucket_seconds=float(bucket_seconds),
        selected_by=selected_by,
        manual_offset_seconds=manual_offset,
        ap_rows=rows.drop(columns=["_bucket", "_ap_key", "_clients"]).reset_index(drop=True),
        bucket_count=len(buckets),
        warnings=warnings,
    )


def _distance_to_range(at: pd.Timestamp, low: pd.Timestamp, high: pd.Timestamp) -> float:
    """``at`` と区間 ``[low, high]`` の距離（秒）。区間の中なら 0。"""
    if low <= at <= high:
        return 0.0
    if at < low:
        return float((low - at).total_seconds())
    return float((at - high).total_seconds())


def _fmt(ts: object) -> str:
    if ts is None or pd.isna(ts):
        return "-"
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
