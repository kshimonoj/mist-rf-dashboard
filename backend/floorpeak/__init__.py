"""フロア別ピーク時点分析のパッケージ。

サイトと期間を指定すると、その期間で最も混雑した時点を選び、フロアごとの
AP 接続端末数を出す。ピークは **バケット化してから合計** して選ぶ（AP 間の
数秒のジッタで偽のピークを掴まないため）。フロア名は ``floormap_*_summary.csv``
から解決し、**最終的なフロア判定は ap_metrics の map_id で行う**（全無線が
停止していて floormap の ap_list に出てこない AP も正しいフロアに載せるため）。

``hangap`` パッケージとはコードを共有しない。読み込みだけ
:mod:`hangap.loader` を再利用する（同じログを 2 通りに読まないため）。
"""
from .analysis import (
    MODEL_COLORS,
    RESULT_COLUMNS,
    TOP_N,
    AnalysisParams,
    AnalysisResult,
    run_analysis,
)
from .floors import UNASSIGNED, FloorResolution, resolve_floors
from .loader import MetricsLoad, load_metrics
from .peak import PeakResult, find_peak

__all__ = [
    "run_analysis",
    "AnalysisParams",
    "AnalysisResult",
    "RESULT_COLUMNS",
    "TOP_N",
    "MODEL_COLORS",
    "load_metrics",
    "MetricsLoad",
    "find_peak",
    "PeakResult",
    "resolve_floors",
    "FloorResolution",
    "UNASSIGNED",
]
