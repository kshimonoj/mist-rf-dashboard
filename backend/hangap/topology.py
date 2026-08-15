"""RF 隣接（RRM neighbors）と地図上の距離隣接のズレを実測する診断。

目的は **計測** であり、周辺 AP 判定そのもの（積集合・上位 N 台の採用・
周辺 AP 列の追加）はここでは行わない。それは別タスクの範囲である。

AP 同士が電波で聞こえ合う範囲は、端末が代わりに接続できる範囲よりずっと広い
（AP は高所・高感度・高出力、端末は地上高・小型アンテナ・低出力）。
そのズレがどれだけあるかを、推測ではなく手元のログから数値で出す。

ネットワークアクセス・LLM 呼び出しは行わない。入力はローカルの CSV のみ。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

import pandas as pd

from .loader import latest_rf_neighbors

#: 既定で評価する「上位 N 台」
DEFAULT_TOP_N: tuple[int, ...] = (4, 6, 10)

#: 既定のバンド（5GHz。判断は分析側で行うため収集側では絞っていない）
DEFAULT_BAND: str = "5"

#: これ未満の AP 数では RF 隣接の広がりを評価できない（全 AP が互いに隣接する完全グラフになる）
MIN_AP_COUNT_FOR_DIAGNOSIS: int = 20


# ---------------------------------------------------------------------------
# 小さな統計ヘルパ（pandas/numpy の欠損挙動に依存しないよう素の Python で書く）
# ---------------------------------------------------------------------------


def _quantile(values: Sequence[float], q: float) -> float | None:
    """線形補間の分位点。空なら None。"""
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _median(values: Sequence[float]) -> float | None:
    return _quantile(values, 0.5)


def _mean(values: Sequence[float]) -> float | None:
    xs = [float(v) for v in values]
    return sum(xs) / len(xs) if xs else None


def _max(values: Sequence[float]) -> float | None:
    xs = [float(v) for v in values]
    return max(xs) if xs else None


def _min(values: Sequence[float]) -> float | None:
    xs = [float(v) for v in values]
    return min(xs) if xs else None


def _fmt(value: float | None, unit: str = "", digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}{unit}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return pd.Timestamp(dt).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 集計の器
# ---------------------------------------------------------------------------


@dataclass
class TopNStats:
    """「上位 N 台」に絞ったときの距離・重なり・一致率。"""

    n: int
    #: 算出に使えた AP 数（座標があり、同一マップに他 AP がいる AP）
    aps: int = 0
    #: 距離上位 N 台までの距離（AP × 順位 のすべてを母集団とする）
    dist_median_m: float | None = None
    dist_max_m: float | None = None
    #: 距離上位 N 台のうち RF 隣接にも含まれる割合（AP ごとの割合の平均）
    dist_in_rf: float | None = None
    #: RF 隣接のうち距離上位 N 台に含まれる割合（AP ごとの割合の平均）
    rf_in_dist: float | None = None
    #: RSSI 上位 N 台と距離上位 N 台の一致率（AP ごとの割合の平均）
    rssi_top_match: float | None = None
    #: RSSI 上位 N 台までの平均距離（AP ごとの平均の平均）
    rssi_top_mean_dist_m: float | None = None
    #: 距離上位 N 台までの平均距離（比較用）
    dist_top_mean_dist_m: float | None = None


@dataclass
class SiteTopology:
    """1 サイト・1 バンドの診断結果。"""

    site_id: str
    site_name: str
    band: str
    #: ap_metrics に現れた AP 数（サイト内 AP 数）
    site_ap_count: int = 0
    #: rf_neighbors に観測側として現れた AP 数
    observer_count: int = 0

    # 1. RF 隣接の広さ
    neighbors_median: float | None = None
    neighbors_mean: float | None = None
    neighbors_max: float | None = None
    neighbors_min: float | None = None
    density_ratio: float | None = None
    rssi_min: float | None = None
    rssi_q1: float | None = None
    rssi_median: float | None = None
    rssi_q3: float | None = None
    rssi_max: float | None = None

    # 2〜4. 距離隣接・重なり・一致率
    top_n: list[TopNStats] = field(default_factory=list)

    # 5. 非対称性
    pair_count: int = 0
    bidirectional_ratio: float | None = None
    direction_diff_median_db: float | None = None
    direction_diff_max_db: float | None = None

    # 6. データ品質
    unknown_neighbor_macs: int = 0
    unknown_neighbor_rows: int = 0
    aps_with_coords: int = 0
    aps_without_coords: int = 0

    @property
    def is_small_sample(self) -> bool:
        return self.site_ap_count < MIN_AP_COUNT_FOR_DIAGNOSIS


@dataclass
class TopologyResult:
    """topology-report の結果一式。"""

    band: str
    top_n: tuple[int, ...]
    used_timestamp: datetime | None = None
    snapshots: list[tuple[datetime, int]] = field(default_factory=list)
    sites: list[SiteTopology] = field(default_factory=list)
    detail: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_small_sample(self) -> bool:
        return any(s.is_small_sample for s in self.sites)

    def render(self) -> str:
        lines: list[str] = []
        add = lines.append

        add("=" * 68)
        add("RF 隣接 × 距離隣接 比較レポート（topology-report）")
        add("=" * 68)
        add(f"バンド: {self.band}  上位N: {', '.join(str(n) for n in self.top_n)}")
        add(f"使用した rf_neighbors の取得時刻: {_fmt_dt(self.used_timestamp)}")
        if len(self.snapshots) > 1:
            add(f"  （読み込んだ取得時刻は {len(self.snapshots)} 時点。最新のみを使用）")
            for ts, n in self.snapshots:
                mark = " ← 使用" if ts == self.used_timestamp else ""
                add(f"    {_fmt_dt(ts)}  rows={n}{mark}")

        if self.has_small_sample:
            add("")
            add("!! サンプル数が少なく、RF隣接の広がりを評価できません "
                f"（AP 数 {MIN_AP_COUNT_FOR_DIAGNOSIS} 未満のサイトがあります）。"
                "少数 AP の環境では全 AP が互いに隣接する完全グラフになるため、"
                "以下の値は実装の動作確認にのみ使えます")

        if not self.sites:
            add("")
            add("[ 対象サイトなし ] 指定バンドの rf_neighbors が読み込まれていません")

        for s in self.sites:
            add("")
            add("-" * 68)
            add(f"[ サイト ] {s.site_name or '(名前なし)'}  site_id={s.site_id}")
            add(f"  サイト内 AP 数（ap_metrics）: {s.site_ap_count}"
                f"{'  ※20未満のため評価不能' if s.is_small_sample else ''}")
            add(f"  RF 隣接の観測側 AP 数: {s.observer_count}")

            add("")
            add("  1. RF 隣接の広さ")
            add(f"     AP あたり隣接数  中央値={_fmt(s.neighbors_median)}  "
                f"平均={_fmt(s.neighbors_mean)}  最大={_fmt(s.neighbors_max, digits=0)}  "
                f"最小={_fmt(s.neighbors_min, digits=0)}")
            add(f"     RSSI 分布  最小={_fmt(s.rssi_min, 'dBm', 1)}  "
                f"Q1={_fmt(s.rssi_q1, 'dBm', 1)}  中央値={_fmt(s.rssi_median, 'dBm', 1)}  "
                f"Q3={_fmt(s.rssi_q3, 'dBm', 1)}  最大={_fmt(s.rssi_max, 'dBm', 1)}")
            add(f"     サイト内 AP 数に対する隣接数の比率: {_fmt(s.density_ratio, digits=3)}"
                "  （1.0 = 全 AP が互いに聞こえる）")

            add("")
            add("  2〜4. 距離隣接との比較（上位 N 台）")
            add("     N   AP数  距離中央値  距離最大   距離上位N∩RF  RF∩距離上位N  RSSI上位N一致  "
                "RSSI上位N平均距離  距離上位N平均距離")
            for t in s.top_n:
                add(
                    f"     {t.n:<3} {t.aps:>4}  "
                    f"{_fmt(t.dist_median_m, 'm', 1):>9}  {_fmt(t.dist_max_m, 'm', 1):>8}  "
                    f"{_fmt_pct(t.dist_in_rf):>12}  {_fmt_pct(t.rf_in_dist):>12}  "
                    f"{_fmt_pct(t.rssi_top_match):>12}  "
                    f"{_fmt(t.rssi_top_mean_dist_m, 'm', 1):>16}  "
                    f"{_fmt(t.dist_top_mean_dist_m, 'm', 1):>16}"
                )

            add("")
            add("  5. 非対称性")
            add(f"     ペア数（無向）: {s.pair_count}  "
                f"双方向で観測された割合: {_fmt_pct(s.bidirectional_ratio)}")
            add(f"     方向差(dB)  中央値={_fmt(s.direction_diff_median_db, 'dB', 1)}  "
                f"最大={_fmt(s.direction_diff_max_db, 'dB', 1)}")

            add("")
            add("  6. データ品質")
            add(f"     ap_metrics に存在しない neighbor_mac: "
                f"{s.unknown_neighbor_macs} 台 / {s.unknown_neighbor_rows} 行")
            add(f"     座標あり AP: {s.aps_with_coords}  座標なし AP（マップ未配置）: "
                f"{s.aps_without_coords}")

        add("")
        add(f"[ 警告 ] {len(self.warnings)} 件")
        if not self.warnings:
            add("  （なし）")
        for w in self.warnings:
            add(f"  ! {w}")
        add("=" * 68)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 座標
# ---------------------------------------------------------------------------


def _latest_coordinates(metrics: pd.DataFrame) -> dict[str, dict]:
    """AP（MAC）ごとの最新の site_id / map_id / x_m / y_m を返す。

    最新行の座標が欠けている AP は「座標なし」として扱う（過去の座標は使わない。
    配置変更を古い値で埋めると距離が実態と食い違うため）。
    """
    out: dict[str, dict] = {}
    if metrics.empty or "mac" not in metrics.columns:
        return out
    df = metrics.dropna(subset=["mac"])
    df = df[df["mac"].astype("string").fillna("") != ""]
    if df.empty:
        return out
    df = df.sort_values("timestamp", kind="stable")
    for mac, grp in df.groupby("mac", sort=False):
        row = grp.iloc[-1]
        map_id = row.get("map_id")
        has_map = isinstance(map_id, str) and map_id.strip() != ""
        out[str(mac)] = {
            "site_id": str(row.get("site_id") or ""),
            "ap_name": str(row.get("ap_name") or ""),
            "map_id": str(map_id).strip() if has_map else "",
            "x_m": _as_float(row.get("x_m")),
            "y_m": _as_float(row.get("y_m")),
        }
    return out


def _as_float(value: object) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance(a: dict, b: dict) -> float | None:
    """同一 map_id 内のユークリッド距離。マップが違う／座標が無い組は None。"""
    if not a["map_id"] or a["map_id"] != b["map_id"]:
        return None
    if a["x_m"] is None or a["y_m"] is None or b["x_m"] is None or b["y_m"] is None:
        return None
    return math.hypot(a["x_m"] - b["x_m"], a["y_m"] - b["y_m"])


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def analyze(
    metrics: pd.DataFrame,
    rf_neighbors: pd.DataFrame,
    *,
    band: str = DEFAULT_BAND,
    top_n: Sequence[int] = DEFAULT_TOP_N,
) -> TopologyResult:
    """RF 隣接と距離隣接を突き合わせて診断結果を返す。

    :param metrics: ap_metrics（座標列 map_id / x_m / y_m を含む）
    :param rf_neighbors: rf_neighbors（全時点。内部で最新時点のみを使う）
    :param band: 対象バンド（``"24"`` / ``"5"`` / ``"6"``）
    :param top_n: 評価する「上位 N 台」
    """
    ns = tuple(sorted({int(n) for n in top_n if int(n) > 0}))
    result = TopologyResult(band=str(band), top_n=ns)

    if not rf_neighbors.empty:
        counts = rf_neighbors.groupby("timestamp").size().sort_index()
        result.snapshots = [(ts, int(n)) for ts, n in counts.items()]

    rf = latest_rf_neighbors(rf_neighbors)
    if not rf.empty:
        result.used_timestamp = rf["timestamp"].max()
        rf = rf[rf["band"].astype("string").fillna("") == str(band)]
    if rf.empty:
        result.warnings.append(
            f"バンド {band} の rf_neighbors が 1 件もありません。"
            "収集（scheduler の日次ジョブ / Save Now）が動いているか確認してください"
        )
        result.detail = pd.DataFrame(columns=_detail_columns(ns))
        return result

    coords = _latest_coordinates(metrics)
    known_macs = set(coords)

    # サイト内 AP 数は ap_metrics 側を正とする（RF 隣接に出てこない AP も数えるため）
    site_ap_counts: dict[str, int] = {}
    if not metrics.empty:
        site_ap_counts = {
            str(site_id): int(grp["mac"].nunique())
            for site_id, grp in metrics.groupby(metrics["site_id"].fillna(""), sort=False)
        }

    detail_rows: list[dict] = []
    for site_id, grp in rf.groupby(rf["site_id"].fillna(""), sort=True):
        site_names = grp["site_name"][grp["site_name"] != ""]
        site = SiteTopology(
            site_id=str(site_id),
            site_name=str(site_names.iloc[0]) if len(site_names) else "",
            band=str(band),
            site_ap_count=site_ap_counts.get(str(site_id), int(grp["ap_mac"].nunique())),
        )
        _analyze_site(site, grp, coords, known_macs, ns, detail_rows)
        result.sites.append(site)

    if result.has_small_sample:
        result.warnings.append(
            f"AP 数が {MIN_AP_COUNT_FOR_DIAGNOSIS} 未満のサイトがあります。"
            "少数 AP では全 AP が互いに隣接する完全グラフになるため、"
            "RF 隣接の広がりは評価できません（値は動作確認用）"
        )
    if not coords:
        result.warnings.append(
            "ap_metrics の座標（map_id / x_m / y_m）が 1 件も読めていません。"
            "距離隣接との比較は行えません"
        )

    result.detail = pd.DataFrame(detail_rows, columns=_detail_columns(ns))
    return result


def _detail_columns(ns: Sequence[int]) -> list[str]:
    cols = [
        "site_id", "site_name", "band", "ap_mac", "ap_name",
        "map_id", "x_m", "y_m", "has_coords",
        "rf_neighbor_count", "rf_neighbor_unknown", "rf_neighbor_other_map",
        "rssi_min", "rssi_median", "rssi_max",
        "same_map_ap_count",
        "bidirectional_ratio", "direction_diff_max_db",
    ]
    for n in ns:
        cols += [
            f"dist_top{n}_median_m",
            f"dist_top{n}_max_m",
            f"dist_top{n}_in_rf",
            f"rf_in_dist_top{n}",
            f"rssi_top{n}_match",
            f"rssi_top{n}_mean_dist_m",
            f"dist_top{n}_mean_dist_m",
        ]
    return cols


def _analyze_site(
    site: SiteTopology,
    rows: pd.DataFrame,
    coords: dict[str, dict],
    known_macs: set[str],
    ns: Sequence[int],
    detail_rows: list[dict],
) -> None:
    """1 サイト分を集計し、AP ごとの明細を ``detail_rows`` へ追記する。"""
    # 方向つき隣接: ap_mac -> {neighbor_mac: rssi}
    directed: dict[str, dict[str, float]] = {}
    for ap_mac, neighbor_mac, rssi in zip(rows["ap_mac"], rows["neighbor_mac"], rows["rssi"]):
        if not ap_mac or not neighbor_mac:
            continue
        value = None if pd.isna(rssi) else float(rssi)
        directed.setdefault(str(ap_mac), {})[str(neighbor_mac)] = value

    site.observer_count = len(directed)

    # -- 6. データ品質 --
    unknown_macs: set[str] = set()
    unknown_rows = 0
    for neighbors in directed.values():
        for nb in neighbors:
            if nb not in known_macs:
                unknown_macs.add(nb)
                unknown_rows += 1
    site.unknown_neighbor_macs = len(unknown_macs)
    site.unknown_neighbor_rows = unknown_rows

    site_macs = [m for m, c in coords.items() if c["site_id"] == site.site_id]
    if not site_macs:
        # ap_metrics が無い（または site_id が噛み合わない）場合は観測側 AP を母集団とする
        site_macs = sorted(directed)
    site.aps_with_coords = sum(
        1 for m in site_macs
        if m in coords and coords[m]["map_id"] and coords[m]["x_m"] is not None
    )
    site.aps_without_coords = len(site_macs) - site.aps_with_coords

    # -- 1. RF 隣接の広さ --
    counts = [len(v) for v in directed.values()]
    site.neighbors_median = _median(counts)
    site.neighbors_mean = _mean(counts)
    site.neighbors_max = _max(counts)
    site.neighbors_min = _min(counts)
    if site.site_ap_count > 1 and site.neighbors_mean is not None:
        site.density_ratio = site.neighbors_mean / (site.site_ap_count - 1)

    rssis = [r for v in directed.values() for r in v.values() if r is not None]
    site.rssi_min = _min(rssis)
    site.rssi_q1 = _quantile(rssis, 0.25)
    site.rssi_median = _median(rssis)
    site.rssi_q3 = _quantile(rssis, 0.75)
    site.rssi_max = _max(rssis)

    # -- 5. 非対称性 --
    pairs: set[tuple[str, str]] = set()
    for a, neighbors in directed.items():
        for b in neighbors:
            pairs.add((a, b) if a <= b else (b, a))
    diffs: list[float] = []
    bidirectional = 0
    for a, b in pairs:
        if b in directed.get(a, {}) and a in directed.get(b, {}):
            bidirectional += 1
            fwd = directed[a][b]
            rev = directed[b][a]
            if fwd is not None and rev is not None:
                diffs.append(abs(fwd - rev))
    site.pair_count = len(pairs)
    site.bidirectional_ratio = (bidirectional / len(pairs)) if pairs else None
    site.direction_diff_median_db = _median(diffs)
    site.direction_diff_max_db = _max(diffs)

    # -- 2〜4. 距離隣接との比較 --
    per_n: dict[int, dict[str, list[float]]] = {
        n: {"dists": [], "dist_in_rf": [], "rf_in_dist": [], "match": [],
            "rssi_mean_dist": [], "dist_mean_dist": [], "aps": []}
        for n in ns
    }

    for ap_mac in sorted(set(directed) | set(site_macs)):
        info = coords.get(ap_mac)
        neighbors = directed.get(ap_mac, {})
        rf_set = set(neighbors)
        rf_rssi = [r for r in neighbors.values() if r is not None]

        # 同一マップ内の他 AP との距離
        dist_pairs: list[tuple[float, str]] = []
        other_map = 0
        if info is not None and info["map_id"] and info["x_m"] is not None:
            for other in site_macs:
                if other == ap_mac:
                    continue
                oinfo = coords.get(other)
                if oinfo is None:
                    continue
                d = _distance(info, oinfo)
                if d is not None:
                    dist_pairs.append((d, other))
            # RF 隣接のうち、距離を出せない（別マップ／座標なし）相手の数
            for nb in rf_set:
                oinfo = coords.get(nb)
                if oinfo is None or _distance(info, oinfo) is None:
                    other_map += 1
        dist_pairs.sort(key=lambda t: (t[0], t[1]))

        row: dict = {
            "site_id": site.site_id,
            "site_name": site.site_name,
            "band": site.band,
            "ap_mac": ap_mac,
            "ap_name": (info or {}).get("ap_name", ""),
            "map_id": (info or {}).get("map_id", ""),
            "x_m": (info or {}).get("x_m"),
            "y_m": (info or {}).get("y_m"),
            "has_coords": bool(info and info["map_id"] and info["x_m"] is not None),
            "rf_neighbor_count": len(rf_set),
            "rf_neighbor_unknown": sum(1 for nb in rf_set if nb not in known_macs),
            "rf_neighbor_other_map": other_map,
            "rssi_min": _min(rf_rssi),
            "rssi_median": _median(rf_rssi),
            "rssi_max": _max(rf_rssi),
            "same_map_ap_count": len(dist_pairs),
            "bidirectional_ratio": _ap_bidirectional_ratio(ap_mac, neighbors, directed),
            "direction_diff_max_db": _ap_direction_diff_max(ap_mac, neighbors, directed),
        }

        # RSSI 上位（強い順）。RSSI が無い相手は末尾に回す
        rssi_sorted = [
            mac for mac, _ in sorted(
                neighbors.items(),
                key=lambda kv: (-(kv[1] if kv[1] is not None else -999.0), kv[0]),
            )
        ]

        for n in ns:
            dist_top = [mac for _, mac in dist_pairs[:n]]
            dist_vals = [d for d, _ in dist_pairs[:n]]
            rssi_top = rssi_sorted[:n]

            row[f"dist_top{n}_median_m"] = _median(dist_vals)
            row[f"dist_top{n}_max_m"] = _max(dist_vals)

            dist_in_rf = (
                len([m for m in dist_top if m in rf_set]) / len(dist_top) if dist_top else None
            )
            rf_in_dist = (
                len([m for m in rf_set if m in set(dist_top)]) / len(rf_set)
                if rf_set and dist_top else None
            )
            match = (
                len([m for m in rssi_top if m in set(dist_top)]) / len(dist_top)
                if dist_top and rssi_top else None
            )
            rssi_top_dists = [
                d for d in (
                    _distance(info, coords[m]) if info and m in coords else None
                    for m in rssi_top
                ) if d is not None
            ]

            row[f"dist_top{n}_in_rf"] = dist_in_rf
            row[f"rf_in_dist_top{n}"] = rf_in_dist
            row[f"rssi_top{n}_match"] = match
            row[f"rssi_top{n}_mean_dist_m"] = _mean(rssi_top_dists)
            row[f"dist_top{n}_mean_dist_m"] = _mean(dist_vals)

            bucket = per_n[n]
            if dist_top:
                bucket["aps"].append(1.0)
                bucket["dists"].extend(dist_vals)
                bucket["dist_mean_dist"].append(_mean(dist_vals))
            if dist_in_rf is not None:
                bucket["dist_in_rf"].append(dist_in_rf)
            if rf_in_dist is not None:
                bucket["rf_in_dist"].append(rf_in_dist)
            if match is not None:
                bucket["match"].append(match)
            if rssi_top_dists:
                bucket["rssi_mean_dist"].append(_mean(rssi_top_dists))

        detail_rows.append(row)

    for n in ns:
        b = per_n[n]
        site.top_n.append(TopNStats(
            n=n,
            aps=len(b["aps"]),
            dist_median_m=_median(b["dists"]),
            dist_max_m=_max(b["dists"]),
            dist_in_rf=_mean(b["dist_in_rf"]),
            rf_in_dist=_mean(b["rf_in_dist"]),
            rssi_top_match=_mean(b["match"]),
            rssi_top_mean_dist_m=_mean(b["rssi_mean_dist"]),
            dist_top_mean_dist_m=_mean(b["dist_mean_dist"]),
        ))


def _ap_bidirectional_ratio(
    ap_mac: str, neighbors: dict[str, float | None], directed: dict[str, dict[str, float | None]]
) -> float | None:
    if not neighbors:
        return None
    both = sum(1 for nb in neighbors if ap_mac in directed.get(nb, {}))
    return both / len(neighbors)


def _ap_direction_diff_max(
    ap_mac: str, neighbors: dict[str, float | None], directed: dict[str, dict[str, float | None]]
) -> float | None:
    diffs = []
    for nb, fwd in neighbors.items():
        rev = directed.get(nb, {}).get(ap_mac)
        if fwd is not None and rev is not None:
            diffs.append(abs(fwd - rev))
    return _max(diffs)
