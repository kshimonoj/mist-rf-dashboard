"""アップロードされたファイルの復元（サーバ側）。

復元の入力は「利用者が手元で加工したファイル」なので、ログ API のように
サーバ上のファイルを指すことができない。アップロードを受ける必要がある。

**アップロードされたファイルは ``data/`` 配下に置かない。** 一時ディレクトリに
書いて処理し、レスポンスを組み立てたら削除する（``TemporaryDirectory`` の後始末に
任せる）。復元結果もディスクには残さない。

ソルトとマッピングは :mod:`pseudonymizer.service` と同じ場所（``data/`` 直下、
ログ API のファイル名パターンに一致しない）から読む。**HTTP から取得できる経路は
作らない。**
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import restore as restore_mod
from . import service
from .restore import RestoreReport, UnsupportedFormatError

logger = logging.getLogger(__name__)

#: 1 リクエストで復元できるファイル数の上限（仮名化ダウンロードと揃える）
MAX_FILES = service.MAX_FILES

#: アップロードの合計サイズ上限。既定 50MB。
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class RestoreInputError(RuntimeError):
    """入力の指定が不正（呼び出し側で 400 にする）。"""


@dataclass(frozen=True)
class RestoredFile:
    """復元した 1 ファイル。"""

    source_name: str
    filename: str
    content: bytes


def _safe_name(raw: str) -> str:
    """アップロードされた名前からディレクトリ成分を落とす（パストラバーサル対策）。"""
    name = os.path.basename((raw or "").replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise RestoreInputError("ファイル名が不正です")
    return name


def validate_uploads(names: Sequence[str], sizes: Sequence[int]) -> None:
    """件数・合計サイズ・拡張子を検証する。読み込む前に弾けるものはここで弾く。"""
    if not names:
        raise RestoreInputError("復元するファイルが指定されていません")
    if len(names) > MAX_FILES:
        raise RestoreInputError(
            f"一度に復元できるのは {MAX_FILES} 件までです（指定: {len(names)} 件）"
        )
    total = sum(sizes)
    if total > MAX_UPLOAD_BYTES:
        raise RestoreInputError(
            f"アップロードできるのは合計 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB までです"
            f"（指定: {total / (1024 * 1024):.1f}MB）"
        )
    seen: set[str] = set()
    for raw in names:
        name = _safe_name(raw)
        if name in seen:
            raise RestoreInputError(f"同じ名前のファイルが複数あります: {name}")
        seen.add(name)
        ext = Path(name).suffix.lower()
        if ext not in restore_mod.SUPPORTED_EXTENSIONS:
            raise UnsupportedFormatError(
                f"対応していない形式です: {name}"
                f"（対応: {', '.join(sorted(restore_mod.SUPPORTED_EXTENSIONS))}）"
            )


def restore_uploads(
    uploads: Sequence[tuple[str, bytes]], *, time_restore: bool = True
) -> tuple[list[RestoredFile], RestoreReport]:
    """アップロードされた (ファイル名, 中身) を復元する。

    一時ディレクトリで処理し、戻すのはメモリ上のバイト列だけ。
    """
    validate_uploads([name for name, _ in uploads], [len(data) for _, data in uploads])

    engine = restore_mod.load_engine(time_restore=time_restore)

    outputs: list[RestoredFile] = []
    report = RestoreReport()
    # 入力・出力とも一時ディレクトリに置き、抜けたら消える（data/ には一切書かない）
    with tempfile.TemporaryDirectory(prefix="pseudonym-restore-") as tmp:
        in_dir = Path(tmp) / "in"
        out_dir = Path(tmp) / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        for raw, data in uploads:
            (in_dir / _safe_name(raw)).write_bytes(data)
        for raw, _ in uploads:
            src = in_dir / _safe_name(raw)
            item = engine.restore_file(src, out_dir)
            report.files.append(item)
            outputs.append(
                RestoredFile(
                    source_name=src.name,
                    filename=item.filename,
                    content=(out_dir / item.filename).read_bytes(),
                )
            )
    if report.residual_total:
        logger.warning(
            "restore: %d 件の仮名らしき文字列がマッピングに無いまま残りました"
            "（マッピングが古いか、別環境のソルトの可能性があります）",
            report.residual_total,
        )
    return outputs, report
