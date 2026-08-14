"""ソルトとタイムオフセットの生成・読込・永続化。

ソルトファイル自体が機密であり、失うと過去に仮名化したログとの対応が切れる。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

SALT_VERSION = 1
DEFAULT_SALT_FILENAME = ".pseudonym_salt.json"
DEFAULT_MAP_FILENAME = ".pseudonym_map.json"

# タイムシフトの粒度。既定は「日単位」（時刻は保存され、曜日はずれる）。
# 「週単位」は曜日と時刻の両方が保存されるため、曜日パターン分析に使えるが
# 再識別リスクが上がる（例: 「土曜夕方の混雑」まで特定できてしまう）。
GRANULARITY_DAY = "day"
GRANULARITY_WEEK = "week"
SHIFT_GRANULARITIES = (GRANULARITY_DAY, GRANULARITY_WEEK)
DEFAULT_SHIFT_GRANULARITY = GRANULARITY_DAY

_SECONDS_PER_DAY = 24 * 3600
_SECONDS_PER_WEEK = 7 * _SECONDS_PER_DAY

_MIN_SHIFT_DAYS = 60
_MAX_SHIFT_DAYS = 1825  # 約 5 年
_MIN_SHIFT_WEEKS = 8
_MAX_SHIFT_WEEKS = 260  # 約 5 年


class SaltError(RuntimeError):
    """ソルトファイルの読込・検証に失敗した。"""


@dataclass(frozen=True)
class SaltMaterial:
    """ソルトファイルの内容。"""

    salt: bytes
    time_offset_seconds: int
    created_at: str
    version: int = SALT_VERSION
    shift_granularity: str = GRANULARITY_WEEK

    @property
    def fingerprint(self) -> str:
        """ソルトの指紋。マッピングファイルの取り違え検出に使う（ソルト自体は復元できない）。"""
        return hmac.new(self.salt, b"pseudonymizer:fingerprint", hashlib.sha256).hexdigest()[:16]

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "salt": self.salt.hex(),
            "time_offset_seconds": self.time_offset_seconds,
            "created_at": self.created_at,
            "shift_granularity": self.shift_granularity,
        }


def _write_private_json(path: str, payload: dict) -> None:
    """0600 でファイルを作成して JSON を書き出す。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
    except Exception:
        os.unlink(tmp_path)
        raise
    os.replace(tmp_path, path)
    os.chmod(path, 0o600)


def generate_salt_material(granularity: str = DEFAULT_SHIFT_GRANULARITY) -> SaltMaterial:
    """新しいソルトとタイムオフセットを生成する。"""
    if granularity not in SHIFT_GRANULARITIES:
        raise SaltError(f"unsupported shift granularity: {granularity!r}")
    if granularity == GRANULARITY_DAY:
        days = _MIN_SHIFT_DAYS + secrets.randbelow(_MAX_SHIFT_DAYS - _MIN_SHIFT_DAYS + 1)
        offset = -days * _SECONDS_PER_DAY
    else:
        weeks = _MIN_SHIFT_WEEKS + secrets.randbelow(_MAX_SHIFT_WEEKS - _MIN_SHIFT_WEEKS + 1)
        offset = -weeks * _SECONDS_PER_WEEK
    return SaltMaterial(
        salt=secrets.token_bytes(32),
        # 過去方向へずらす（未来の日時が出力されると扱いづらいため）
        time_offset_seconds=offset,
        created_at=datetime.now(timezone.utc).isoformat(),
        shift_granularity=granularity,
    )


def load_salt(path: str) -> SaltMaterial:
    """既存のソルトファイルを読み込む。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise SaltError(f"salt file is not valid JSON: {path} ({e.msg})") from e
    except OSError as e:
        raise SaltError(f"cannot read salt file: {path} ({e.strerror})") from e

    if not isinstance(data, dict):
        raise SaltError(f"salt file must contain a JSON object: {path}")
    version = data.get("version")
    if version != SALT_VERSION:
        raise SaltError(f"unsupported salt file version: {version!r} (expected {SALT_VERSION})")
    salt_hex = data.get("salt")
    if not isinstance(salt_hex, str) or len(salt_hex) != 64:
        raise SaltError("salt file field 'salt' must be a 32-byte hex string")
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError as e:
        raise SaltError("salt file field 'salt' is not valid hex") from e
    offset = data.get("time_offset_seconds")
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise SaltError("salt file field 'time_offset_seconds' must be an integer")
    created_at = data.get("created_at")
    if not isinstance(created_at, str):
        raise SaltError("salt file field 'created_at' must be a string")

    granularity = data.get("shift_granularity")
    if granularity is None:
        print(
            f"warning: salt file has no recorded shift granularity ({path}); "
            f"assuming '{GRANULARITY_WEEK}' to preserve consistency with logs "
            "pseudonymized before this field existed",
            file=sys.stderr,
        )
        granularity = GRANULARITY_WEEK
    elif granularity not in SHIFT_GRANULARITIES:
        raise SaltError(f"salt file field 'shift_granularity' has an unsupported value: {granularity!r}")

    return SaltMaterial(
        salt=salt, time_offset_seconds=offset, created_at=created_at, shift_granularity=granularity
    )


def save_salt(path: str, material: SaltMaterial) -> None:
    _write_private_json(path, material.to_json())


def load_or_create_salt(
    path: str, *, granularity: str = DEFAULT_SHIFT_GRANULARITY, quiet: bool = False
) -> tuple[SaltMaterial, bool]:
    """ソルトファイルを読み込む。無ければ生成して 0600 で保存する。

    ``granularity`` は新規生成する場合にのみ使う。既存ファイルの粒度は
    ファイルに記録された値（無ければ ``week``）をそのまま使う。

    戻り値は (ソルト, 新規生成したか)。
    """
    if os.path.exists(path):
        material = load_salt(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077 and not quiet:
            print(
                f"warning: salt file is readable by others (mode {mode:04o}): {path}",
                file=sys.stderr,
            )
        return material, False

    material = generate_salt_material(granularity)
    save_salt(path, material)
    if not quiet:
        print(
            "warning: created a new salt file: "
            f"{path}\n"
            "warning:   このファイルを失うと、過去に仮名化したログとの対応が切れます。\n"
            "warning:   ファイル自体が機密です。リポジトリにコミットしないでください。",
            file=sys.stderr,
        )
    return material, True


def default_salt_path(out_dir: str) -> str:
    return os.path.join(out_dir, DEFAULT_SALT_FILENAME)


def default_map_path(salt_path: str) -> str:
    """マッピングファイルはソルトファイルと同じディレクトリに置く。"""
    return os.path.join(os.path.dirname(os.path.abspath(salt_path)), DEFAULT_MAP_FILENAME)
