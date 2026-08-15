"""ハングAP 分析用のパッケージ（ログの結合・正規化、ゼロ区間の検出、周辺AP判定）。

周辺 AP は **距離だけ**で判定する（RF 隣接は参考列にとどめる）。詳細は
:mod:`hangap.neighbors` の docstring を参照。
"""
from .detector import (
    CORE_RESULT_COLUMNS,
    DEFAULT_EVENT_WINDOW,
    DEFAULT_EXODUS_THRESHOLD,
    DEFAULT_MIN_ZERO_SAMPLES,
    DEFAULT_TRUNCATED_WARN_RATIO,
    RESULT_COLUMNS,
    detect,
    truncated_warning,
)
from .loader import (
    DEFAULT_FILE_TYPES,
    DEFAULT_GAP_FACTOR,
    LoadReport,
    LoadResult,
    load,
)
from .neighbors import (
    DEFAULT_MAX_DISTANCE_M,
    DEFAULT_NEIGHBOR_CLIENT_THRESHOLD,
    DEFAULT_NEIGHBOR_COUNT,
    NEIGHBOR_COLUMNS,
    NeighborContext,
    build_context,
    render_explain,
)

__all__ = [
    "load",
    "LoadResult",
    "LoadReport",
    "DEFAULT_FILE_TYPES",
    "DEFAULT_GAP_FACTOR",
    "detect",
    "RESULT_COLUMNS",
    "CORE_RESULT_COLUMNS",
    "DEFAULT_MIN_ZERO_SAMPLES",
    "DEFAULT_EVENT_WINDOW",
    "DEFAULT_EXODUS_THRESHOLD",
    "DEFAULT_TRUNCATED_WARN_RATIO",
    "truncated_warning",
    "build_context",
    "render_explain",
    "NeighborContext",
    "NEIGHBOR_COLUMNS",
    "DEFAULT_NEIGHBOR_COUNT",
    "DEFAULT_MAX_DISTANCE_M",
    "DEFAULT_NEIGHBOR_CLIENT_THRESHOLD",
]
