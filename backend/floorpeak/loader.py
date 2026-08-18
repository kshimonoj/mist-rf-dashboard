"""ap_metrics の読み込み（サイト絞り込み・期間絞り込み・バケット幅の決定）。

設計方針:

- 読み込み・正規化・重複排除・サンプリング間隔の推定は :mod:`hangap.loader` を
  **そのまま使う**（再実装しない）。同じログを 2 通りに読む実装が並ぶと、
  Hang AP 分析と結果が食い違ったときに原因を切り分けられなくなる。
  ``hangap`` 側のコードはここから **読むだけ** で、書き換えない。
- floorpeak が要るのは ap_metrics だけなので、``file_types`` を絞って読む
  （ap_events / rf_neighbors は DataFrame まで読み込まない）。
- 期間は **半開区間** ``[window_start, window_end)``。窓の右端ちょうどのサンプルは
  入らない（hangap の detector と同じ扱い）。
- ネットワークアクセス・LLM 呼び出しは行わない。入力はローカルファイルのみ。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pandas as pd

from hangap import loader as hangap_loader

#: DataFrame まで読み込む種別。ap_events / rf_neighbors はこの分析では使わない
METRICS_FILE_TYPES: tuple[str, ...] = ("ap_metrics", "ap_metrics_v1")

#: サンプリング間隔を推定できなかったときのバケット幅（秒）。使ったら必ず警告に出す
FALLBACK_BUCKET_SECONDS: float = 300.0

#: 分析に必要な列（ap_metrics の一部）。ここに無い列は結果にも出さない
NEEDED_COLUMNS: tuple[str, ...] = (
    "timestamp", "site_id", "site_name", "ap_id", "ap_name", "mac", "model",
    "num_clients", "status", "map_id", "x_m", "y_m",
)


#: hangap のローダが必ず出すが floorpeak には無関係な警告。
#: floorpeak は ap_events を **意図的に読み込まない** ので、「ap_events が 1 件もない」は
#: 常に出る。無意味な警告を毎回見せると、本当に読むべき警告が埋もれる。
_IGNORED_WARNING_MARKERS: tuple[str, ...] = ("ap_events",)


def relevant_warnings(warnings: Sequence[str]) -> list[str]:
    """ローダの警告のうち floorpeak に関係するものだけを残す。"""
    return [w for w in warnings if not any(m in w for m in _IGNORED_WARNING_MARKERS)]


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

    **「ピークが無かった」とは別の状態である。** そもそも読むものが無かったので、
    結果 0 件として扱ってはいけない。
    """


@dataclass
class MetricsLoad:
    """:func:`load_metrics` の結果。"""

    #: 期間で絞り込んだ後の ap_metrics
    metrics: pd.DataFrame
    #: 期間で絞り込む **前** の行数（「ログが無い」と「期間に無い」を区別するため）
    rows_before_window: int
    #: ピーク判定に使うバケット幅（秒）
    bucket_seconds: float
    #: 推定できずフォールバックを使ったか
    bucket_seconds_estimated: bool
    site_id: str
    site_name: str
    report: hangap_loader.LoadReport
    warnings: list[str] = field(default_factory=list)


def is_data_file(path: Path) -> bool:
    """走査対象のデータファイルか（判定は hangap.loader に委ねる）。

    ``hangap_results`` / ``floorpeak_results`` 配下は
    :data:`hangap.loader.EXCLUDED_DIR_NAMES` が無条件に外す。**分析の出力を次の
    分析の入力として拾わせないこと。**
    """
    return hangap_loader.is_data_file(path)


def collect_files(directory: str | Path) -> list[Path]:
    """ディレクトリ配下の CSV/XLSX を列挙する（**呼び出し時点で確定させる**）。

    分析中に定期収集がファイルを足しても結果が揺れないよう、ジョブ開始時に
    この一覧を固定して使う。
    """
    root = Path(directory)
    if not root.is_dir():
        return []
    return [f for f in sorted(root.rglob("*")) if f.is_file() and is_data_file(f)]


def filter_window(
    metrics: pd.DataFrame,
    window_start: pd.Timestamp | None,
    window_end: pd.Timestamp | None,
) -> pd.DataFrame:
    """期間で絞り込む。**半開区間** ``[window_start, window_end)``。

    右端を含めると、隣り合う 2 つの窓で同じサンプルが両方に入る。
    """
    if metrics.empty:
        return metrics
    keep = pd.Series(True, index=metrics.index)
    if window_start is not None:
        keep &= metrics["timestamp"] >= window_start
    if window_end is not None:
        keep &= metrics["timestamp"] < window_end
    return metrics[keep].reset_index(drop=True)


def load_metrics(
    files: Sequence[Path],
    *,
    site: str,
    window_start: pd.Timestamp | None = None,
    window_end: pd.Timestamp | None = None,
) -> MetricsLoad:
    """ap_metrics を読み込み、サイトと期間で絞り込む。

    :param site: 対象サイト（site_id または site_name）。**単一指定が必須**。
        「サイト全体のピーク」は複数サイトでは定義できない。
    :raises UnclassifiedInputError: 入力の種別を判定できなかった
    :raises SiteNotFoundError: 指定サイトがログに無い
    :raises NoMetricsError: 対象になる ap_metrics が 1 行も無い
    """
    result = hangap_loader.load(
        list(files), file_types=METRICS_FILE_TYPES, sites=[site],
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
            f"指定されたサイトの ap_metrics が 1 行もありません: {site}"
        )

    warnings: list[str] = []

    # バケット幅は **期間で絞り込む前** のデータから推定した値を使う。
    # 窓を短く切ると推定に使えるサンプルが減り、間隔がぶれる。
    overall = report.overall_interval_seconds
    estimated = overall is not None and overall > 0
    bucket_seconds = float(overall) if estimated else FALLBACK_BUCKET_SECONDS
    if not estimated:
        warnings.append(
            "サンプリング間隔を推定できませんでした"
            f"（サンプルが少なすぎます）。バケット幅は既定値 {FALLBACK_BUCKET_SECONDS:g} 秒を使います"
        )

    site_id = ""
    site_name = ""
    if site_filter is not None and site_filter.site_ids:
        site_id = site_filter.site_ids[0]
        if len(site_filter.site_ids) > 1:
            warnings.append(
                f"サイト指定 {site!r} が複数の site_id に一致しました"
                f"（{', '.join(site_filter.site_ids)}）。同名サイトが混在している可能性があります"
            )
    if not metrics.empty:
        names = metrics["site_name"].dropna().astype(str)
        names = names[names != ""]
        if not names.empty:
            site_name = str(names.iloc[0])
        if not site_id:
            ids = metrics["site_id"].dropna().astype(str)
            ids = ids[ids != ""]
            if not ids.empty:
                site_id = str(ids.iloc[0])

    windowed = filter_window(metrics, window_start, window_end)
    if windowed.empty:
        raise NoMetricsError(
            "指定された期間に ap_metrics が 1 行もありません"
            f"（サイトのログは {len(metrics)} 行あります）。期間の指定を確認してください。"
            "これは「ピークが無かった」とは別の状態です。"
        )

    return MetricsLoad(
        metrics=windowed,
        rows_before_window=int(len(metrics)),
        bucket_seconds=bucket_seconds,
        bucket_seconds_estimated=estimated,
        site_id=site_id,
        site_name=site_name,
        report=report,
        warnings=warnings,
    )
