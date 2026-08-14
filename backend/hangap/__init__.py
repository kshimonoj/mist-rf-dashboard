"""ハングAP 分析用のログ読み込みパッケージ。

検出ロジックは含まない（本パッケージは結合・正規化・レポートのみ）。
"""
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
]
