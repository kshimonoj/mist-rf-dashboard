"""ハングAP 分析用のパッケージ（ログの結合・正規化と、ゼロ区間の検出）。

周辺 AP の判定（距離・RF 隣接・近傍集合）は含まない。
"""
from .detector import (
    DEFAULT_EVENT_WINDOW,
    DEFAULT_EXODUS_THRESHOLD,
    DEFAULT_MIN_ZERO_SAMPLES,
    RESULT_COLUMNS,
    detect,
)
from .loader import (
    DEFAULT_FILE_TYPES,
    DEFAULT_GAP_FACTOR,
    LoadReport,
    LoadResult,
    load,
)

__all__ = [
    "load",
    "LoadResult",
    "LoadReport",
    "DEFAULT_FILE_TYPES",
    "DEFAULT_GAP_FACTOR",
    "detect",
    "RESULT_COLUMNS",
    "DEFAULT_MIN_ZERO_SAMPLES",
    "DEFAULT_EVENT_WINDOW",
    "DEFAULT_EXODUS_THRESHOLD",
]
