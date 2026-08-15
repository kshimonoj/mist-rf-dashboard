"""周辺 AP の判定（**距離ベース**）と、判定根拠の説明（explain）。

ゼロクライアント区間に「周辺の AP に端末がいたか」を添えるためのモジュール。
これが無いと「AP がハングした」と「単に人がいなかった」を区別できない。

.. note::
   **周辺 AP は距離だけで選ぶ。RF 隣接（rf_neighbors）は判定に使わない。**
   実サイト（250AP / 7 マップ）の実測にもとづく判断であり、根拠は次のとおり。

   - RF 隣接の **49.8%** がマップまたぎ（別フロアの別の群衆）
   - RSSI 上位 N と距離上位 N の一致は **46.2%**（N=4）＝両者は別のものを測っている
   - RSSI 上位 N の平均距離 **18.9m** に対し距離上位 N は **11.9m**（RSSI だと 60% 遠い）
   - RF 隣接の双方向観測率は **28.7%**（rf_neighbors 自体が不完全）
   - 座標は 250/250 の AP に揃っている

   判定したいのは「端末がどこへ逃げたか」ではなく「**その場所に人がいたか**」であり、
   物理的な近さのほうが正しい問いに答える。``周辺AP RF隣接数`` は人が見るための
   **参考列**であって、判定には一切影響しない。

.. warning::
   既定値（``neighbor_count=4`` / ``max_distance_m=25`` /
   ``neighbor_client_threshold=1.0``）は **暫定**である。実サイトのデータを見ながら
   調整する前提の値であり、確定値ではない。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 既定値（いずれも暫定。上の warning を参照）
# ---------------------------------------------------------------------------

#: 近傍として採用する最大台数（距離が近い順）
DEFAULT_NEIGHBOR_COUNT: int = 4

#: 近傍として認める最大距離（m）。上限が無いと、AP 密度の低いマップで
#: 「上位N台」が 58m 先まで伸びて別の場所を見てしまう（実測値）。
DEFAULT_MAX_DISTANCE_M: float = 25.0

#: 「周辺に端末あり」と判定する ``周辺AP端末数合計`` のしきい値
DEFAULT_NEIGHBOR_CLIENT_THRESHOLD: float = 1.0

# 周辺AP判定の 3 値
VERDICT_PRESENT: str = "周辺に端末あり"
VERDICT_ABSENT: str = "周辺も端末なし"
VERDICT_UNKNOWN: str = "判定不能"

# 判定不能の理由（explain の表示にだけ使う。列には出さない）
REASON_OK: str = "ok"
REASON_NO_COORDS: str = "no_coords"
REASON_NO_NEIGHBOR: str = "no_neighbor"

#: detector の結果に追加する列（RESULT_COLUMNS の末尾に付く）
NEIGHBOR_COLUMNS: tuple[str, ...] = (
    "周辺AP数",
    "周辺AP名",
    "周辺AP距離",
    "周辺AP端末数",
    "周辺AP端末数合計",
    "周辺AP判定",
    "周辺AP RF隣接数",
)

#: 近傍AP を並べるときの区切り（名前・距離・端末数の 3 列で同じ順序・同じ件数）
NEIGHBOR_SEPARATOR: str = ", "


# ---------------------------------------------------------------------------
# 小さなヘルパ
# ---------------------------------------------------------------------------


def _as_float(value: object) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _fmt1(value: float | None) -> str:
    return "" if value is None else f"{value:.1f}"


# ---------------------------------------------------------------------------
# 近傍AP
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Neighbor:
    """距離で選ばれた近傍 AP 1 台。"""

    ap_id: str
    ap_name: str
    mac: str
    distance_m: float
    #: rf_neighbors にも隣接として現れるか（**参考情報**。判定には使わない）
    in_rf: bool = False


@dataclass
class NeighborContext:
    """AP ごとの近傍集合と、区間中の平均 num_clients を引くためのインデックス。

    :func:`build_context` で作る。AP は動かないので座標は最新行から 1 度だけ取る。
    """

    neighbor_count: int = DEFAULT_NEIGHBOR_COUNT
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M
    #: rf_neighbors を読み込めたか（False なら ``周辺AP RF隣接数`` は空にする）
    has_rf: bool = False
    #: ap_id → 近傍AP（距離の昇順）
    by_ap: dict[str, list[Neighbor]] = field(default_factory=dict)
    #: ap_id → 判定不能の理由（``REASON_*``）
    reasons: dict[str, str] = field(default_factory=dict)
    #: ap_id → (timestamp[int64], num_clients[float]) の昇順配列
    series: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
    #: ap_name → ap_id（explain で名前から引くため。同名は最後の 1 件が残る）
    ids_by_name: dict[str, str] = field(default_factory=dict)

    # -- 参照 ---------------------------------------------------------------

    def neighbors_of(self, ap_id: str) -> list[Neighbor]:
        return self.by_ap.get(str(ap_id), [])

    def reason_of(self, ap_id: str) -> str:
        return self.reasons.get(str(ap_id), REASON_NO_COORDS)

    def reason_of_name(self, ap_name: str) -> str:
        ap_id = self.ids_by_name.get(str(ap_name))
        return self.reason_of(ap_id) if ap_id is not None else REASON_NO_COORDS

    def mean_clients(self, ap_id: str, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
        """区間（``start`` 〜 ``end``、両端を含む）の平均 num_clients。

        区間中にサンプルが 1 件も無い AP は、区間の直前まで持っていた値
        （``end`` 以前の最後の値）で代用する。それも無ければ None。
        """
        found = self.series.get(str(ap_id))
        if found is None:
            return None
        ts, clients = found
        lo = int(np.searchsorted(ts, np.int64(pd.Timestamp(start).value), side="left"))
        hi = int(np.searchsorted(ts, np.int64(pd.Timestamp(end).value), side="right"))
        if hi > lo:
            seg = clients[lo:hi]
            seg = seg[~np.isnan(seg)]
            if seg.size:
                return float(seg.mean())
        head = clients[:hi]
        valid = np.flatnonzero(~np.isnan(head))
        if valid.size:
            return float(head[valid[-1]])
        return None

    # -- 列 -----------------------------------------------------------------

    def columns_for(
        self,
        ap_id: str,
        zero_start: pd.Timestamp,
        zero_end: pd.Timestamp,
        threshold: float = DEFAULT_NEIGHBOR_CLIENT_THRESHOLD,
    ) -> dict[str, object]:
        """1 区間分の周辺AP列を作る。判定不能なら数値列は欠損にする。"""
        empty: dict[str, object] = {
            "周辺AP数": pd.NA,
            "周辺AP名": "",
            "周辺AP距離": "",
            "周辺AP端末数": "",
            "周辺AP端末数合計": pd.NA,
            "周辺AP判定": VERDICT_UNKNOWN,
            "周辺AP RF隣接数": pd.NA,
        }
        found = self.neighbors_of(ap_id)
        if not found:
            return empty

        means = [self.mean_clients(n.ap_id, zero_start, zero_end) for n in found]
        available = [m for m in means if m is not None]
        total = float(sum(available)) if available else None
        verdict = VERDICT_UNKNOWN
        if total is not None:
            verdict = VERDICT_PRESENT if total >= threshold else VERDICT_ABSENT
        return {
            "周辺AP数": len(found),
            "周辺AP名": NEIGHBOR_SEPARATOR.join(n.ap_name for n in found),
            "周辺AP距離": NEIGHBOR_SEPARATOR.join(_fmt1(n.distance_m) for n in found),
            "周辺AP端末数": NEIGHBOR_SEPARATOR.join(_fmt1(m) for m in means),
            "周辺AP端末数合計": pd.NA if total is None else round(total, 1),
            "周辺AP判定": verdict,
            # 参考列。rf_neighbors が無ければ空にする（エラーにはしない）
            "周辺AP RF隣接数": sum(1 for n in found if n.in_rf) if self.has_rf else pd.NA,
        }


# ---------------------------------------------------------------------------
# 構築
# ---------------------------------------------------------------------------


def _latest_coords(metrics: pd.DataFrame) -> dict[str, dict]:
    """ap_id ごとの最新の ap_name / mac / map_id / x_m / y_m。

    最新行の座標が欠けている AP は「座標なし」として扱う（過去の座標では埋めない。
    配置変更を古い値で埋めると距離が実態と食い違うため）。
    ``ap_metrics_v1``（座標列を持たない 33 列版）だけを読み込んだ場合は全 AP が座標なしになる。
    """
    out: dict[str, dict] = {}
    if metrics is None or len(metrics) == 0 or "ap_id" not in metrics.columns:
        return out
    df = metrics.copy()
    df["timestamp"] = pd.to_datetime(df.get("timestamp"), errors="coerce")
    df = df[df["timestamp"].notna()]
    if df.empty:
        return out
    df = df.sort_values("timestamp", kind="stable")
    has_coord_columns = all(c in df.columns for c in ("map_id", "x_m", "y_m"))
    for ap_id, grp in df.groupby("ap_id", sort=False):
        row = grp.iloc[-1]
        map_id = _text(row.get("map_id")) if has_coord_columns else ""
        out[str(ap_id)] = {
            "ap_name": _text(row.get("ap_name")),
            "mac": _text(row.get("mac")),
            "map_id": map_id,
            "x_m": _as_float(row.get("x_m")) if has_coord_columns else None,
            "y_m": _as_float(row.get("y_m")) if has_coord_columns else None,
        }
    return out


def _has_coords(info: dict) -> bool:
    return bool(info["map_id"]) and info["x_m"] is not None and info["y_m"] is not None


def _rf_pairs(rf_neighbors: pd.DataFrame | None) -> set[tuple[str, str]]:
    """rf_neighbors の最新時点から、方向つき隣接ペア (ap_mac, neighbor_mac) を集める。

    バンド・方向は区別しない（参考列の算出にしか使わないため）。
    """
    if rf_neighbors is None or len(rf_neighbors) == 0:
        return set()
    if not {"ap_mac", "neighbor_mac"} <= set(rf_neighbors.columns):
        return set()
    df = rf_neighbors
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], errors="coerce")
        latest = ts.max()
        if pd.notna(latest):
            df = df[ts == latest]
    return {
        (_text(a), _text(b))
        for a, b in zip(df["ap_mac"], df["neighbor_mac"])
        if _text(a) and _text(b)
    }


def _client_series(metrics: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """ap_id ごとの (timestamp[int64], num_clients[float]) を時刻昇順で持つ。"""
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    if metrics is None or len(metrics) == 0:
        return out
    if not {"ap_id", "timestamp", "num_clients"} <= set(metrics.columns):
        return out
    df = pd.DataFrame(
        {
            "ap_id": metrics["ap_id"].astype("string").fillna(""),
            "timestamp": pd.to_datetime(metrics["timestamp"], errors="coerce"),
            "num_clients": pd.to_numeric(metrics["num_clients"], errors="coerce"),
        }
    )
    df = df[df["timestamp"].notna()].sort_values("timestamp", kind="stable")
    for ap_id, grp in df.groupby("ap_id", sort=False):
        out[str(ap_id)] = (
            grp["timestamp"].to_numpy(dtype="datetime64[ns]").astype("int64"),
            grp["num_clients"].to_numpy(dtype="float64", na_value=np.nan),
        )
    return out


def build_context(
    metrics: pd.DataFrame,
    rf_neighbors: pd.DataFrame | None = None,
    *,
    neighbor_count: int = DEFAULT_NEIGHBOR_COUNT,
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M,
) -> NeighborContext:
    """``ap_metrics`` から近傍AP のインデックスを作る。

    近傍の定義:

    - AP X と **同じ map_id** に属し、
    - X からの距離（x_m / y_m のユークリッド距離）が近い順に上位 ``neighbor_count`` 台、
    - かつ 距離 <= ``max_distance_m``

    別マップの AP は近傍にしない（座標系が違い距離を定義できない）。上限内に 1 台も
    無ければ「判定不能」であり、**0 台と混同しない**。

    :param metrics: ローダの ``metrics``（``map_id`` / ``x_m`` / ``y_m`` を含むこと。
        座標列を持たない ``ap_metrics_v1`` だけの場合は全 AP が判定不能になる）
    :param rf_neighbors: ローダの ``rf_neighbors``。**参考列の算出にしか使わない。**
        None / 空でもエラーにはせず、``周辺AP RF隣接数`` を空にするだけ。
    """
    count = max(int(neighbor_count), 0)
    limit = float(max_distance_m)

    coords = _latest_coords(metrics)
    rf = _rf_pairs(rf_neighbors)
    ctx = NeighborContext(
        neighbor_count=count,
        max_distance_m=limit,
        has_rf=bool(rf),
        series=_client_series(metrics),
        ids_by_name={info["ap_name"]: ap_id for ap_id, info in coords.items() if info["ap_name"]},
    )

    # map_id ごとに座標を持つ AP を集める（別マップとは距離を出さない）
    by_map: dict[str, list[str]] = {}
    for ap_id, info in coords.items():
        if _has_coords(info):
            by_map.setdefault(info["map_id"], []).append(ap_id)

    for ap_id, info in coords.items():
        if not _has_coords(info):
            ctx.reasons[ap_id] = REASON_NO_COORDS
            continue
        pairs: list[tuple[float, str, str]] = []
        for other in by_map.get(info["map_id"], ()):
            if other == ap_id:
                continue
            oinfo = coords[other]
            distance = math.hypot(info["x_m"] - oinfo["x_m"], info["y_m"] - oinfo["y_m"])
            if distance <= limit:
                # 同距離の並びを安定させるため、名前 → ap_id の順で決める
                pairs.append((distance, oinfo["ap_name"], other))
        pairs.sort(key=lambda t: (t[0], t[1], t[2]))

        chosen = [
            Neighbor(
                ap_id=other,
                ap_name=coords[other]["ap_name"],
                mac=coords[other]["mac"],
                distance_m=distance,
                in_rf=(
                    (info["mac"], coords[other]["mac"]) in rf
                    or (coords[other]["mac"], info["mac"]) in rf
                ),
            )
            for distance, _, other in pairs[:count]
        ]
        ctx.by_ap[ap_id] = chosen
        ctx.reasons[ap_id] = REASON_OK if chosen else REASON_NO_NEIGHBOR
    return ctx


# ---------------------------------------------------------------------------
# explain（判定根拠の表示）
# ---------------------------------------------------------------------------


def _fmt_ts(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_int(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return str(int(value))


def _fmt_ratio(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.1f}%"


def _split(value: object) -> list[str]:
    text = _text(value)
    if not text:
        return []
    return [part.strip() for part in text.split(NEIGHBOR_SEPARATOR)]


def _explain_neighbors(row: pd.Series, context: NeighborContext | None) -> list[str]:
    limit = context.max_distance_m if context is not None else DEFAULT_MAX_DISTANCE_M
    count = context.neighbor_count if context is not None else DEFAULT_NEIGHBOR_COUNT

    names = _split(row.get("周辺AP名"))
    if not names:
        reason = (
            context.reason_of_name(_text(row.get("ap_name")))
            if context is not None
            else REASON_NO_COORDS
        )
        if reason == REASON_NO_NEIGHBOR:
            why = f"上限 {limit:.1f}m 以内に AP なし"
        else:
            why = "座標（map_id / x_m / y_m）が無く距離を計算できない"
        return [f"  近傍AP: {VERDICT_UNKNOWN}（{why}）"]

    distances = _split(row.get("周辺AP距離"))
    clients = _split(row.get("周辺AP端末数"))
    width = max(len(n) for n in names)
    lines = [f"  近傍AP（距離 <= {limit:.1f}m / 上位 {count} 台）:"]
    for i, name in enumerate(names):
        distance = distances[i] if i < len(distances) else ""
        mean = clients[i] if i < len(clients) else ""
        lines.append(
            f"    {name:<{width}}   距離 {distance:>6}m   "
            f"区間中の平均clients {mean if mean else '-':>6}"
        )

    total = row.get("周辺AP端末数合計")
    total_text = "-" if total is None or pd.isna(total) else f"{float(total):.1f}"
    lines.append(f"  周辺AP端末数合計: {total_text}  → {_text(row.get('周辺AP判定'))}")

    rf_count = row.get("周辺AP RF隣接数")
    if rf_count is not None and not pd.isna(rf_count):
        lines.append(f"  （参考）このうち RF 隣接に現れるもの: {int(rf_count)} 台 ※判定には使わない")
    return lines


def _explain_events(row: pd.Series) -> list[str]:
    times = [t.strip() for t in _text(row.get("Event時刻")).split(" | ") if t.strip()]
    if not times:
        return ["  イベント（±30分）: なし"]
    kinds = [t.strip() for t in _text(row.get("Event種別")).split(" | ")]
    deltas = [t.strip() for t in _text(row.get("ゼロ終了との差(分)")).split(" | ")]
    details = [t.strip() for t in _text(row.get("Event詳細")).split(" | ")]
    lines = ["  イベント（±30分）:"]
    for i, ts in enumerate(times):
        kind = kinds[i] if i < len(kinds) else ""
        delta = deltas[i] if i < len(deltas) else ""
        detail = details[i] if i < len(details) else ""
        tail = f"  {detail}" if detail else ""
        lines.append(f"    {ts}  {kind}  (ゼロ終了との差 {delta}分){tail}")
    return lines


def render_explain(
    result: pd.DataFrame,
    ap_names: list[str] | tuple[str, ...],
    context: NeighborContext | None = None,
) -> str:
    """指定した AP の各区間について判定根拠を組み立てる。

    しきい値が実データで妥当かを人が確かめるための表示であり、結果を絞り込むものではない。
    該当する AP が結果に無い場合もエラーにはせず、その旨を出す。
    """
    blocks: list[str] = []
    for ap_name in ap_names:
        name = str(ap_name).strip()
        blocks.append(f"[ 判定根拠: {name} ]")
        if result is None or len(result) == 0 or "ap_name" not in result.columns:
            blocks.append(f"  該当する区間がありません（{name} は検出結果に含まれていません）")
            blocks.append("")
            continue
        hits = result[result["ap_name"].astype("string").fillna("") == name]
        if hits.empty:
            blocks.append(f"  該当する区間がありません（{name} は検出結果に含まれていません）")
            blocks.append("")
            continue

        for _, row in hits.iterrows():
            blocks.append(
                f"区間 #{_fmt_int(row.get('区間番号'))}"
                f"  ゼロ開始 {_fmt_ts(row.get('ゼロ開始'))}"
                f"  ゼロ終了 {_fmt_ts(row.get('ゼロ終了'))}"
                f"  連続ゼロ {_fmt_int(row.get('連続ゼロ回数'))}回"
                f"  回復状況 {_text(row.get('回復状況'))}"
            )
            blocks.append(
                f"  直前clients {_fmt_int(row.get('直前clients'))}"
                f" → 直後clients {_fmt_int(row.get('直後clients（回復時）'))}"
                f"   AP最大clients {_fmt_int(row.get('AP最大clients'))}"
            )
            blocks.extend(_explain_neighbors(row, context))
            blocks.append(
                f"  サイト全体: {_fmt_int(row.get('サイト合計clients(ゼロ開始時)'))}"
                f" → {_fmt_int(row.get('サイト合計clients(ゼロ終了時)'))}"
                f"（変化率 {_fmt_ratio(row.get('サイト全体変化率'))}）"
                f"  退場疑い: {bool(row.get('退場疑い'))}"
            )
            blocks.extend(_explain_events(row))
            blocks.append("")
    return "\n".join(blocks).rstrip("\n")
