"""変換型ごとの実装と、決定論的な採番エンジン。

採番は「変換型ごとに独立した名前空間で 1 から始まる連番」を割り当てる。
新しい値には ``HMAC-SHA256(salt, "<変換型>:<値>")`` の昇順で番号を配る。
割り当て結果はマッピングファイルへ永続化し、実行をまたいで再利用する
（別のファイル・別のバッチを後から仮名化しても同じ仮名になるようにするため）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .salt import SaltMaterial, _write_private_json
from .schemas import AP_IDENTITY_TYPES, FileType, TransformType as T, ap_link_columns

MAP_VERSION = 1

_MAC_SEP = re.compile(r"[:\-]")

# タイムスタンプの出力形式は入力に合わせる（Mist Dashboard は "%Y-%m-%d %H:%M:%S"）
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")

# AP_ID / AP_NAME / AP_MAC は同一 AP なら番号を揃えたいので、番号空間を共有する。
NAMESPACES: dict[str, tuple[T, ...]] = {
    "AP": AP_IDENTITY_TYPES,
    "SITE_ID": (T.SITE_ID,),
    "SITE_NAME": (T.SITE_NAME,),
    "CLIENT_MAC": (T.CLIENT_MAC,),
    "HOSTNAME": (T.HOSTNAME,),
    "IP": (T.IP,),
    "SSID": (T.SSID,),
    "MAP_NAME": (T.MAP_NAME,),
    "MAP_ID": (T.MAP_ID,),
    "VLAN": (T.VLAN,),
}
NAMESPACE_OF: dict[T, str] = {
    t: ns for ns, types in NAMESPACES.items() for t in types
}

# 採番して仮名を作る変換型（PASSTHROUGH / TIMESTAMP / AP_NAME_LIST を除く）
NUMBERED_TYPES: frozenset[T] = frozenset(NAMESPACE_OF)


class PseudonymizeError(RuntimeError):
    """仮名化処理を続行できない。"""


# ---------------------------------------------------------------------------
# 値の正規化と出力形式
# ---------------------------------------------------------------------------

def normalize_value(ttype: T, raw: str) -> str:
    """照合用にキーを正規化する。MAC はコロンなし小文字（プロジェクト規約）。"""
    value = raw.strip()
    if ttype in (T.AP_MAC, T.CLIENT_MAC):
        return _MAC_SEP.sub("", value).lower()
    return value


def format_pseudonym(ttype: T, idx: int) -> str:
    """変換型と連番から仮名文字列を生成する。"""
    if ttype is T.SITE_ID:
        return f"20000000-0000-4000-8000-{idx:012d}"
    if ttype is T.AP_ID:
        return f"10000000-0000-4000-8000-{idx:012d}"
    if ttype is T.SITE_NAME:
        return f"SITE_{idx:03d}"
    if ttype is T.AP_NAME:
        return f"AP_{idx:04d}"
    if ttype is T.HOSTNAME:
        return f"HOST_{idx:04d}"
    if ttype is T.SSID:
        return f"SSID_{idx:03d}"
    if ttype is T.MAP_NAME:
        return f"FLOOR_{idx:03d}"
    if ttype is T.MAP_ID:
        # SITE_ID（2 始まり）・AP_ID（1 始まり）とプレフィックスが衝突しない独立の名前空間。
        return f"30000000-0000-4000-8000-{idx:012d}"
    if ttype is T.AP_MAC:
        # 02 + 系列識別子 0 + 連番。AP_0200 の MAC は ...00c8 で末尾が揃う。
        return f"020{idx:09x}"
    if ttype is T.CLIENT_MAC:
        # AP_MAC とは別系列（系列識別子 1）。仮名同士が衝突しないようにする。
        return f"021{idx:09x}"
    if ttype is T.IP:
        return f"10.{(idx >> 16) & 0xFF}.{(idx >> 8) & 0xFF}.{idx & 0xFF}"
    if ttype is T.VLAN:
        return str(idx)
    raise PseudonymizeError(f"no pseudonym format for transform type {ttype}")


def _hash_key(salt: bytes, ttype: T, value: str) -> str:
    return hmac.new(
        salt, f"{ttype.value}:{value}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


# ---------------------------------------------------------------------------
# タイムシフト
# ---------------------------------------------------------------------------

def shift_timestamp(raw: str, offset_seconds: int) -> str:
    """タイムスタンプ文字列を offset_seconds だけずらす。形式は入力を維持する。"""
    value = raw.strip()
    if not value:
        return raw
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return (dt + timedelta(seconds=offset_seconds)).strftime(fmt)

    iso = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as e:
        raise PseudonymizeError(f"unparseable timestamp format: {e}") from e
    shifted = dt + timedelta(seconds=offset_seconds)
    out = shifted.isoformat()
    if value.endswith(("Z", "z")):
        out = out.replace("+00:00", "Z")
    return out


# ---------------------------------------------------------------------------
# マッピング（採番結果の永続化）
# ---------------------------------------------------------------------------

@dataclass
class MappingStore:
    """変換型ごとの「元の値 → 連番」を保持する。"""

    salt_fingerprint: str
    assignments: dict[T, dict[str, int]] = field(default_factory=dict)
    dirty: bool = False

    def get(self, ttype: T, value: str) -> int | None:
        return self.assignments.get(ttype, {}).get(value)

    def put(self, ttype: T, value: str, idx: int) -> None:
        self.assignments.setdefault(ttype, {})[value] = idx
        self.dirty = True

    def used_indices(self, namespace: str) -> set[int]:
        used: set[int] = set()
        for ttype in NAMESPACES[namespace]:
            used.update(self.assignments.get(ttype, {}).values())
        return used

    def to_json(self) -> dict:
        return {
            "version": MAP_VERSION,
            "salt_fingerprint": self.salt_fingerprint,
            "assignments": {
                ttype.value: dict(sorted(values.items()))
                for ttype, values in sorted(
                    self.assignments.items(), key=lambda kv: kv[0].value
                )
                if values
            },
        }


def load_mapping(path: str, material: SaltMaterial) -> MappingStore:
    """マッピングファイルを読み込む。無ければ空のストアを返す。"""
    store = MappingStore(salt_fingerprint=material.fingerprint)
    if not os.path.exists(path):
        return store
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise PseudonymizeError(f"cannot read mapping file: {path} ({e})") from e
    if not isinstance(data, dict) or data.get("version") != MAP_VERSION:
        raise PseudonymizeError(
            f"unsupported mapping file version in {path}: {data.get('version')!r}"
        )
    if data.get("salt_fingerprint") != material.fingerprint:
        raise PseudonymizeError(
            f"mapping file {path} was created with a different salt; "
            "使い続けると仮名の一貫性が壊れます。正しいソルトを --salt-file で指定してください。"
        )
    raw_assignments = data.get("assignments") or {}
    for type_name, values in raw_assignments.items():
        try:
            ttype = T(type_name)
        except ValueError as e:
            raise PseudonymizeError(f"unknown transform type in mapping file: {type_name}") from e
        store.assignments[ttype] = {str(k): int(v) for k, v in values.items()}
    return store


def save_mapping(path: str, store: MappingStore) -> None:
    _write_private_json(path, store.to_json())


# ---------------------------------------------------------------------------
# Union-Find（AP 同一性のグルーピング）
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[tuple[T, str], tuple[T, str]] = {}

    def add(self, key: tuple[T, str]) -> None:
        self._parent.setdefault(key, key)

    def find(self, key: tuple[T, str]) -> tuple[T, str]:
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, a: tuple[T, str], b: tuple[T, str]) -> None:
        self.add(a)
        self.add(b)
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def groups(self) -> list[list[tuple[T, str]]]:
        buckets: dict[tuple[T, str], list[tuple[T, str]]] = defaultdict(list)
        for key in self._parent:
            buckets[self.find(key)].append(key)
        return list(buckets.values())


# ---------------------------------------------------------------------------
# 仮名化エンジン
# ---------------------------------------------------------------------------

@dataclass
class TransformStats:
    """dry-run 表示用の集計。"""

    cells_by_column: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    distinct_by_type: dict[T, int] = field(default_factory=dict)


class Pseudonymizer:
    """CSV 行の仮名化を行う。

    使い方: observe_row() で全入力を走査 → build() → transform_row()。
    """

    def __init__(
        self,
        material: SaltMaterial,
        mapping: MappingStore,
        *,
        keep_vlan: bool = False,
        time_shift: bool = True,
        warn: callable = lambda msg: print(f"warning: {msg}", file=sys.stderr),
    ) -> None:
        self._salt = material.salt
        self._offset = material.time_offset_seconds if time_shift else 0
        self._keep_vlan = keep_vlan
        self._mapping = mapping
        self._warn = warn
        self._observed: dict[T, set[str]] = defaultdict(set)
        self._links: list[list[tuple[T, str]]] = []
        self._built = False
        self.stats = TransformStats()

    # -- 収集フェーズ ------------------------------------------------------

    def observe_row(self, ft: FileType, row: dict[str, str]) -> None:
        """1 行から採番対象の値を収集する。"""
        link_cols = ap_link_columns(ft)
        link_keys: list[tuple[T, str]] = []
        for column, raw in row.items():
            rule = ft.rule_for(column)
            if rule is None:
                continue  # 未知の列は cli 側で処理済み
            if rule is T.AP_NAME_LIST:
                for element in _split_list(raw):
                    self._observe_value(T.AP_NAME, element)
                continue
            if rule not in NUMBERED_TYPES:
                continue
            if rule is T.VLAN and self._keep_vlan:
                continue
            key = self._observe_value(rule, raw)
            if key is not None and column in link_cols:
                link_keys.append(key)
        if len(link_keys) >= 2:
            self._links.append(link_keys)

    def _observe_value(self, ttype: T, raw: str) -> tuple[T, str] | None:
        value = normalize_value(ttype, raw or "")
        if not value:
            return None
        self._observed[ttype].add(value)
        return (ttype, value)

    # -- 採番フェーズ ------------------------------------------------------

    def build(self) -> None:
        """収集した値に連番を割り当てる。"""
        if self._built:
            return
        self._assign_ap_namespace()
        for ttype, values in self._observed.items():
            if NAMESPACE_OF.get(ttype) == "AP":
                continue
            self._assign_simple(ttype, values)
        for ttype, values in self._observed.items():
            self.stats.distinct_by_type[ttype] = len(values)
        self._built = True

    def _next_free(self, used: set[int], cursor: int) -> tuple[int, int]:
        while cursor in used:
            cursor += 1
        return cursor, cursor + 1

    def _assign_simple(self, ttype: T, values: set[str]) -> None:
        pending = sorted(
            (v for v in values if self._mapping.get(ttype, v) is None),
            key=lambda v: _hash_key(self._salt, ttype, v),
        )
        if not pending:
            return
        namespace = NAMESPACE_OF[ttype]
        used = self._mapping.used_indices(namespace)
        cursor = 1
        for value in pending:
            idx, cursor = self._next_free(used, cursor)
            used.add(idx)
            self._mapping.put(ttype, value, idx)

    def _assign_ap_namespace(self) -> None:
        """AP_ID / AP_NAME / AP_MAC を同一 AP 単位でまとめて採番する。"""
        uf = _UnionFind()
        for ttype in AP_IDENTITY_TYPES:
            for value in self._observed.get(ttype, ()):  # 単独出現も 1 グループ
                uf.add((ttype, value))
        for keys in self._links:
            ap_keys = [k for k in keys if k[0] in AP_IDENTITY_TYPES]
            for other in ap_keys[1:]:
                uf.union(ap_keys[0], other)

        groups: list[list[tuple[T, str]]] = []
        for group in uf.groups():
            by_type: dict[T, set[str]] = defaultdict(set)
            for ttype, value in group:
                by_type[ttype].add(value)
            if any(len(v) > 1 for v in by_type.values()):
                # 同じグループに同じ型の値が複数 = 対応が取れていない。
                # 仮名が衝突しないよう、グループを解体して独立に採番する。
                self._warn(
                    "AP identity linkage is inconsistent "
                    f"({', '.join(f'{t.value}x{len(v)}' for t, v in by_type.items())}); "
                    "falling back to independent numbering for this group"
                )
                groups.extend([[member] for member in group])
            else:
                groups.append(group)

        used = self._mapping.used_indices("AP")
        pending: list[list[tuple[T, str]]] = []
        for group in groups:
            existing = {
                self._mapping.get(t, v)
                for t, v in group
                if self._mapping.get(t, v) is not None
            }
            if len(existing) == 1:
                idx = existing.pop()
                for ttype, value in group:
                    if self._mapping.get(ttype, value) is None:
                        self._mapping.put(ttype, value, idx)
            elif not existing:
                pending.append(group)
            else:
                # 既に別々の番号が振られている値が同じ AP としてリンクされた。
                # 過去の仮名との対応を壊さないため、既存の番号を尊重する。
                self._warn(
                    "AP identity spans multiple previously assigned numbers; "
                    "keeping the existing assignments"
                )
                for member in group:
                    if self._mapping.get(*member) is None:
                        pending.append([member])

        pending.sort(key=lambda g: min(_hash_key(self._salt, t, v) for t, v in g))
        cursor = 1
        for group in pending:
            idx, cursor = self._next_free(used, cursor)
            used.add(idx)
            for ttype, value in group:
                self._mapping.put(ttype, value, idx)

    # -- 変換フェーズ ------------------------------------------------------

    def pseudonym(self, ttype: T, raw: str) -> str:
        value = normalize_value(ttype, raw or "")
        if not value:
            return ""
        idx = self._mapping.get(ttype, value)
        if idx is None:
            raise PseudonymizeError(
                f"value of transform type {ttype.value} was not registered before build()"
            )
        return format_pseudonym(ttype, idx)

    def transform_value(self, ttype: T, raw: str) -> str:
        if ttype is T.PASSTHROUGH:
            return raw
        if ttype is T.TIMESTAMP:
            return shift_timestamp(raw, self._offset) if self._offset else raw
        if ttype is T.AP_NAME_LIST:
            elements = _split_list(raw)
            if not elements:
                return raw
            return ",".join(self.pseudonym(T.AP_NAME, e) for e in elements)
        if ttype is T.VLAN and self._keep_vlan:
            return raw
        return self.pseudonym(ttype, raw)

    def transform_row(self, ft: FileType, row: dict[str, str]) -> dict[str, str]:
        """1 行を仮名化する。未知の列（cli で keep 指定）はそのまま通す。"""
        if not self._built:
            raise PseudonymizeError("build() must be called before transform_row()")
        out: dict[str, str] = {}
        for column, raw in row.items():
            rule = ft.rule_for(column)
            if rule is None:
                out[column] = raw
                continue
            value = self.transform_value(rule, raw)
            out[column] = value
            if rule not in (T.PASSTHROUGH,) and (raw or "").strip():
                self.stats.cells_by_column[column] += 1
        return out

    # -- 検証用の補助情報 --------------------------------------------------

    @property
    def generated_ips(self) -> set[str]:
        """生成した仮名 IP の集合（leak check の除外に使う）。"""
        return {
            format_pseudonym(T.IP, idx)
            for idx in self._mapping.assignments.get(T.IP, {}).values()
        }

    @property
    def time_offset_seconds(self) -> int:
        return self._offset

    @property
    def mapping(self) -> MappingStore:
        return self._mapping


def _split_list(raw: str) -> list[str]:
    """カンマ区切りの複数値を分割する（空要素は捨てる）。"""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]
