"""``ap_metrics`` と ``ap_events`` の読み込み（サイト絞り込み・期間絞り込み）。

設計方針:

- 読み込み・正規化・重複排除・サンプリング間隔の推定は :mod:`hangap.loader` を
  **そのまま使う**（再実装しない）。``hangap`` 側のコードはここから **読むだけ** で、
  書き換えない。
- ``ap_events`` には ``site_id`` が **無い**。サイトの絞り込みは ``site_name`` で行う
  （``ap_metrics`` 側で解決した site_id → site_name を使う）。
- 期間は **半開区間** ``[window_start, window_end)``。
- **``ap_metrics`` は期間で絞らない。** イベント直前・直後のサンプルは窓の外に
  あることがあるため（窓で切ると窓際のイベントが必ず「照合不可」になる）。
- ``ap_events`` は 2 つ持つ。``events`` は期間で絞ったもの（数える対象）、
  ``events_all`` は絞る前のもの（レーダーの突合と汚染判定に使う）。
- ネットワークアクセス・LLM 呼び出しは行わない。入力はローカルファイルのみ。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pandas as pd

from hangap import loader as hangap_loader

#: DataFrame まで読み込む種別（rf_neighbors はこの分析では使わない）
LOAD_FILE_TYPES: tuple[str, ...] = ("ap_metrics", "ap_metrics_v1", "ap_events")

#: ap_metrics として読み込む種別（使用ファイル数の集計に使う）
METRICS_FILE_TYPES: tuple[str, ...] = ("ap_metrics", "ap_metrics_v1")

#: ap_events の種別
EVENTS_FILE_TYPE: str = "ap_events"

#: サンプリング間隔を推定できなかったときの既定値（秒）。使ったら必ず警告に出す
FALLBACK_INTERVAL_SECONDS: float = 300.0

#: 分析に必要な ap_metrics の列（これ以外は読み込み後に捨てる）
NEEDED_METRIC_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_id", "site_name", "ap_id", "ap_name", "mac", "num_clients",
    "radio_24_utilization", "radio_5_utilization", "radio_6_utilization",
)


class LoadError(RuntimeError):
    """読み込みを続行できない状態。CLI は終了コード 1、API は failed にする。"""


class UnclassifiedInputError(LoadError):
    """入力ファイルの種別を判定できなかった。"""


class SiteNotFoundError(LoadError):
    """指定されたサイトがログに存在しない。"""

    def __init__(self, message: str, missing: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.missing = tuple(missing)


class NoMetricsError(LoadError):
    """分析対象の ap_metrics が 1 行も無い。

    **「チャネル変更が無かった」とは別の状態である。** そもそも読むものが
    無かったので、結果 0 件として扱ってはいけない。
    """


class NoEventsError(LoadError):
    """``ap_events`` が 1 行も無い。

    こちらも「RRM が動かなかった」ではなく「イベントログが無い」である。
    期間内にイベントが 0 件なのは正常な結果なので、そちらは例外にしない。
    """


@dataclass
class RrmLoad:
    """:func:`load_logs` の結果。"""

    #: 期間で絞る **前** の ap_metrics（前後サンプルを窓の外から拾うため）
    metrics: pd.DataFrame
    #: 期間で絞った ap_events（数える対象）
    events: pd.DataFrame
    #: 期間で絞る前の ap_events（レーダーの突合・汚染判定に使う）
    events_all: pd.DataFrame
    #: 前後サンプルの距離を判定するのに使う間隔（秒）
    interval_seconds: float
    #: 推定できたか（False ならフォールバックを使った）
    interval_estimated: bool
    site_ids: tuple[str, ...]
    site_names: tuple[str, ...]
    site_labels: tuple[str, ...]
    report: hangap_loader.LoadReport
    warnings: list[str] = field(default_factory=list)


def is_data_file(path: Path) -> bool:
    """走査対象のデータファイルか（判定は hangap.loader に委ねる）。

    ``rrm_results`` 配下は :data:`hangap.loader.EXCLUDED_DIR_NAMES` が無条件に外す。
    **分析の出力を次の分析の入力として拾わせないこと。**
    """
    return hangap_loader.is_data_file(path)


def collect_files(directory: str | Path) -> list[Path]:
    """ディレクトリ配下の CSV/XLSX を列挙する（**呼び出し時点で確定させる**）。"""
    root = Path(directory)
    if not root.is_dir():
        return []
    return [f for f in sorted(root.rglob("*")) if f.is_file() and is_data_file(f)]


def filter_window(
    events: pd.DataFrame,
    window_start: pd.Timestamp | None,
    window_end: pd.Timestamp | None,
) -> pd.DataFrame:
    """期間で絞り込む。**半開区間** ``[window_start, window_end)``。"""
    if events.empty:
        return events
    keep = pd.Series(True, index=events.index)
    if window_start is not None:
        keep &= events["event_timestamp"] >= window_start
    if window_end is not None:
        keep &= events["event_timestamp"] < window_end
    return events[keep].reset_index(drop=True)


def _site_names(metrics: pd.DataFrame) -> list[str]:
    if metrics.empty:
        return []
    names = metrics["site_name"].dropna().astype(str)
    names = names[names != ""]
    return list(dict.fromkeys(names))


def used_files(report: hangap_loader.LoadReport, file_types: Sequence[str]) -> int:
    """指定種別として識別できたファイル数（``files_scanned`` はログ全体の数）。"""
    return sum(
        report.file_stats[t].files for t in file_types if t in report.file_stats
    )


def load_logs(
    files: Sequence[Path],
    *,
    sites: Sequence[str] | None = None,
    window_start: pd.Timestamp | None = None,
    window_end: pd.Timestamp | None = None,
) -> RrmLoad:
    """``ap_metrics`` と ``ap_events`` を読み込み、サイトと期間で絞り込む。

    :param sites: 対象サイト（site_id または site_name）。``None`` / 空なら全サイト。
        **複数指定できる**（サイト別の比較を出すため）。
    :raises UnclassifiedInputError: 入力の種別を判定できなかった
    :raises SiteNotFoundError: 指定サイトがログに無い
    :raises NoMetricsError: ap_metrics が 1 行も無い
    :raises NoEventsError: ap_events が 1 行も無い
    """
    requested = [s for s in (sites or []) if str(s).strip()]
    result = hangap_loader.load(
        list(files),
        file_types=LOAD_FILE_TYPES,
        sites=requested or None,
    )
    report = result.report
    site_filter = report.site_filter

    rows_before = site_filter.rows_before if site_filter is not None else report.metrics_rows
    if rows_before == 0:
        if report.unclassified:
            sample = ", ".join(report.unclassified[:5])
            more = " ..." if len(report.unclassified) > 5 else ""
            raise UnclassifiedInputError(
                f"入力ファイルの種別を判定できませんでした（ap_metrics に一致しません）: {sample}{more}"
            )
        raise NoMetricsError(
            f"ap_metrics を 1 行も読み込めませんでした（走査ファイル数={report.files_scanned}）。"
            "分析対象のログが存在しないか、保存期間の設定で削除された可能性があります。"
        )

    if site_filter is not None and site_filter.missing:
        available = ", ".join(site_filter.available)
        raise SiteNotFoundError(
            f"指定されたサイトがログに見つかりません: {', '.join(site_filter.missing)}"
            f"（ログに含まれるサイト: {available or 'なし'}）",
            missing=site_filter.missing,
        )

    metrics = result.metrics
    if metrics.empty:
        raise NoMetricsError(
            "指定されたサイトの ap_metrics が 1 行もありません: "
            + (", ".join(requested) if requested else "（全サイト）")
        )
    metrics = metrics.reindex(columns=list(NEEDED_METRIC_COLUMNS))

    warnings: list[str] = []

    overall = report.overall_interval_seconds
    estimated = overall is not None and overall > 0
    interval = float(overall) if estimated else FALLBACK_INTERVAL_SECONDS
    if not estimated:
        warnings.append(
            "サンプリング間隔を推定できませんでした（サンプルが少なすぎます）。"
            f"前後サンプルの距離判定には既定値 {FALLBACK_INTERVAL_SECONDS:g} 秒を使います"
        )

    names = _site_names(metrics)
    if site_filter is not None:
        site_ids = tuple(site_filter.site_ids)
        labels = tuple(site_filter.labels)
    else:
        # site_id ごとに 1 ラベル。site_name が空の行が混ざっても
        # ``[id]`` だけのラベルを重ねて出さない（名前のある行を優先する）
        by_id: dict[str, str] = {}
        pairs = metrics[["site_id", "site_name"]].fillna("").astype(str).drop_duplicates()
        for site_id, site_name in pairs.itertuples(index=False, name=None):
            if not site_id:
                continue
            # 初出を採り、名前が空だった site_id は後から出てきた名前で埋める
            if site_id not in by_id or (site_name and not by_id[site_id]):
                by_id[site_id] = site_name
        site_ids = tuple(by_id)
        labels = tuple(
            f"{name} [{site_id}]" if name else f"[{site_id}]"
            for site_id, name in by_id.items()
        )
    if len(names) < len(site_ids):
        warnings.append(
            f"site_name が重複しています（site_id {len(site_ids)} 件に対して site_name {len(names)} 件）。"
            "ap_events は site_name でしか突合できないため、同名サイトのイベントは分離できません"
        )

    # ap_events は site_id を持たないので site_name で絞る（PRECONDITION 4）
    events_all = result.events
    if requested and not events_all.empty:
        keep = events_all["site_name"].astype(str).isin(names)
        dropped = int((~keep).sum())
        events_all = events_all[keep].reset_index(drop=True)
        if dropped and events_all.empty:
            warnings.append(
                f"対象サイトの ap_events が 1 件もありません（他サイトのイベント {dropped} 件は除外しました）"
            )

    if result.events.empty:
        raise NoEventsError(
            f"ap_events を 1 行も読み込めませんでした（走査ファイル数={report.files_scanned}）。"
            "イベントログが存在しないか、保存期間の設定で削除された可能性があります。"
            "これは「RRM が動作しなかった」とは別の状態です。"
        )

    events = filter_window(events_all, window_start, window_end)
    if events.empty:
        warnings.append(
            "指定された期間に ap_events が 1 件もありません"
            f"（対象サイトのイベントは全期間で {len(events_all)} 件あります）。"
            "期間内に RRM が動作しなかったか、期間の指定が外れています"
        )

    return RrmLoad(
        metrics=metrics,
        events=events,
        events_all=events_all,
        interval_seconds=interval,
        interval_estimated=estimated,
        site_ids=site_ids,
        site_names=tuple(names),
        site_labels=labels,
        report=report,
        warnings=warnings,
    )
