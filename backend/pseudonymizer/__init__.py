"""Mist Dashboard の CSV ログを仮名化（pseudonymization）するツール。

マスキング（不可逆な情報の破棄）ではなく、一貫性のある別名への置換を行う。
詳細と限界については README.md を参照。
"""
from .leakcheck import LeakCheckFailed, Violation, check_output
from .salt import SaltError, SaltMaterial, load_or_create_salt
from .schemas import FILE_TYPES, FileType, TransformType, detect_file_type
from .transforms import MappingStore, Pseudonymizer, PseudonymizeError

__all__ = [
    "FILE_TYPES",
    "FileType",
    "LeakCheckFailed",
    "MappingStore",
    "PseudonymizeError",
    "Pseudonymizer",
    "SaltError",
    "SaltMaterial",
    "TransformType",
    "Violation",
    "check_output",
    "detect_file_type",
    "load_or_create_salt",
]
