"""ダウンロード時のその場仮名化（サーバ側）。

CLI（:mod:`pseudonymizer.cli`）と **同じエンジン・同じソルト・同じマッピング** を使う。
仮名化版のファイルは一切ディスクに残さない（メモリ上で作って返すだけ）。

一貫性について（ここが壊れると仮名化データは使い物にならない）:

- 仮名は「HMAC のハッシュ順に配る連番」であり、番号は **入力集合に依存する**。
  ダウンロードのたびにソルトを作り直すと、同じ AP が毎回違う仮名になる。
- そのためソルトとマッピングは :data:`SALT_PATH` / :data:`MAP_PATH` に **永続化** し、
  リクエストをまたいで再利用する。
- 置き場所は ``data/`` 直下。``data/logs`` の中に置くと、ログの一覧・ダウンロード API
  から落とせてしまい仮名化の意味が消える（:mod:`routers.logs` のファイル名パターンで
  弾かれることをテストで固定している）。
- 採番とマッピングの書き戻しは :data:`_LOCK` で直列化する。並行リクエストで
  別々に採番すると同じ番号が二重に配られる。

このモジュールでは仮名化エンジン（transforms / leakcheck / salt）に手を入れない。
分析結果だけに必要な処理（``" | "`` 区切りの時刻リスト、構造由来の日本語の扱い）は
ここでエンジンを **包んで** 実現する。
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from . import cli
from .leakcheck import RULE_NON_ASCII, LeakCheckFailed, Violation, check_output
from .salt import (
    DEFAULT_MAP_FILENAME,
    DEFAULT_SALT_FILENAME,
    DEFAULT_SHIFT_GRANULARITY,
    load_or_create_salt,
)
from .schemas import (
    EVENT_LIST_SEPARATOR,
    HANGAP_RESULT_TEXT_LITERALS,
    FileType,
    TransformType as T,
)
from .transforms import Pseudonymizer, PseudonymizeError, load_mapping, save_mapping, shift_timestamp

logger = logging.getLogger(__name__)

#: ソルト・マッピングの置き場所。**``data/logs`` の外**（ログ API から取れてはならない）。
#: テストは monkeypatch でこの 2 つを差し替える。
DATA_DIR = "/app/data"
SALT_PATH = os.path.join(DATA_DIR, DEFAULT_SALT_FILENAME)
MAP_PATH = os.path.join(DATA_DIR, DEFAULT_MAP_FILENAME)

#: 1 リクエストで仮名化できるファイル数の上限。
#: 全ファイルをメモリに載せてから変換する（1 ファイルでも漏れたら何も返さないため）。
MAX_FILES = 50

#: 仮名化済みであることを示す印（ファイル名の末尾）
PSEUDONYMIZED_SUFFIX = "_pseudonymized"

#: ホワイトリスト外の列があったらエラーにする（CLI の既定と同じ。黙って通さない）
UNKNOWN_MODE = cli.UNKNOWN_ERROR

_LOCK = threading.RLock()

#: ファイル名に埋まっている ``YYYYMMDD_HHMM`` / ``YYYYMMDD_HHMMSS``
_NAME_TIMESTAMP = re.compile(r"(?<!\d)(\d{8})_(\d{6}|\d{4})(?!\d)")

_BOM = b"\xef\xbb\xbf"


class PseudonymizeInputError(RuntimeError):
    """入力の指定が不正（呼び出し側で 400 にする）。"""


@dataclass(frozen=True)
class Output:
    """仮名化した 1 ファイル。"""

    #: 元のファイル名（ログ側の突き合わせ用。レスポンスには出さない）
    source_name: str
    #: 仮名化済みであることが分かり、日付がずれたファイル名
    filename: str
    content: bytes


# ---------------------------------------------------------------------------
# ファイル名（中身の時刻はずれるのに、ファイル名だけ実日付が残ると台無しになる）
# ---------------------------------------------------------------------------


def shift_name_timestamp(name: str, offset_seconds: int) -> str:
    """ファイル名の ``YYYYMMDD_HHMM(SS)`` を中身と同じだけずらす。

    タイムシフトは日（または週）単位なので、時刻部分は変わらず日付だけがずれる。
    毎正時保存という並びも、ファイル同士の前後関係も保たれる。
    """
    match = _NAME_TIMESTAMP.search(name)
    if match is None:
        raise PseudonymizeError(f"cannot find a timestamp in filename: {name}")
    time_fmt = "%H%M%S" if len(match.group(2)) == 6 else "%H%M"
    parsed = datetime.strptime(f"{match.group(1)}_{match.group(2)}", f"%Y%m%d_{time_fmt}")
    shifted = parsed + timedelta(seconds=offset_seconds)
    return name[: match.start()] + shifted.strftime(f"%Y%m%d_{time_fmt}") + name[match.end() :]


def output_name(source_name: str, offset_seconds: int) -> str:
    """``ap_metrics_20260101_0900_JST.csv`` → ``ap_metrics_20250901_0900_JST_pseudonymized.csv``"""
    stem, ext = os.path.splitext(source_name)
    return f"{shift_name_timestamp(stem, offset_seconds)}{PSEUDONYMIZED_SUFFIX}{ext}"


# ---------------------------------------------------------------------------
# 分析結果だけに必要な処理
# ---------------------------------------------------------------------------


def shift_timestamp_list(raw: str, offset_seconds: int) -> str:
    """``" | "`` 区切りで並んだ時刻の各要素をずらす。区切りと件数はそのまま保つ。"""
    if not raw or not raw.strip() or not offset_seconds:
        return raw
    parts = raw.split(EVENT_LIST_SEPARATOR)
    return EVENT_LIST_SEPARATOR.join(
        shift_timestamp(p, offset_seconds) if p.strip() else p for p in parts
    )


#: 長いものから消す（``打ち切り(欠測)`` を消す前に ``回復`` が食い合わないようにする）
_TEXT_LITERALS: tuple[str, ...] = tuple(
    sorted(HANGAP_RESULT_TEXT_LITERALS, key=len, reverse=True)
)


def _strip_known_literals(cell: str) -> str:
    for literal in _TEXT_LITERALS:
        cell = cell.replace(literal, "")
    return cell


def non_ascii_violations(
    header: Sequence[str], rows: Sequence[dict[str, str]], allowed_columns: frozenset[str]
) -> list[Violation]:
    """構造由来の日本語を除いたうえで、残った非 ASCII を違反として返す。

    分析結果は列名も判定値も日本語なので、leak check の非 ASCII 規則をそのまま
    当てると必ず発火する。列名はホワイトリストに載っているものだけを許し、値は
    :data:`schemas.HANGAP_RESULT_TEXT_LITERALS` に挙げた文字列だけを取り除く。
    それでも非 ASCII が残るなら、施設名・SSID などの変換漏れとして扱う。

    戻り値は値そのものを含まない（:class:`leakcheck.Violation` と同じ扱い）。
    """
    violations: list[Violation] = []
    for column in header:
        if column not in allowed_columns and any(ord(ch) > 127 for ch in column):
            violations.append(Violation(RULE_NON_ASCII, column, 1))
    for i, row in enumerate(rows, start=2):  # 1 行目はヘッダ
        for column in header:
            cell = row.get(column) or ""
            if not cell:
                continue
            if any(ord(ch) > 127 for ch in _strip_known_literals(cell)):
                violations.append(Violation(RULE_NON_ASCII, column, i))
    return violations


def _transform_row(engine: Pseudonymizer, ft: FileType, row: dict[str, str]) -> dict[str, str]:
    """1 行を仮名化する。``TIMESTAMP_LIST`` だけここで処理し、残りはエンジンに任せる。"""
    out: dict[str, str] = {}
    for column, raw in row.items():
        rule = ft.rule_for(column)
        if rule is None:
            out[column] = raw
        elif rule is T.TIMESTAMP_LIST:
            out[column] = shift_timestamp_list(raw, engine.time_offset_seconds)
        else:
            out[column] = engine.transform_value(rule, raw)
    return out


def _transform_hangap_result(engine: Pseudonymizer, item: cli.InputFile) -> str:
    """分析結果 1 ファイルを仮名化して CSV テキストを返す（leak check 込み）。"""
    out_rows = [
        {c: r.get(c, "") for c in item.output_columns}
        for r in (_transform_row(engine, item.file_type, row) for row in item.rows)
    ]

    whitelist = item.file_type.whitelist
    # 非 ASCII 規則だけは構造を分かっているこちらで判定し直す。他の規則は素通し。
    violations = [
        v
        for v in check_output(
            item.output_columns,
            out_rows,
            allowed_columns=whitelist,
            allowed_ips=frozenset(engine.generated_ips),
        )
        if v.rule != RULE_NON_ASCII
    ]
    violations.extend(non_ascii_violations(item.output_columns, out_rows, whitelist))
    if violations:
        raise LeakCheckFailed(item.path, violations)

    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=item.output_columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(out_rows)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def _had_bom(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(3) == _BOM


def _read(path: Path) -> cli.InputFile:
    """CLI と同じ読み込み（種別判定・ホワイトリスト検証込み）。

    分析結果 csv は Excel 向けに BOM 付きで書かれているので ``utf-8-sig`` で読む
    （BOM 無しのファイルでも挙動は変わらない）。
    """
    return cli.read_input(str(path), UNKNOWN_MODE, encoding="utf-8-sig")


def _load_engine() -> Pseudonymizer:
    """永続化したソルト・マッピングでエンジンを組み立てる（``_LOCK`` の中で呼ぶこと）。"""
    existed = os.path.exists(SALT_PATH)
    material, created = load_or_create_salt(
        SALT_PATH, granularity=DEFAULT_SHIFT_GRANULARITY, quiet=True
    )
    if created:
        logger.warning(
            "pseudonymize: created a new salt file: %s — "
            "このファイルを失うと過去に配布した仮名化ファイルとの対応が切れます",
            SALT_PATH,
        )
    elif existed:
        mode = stat.S_IMODE(os.stat(SALT_PATH).st_mode)
        if mode & 0o077:
            logger.warning("pseudonymize: salt file is readable by others (mode %04o): %s",
                           mode, SALT_PATH)
    mapping = load_mapping(MAP_PATH, material)
    return Pseudonymizer(material, mapping, warn=lambda msg: logger.warning("pseudonymize: %s", msg))


def pseudonymize_files(paths: Sequence[Path]) -> list[Output]:
    """指定したファイルを **同一のソルト・マッピング** でまとめて仮名化する。

    1 ファイルでも leak check が発火したら :class:`leakcheck.LeakCheckFailed` を
    投げ、**何も返さない**（途中まで流してから失敗する形にしない）。
    """
    if not paths:
        raise PseudonymizeInputError("仮名化するファイルが指定されていません")
    if len(paths) > MAX_FILES:
        raise PseudonymizeInputError(
            f"一度に仮名化できるのは {MAX_FILES} 件までです（指定: {len(paths)} 件）"
        )

    for path in paths:
        if not path.is_file():
            raise PseudonymizeInputError(f"ファイルが見つかりません: {path.name}")

    with _LOCK:
        engine = _load_engine()

        # ファイルは 2 回読む。全ファイルの行を同時にメモリへ載せると、上限いっぱい
        # （50 ファイル）で数百 MB になる。1 回目は採番対象の収集だけなので、
        # 読んだ行は 1 ファイル分ずつ捨てられる。
        for path in paths:
            item = _read(path)
            for row in item.rows:
                engine.observe_row(item.file_type, row)
        engine.build()

        # 変換・検証をすべて終えてから返す（1 件でも漏れたら 1 件も返さない）
        outputs: list[Output] = []
        for path in paths:
            item = _read(path)
            if item.file_type.key == "hangap_result":
                text = _transform_hangap_result(engine, item)
            else:
                # ログ CSV は CLI とまったく同じ経路を通す（出力が食い違わないように）
                _rows, text = cli.transform_file(engine, item, UNKNOWN_MODE)
            data = text.encode("utf-8")
            if _had_bom(path):
                data = _BOM + data  # Excel 向け。元ファイルの書式を保つ
            name = path.name
            outputs.append(
                Output(
                    source_name=name,
                    filename=output_name(name, engine.time_offset_seconds),
                    content=data,
                )
            )

        if engine.mapping.dirty:
            save_mapping(MAP_PATH, engine.mapping)
        return outputs
