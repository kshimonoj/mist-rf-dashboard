"""出力の再スキャン（leak check）。

出力ファイルを書き出す **前** に走らせ、1 件でも検出されたら出力を破棄する。
エラーメッセージには検出した値そのものを絶対に含めない（列名・行番号・規則名のみ）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 生成した仮名 UUID（AP_ID は 1 始まり、SITE_ID は 2 始まり、MAP_ID は 3 始まり）
_PSEUDO_UUID = re.compile(r"^[123]0000000-0000-4000-8000-[0-9]{12}$")
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# コロン／ハイフン区切り MAC と、区切り無しの 12 桁 hex
_MAC_SEPARATED = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}(?![0-9a-fA-F])")
_MAC_BARE = re.compile(r"(?<![0-9a-zA-Z])[0-9a-fA-F]{12}(?![0-9a-zA-Z])")

_IPV4 = re.compile(r"(?<![0-9.])((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?![0-9.])")
_PRIVATE_10 = re.compile(r"^10\.")
_PRIVATE_192 = re.compile(r"^192\.168\.")
_PRIVATE_172 = re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\.")

# MAC を厳密に判定する列（変換対象なので、必ず 02 始まりになっているはず）
MAC_COLUMNS = frozenset({"mac", "ap_mac", "bssid"})

RULE_UUID = "uuid_not_pseudonymized"
RULE_MAC = "mac_not_pseudonymized"
RULE_PRIVATE_IP = "private_ip_not_pseudonymized"
RULE_UNKNOWN_COLUMN = "column_not_in_whitelist"
RULE_NON_ASCII = "non_ascii_character"

ALL_RULES = (RULE_UUID, RULE_MAC, RULE_PRIVATE_IP, RULE_UNKNOWN_COLUMN, RULE_NON_ASCII)


class LeakCheckFailed(RuntimeError):
    """leak check が発火した。出力は破棄される。"""

    def __init__(self, path: str, violations: list["Violation"]) -> None:
        self.path = path
        self.violations = violations
        detail = "\n".join(f"  - {v}" for v in violations[:20])
        more = "" if len(violations) <= 20 else f"\n  ... and {len(violations) - 20} more"
        super().__init__(
            f"leak check failed for {path} ({len(violations)} violation(s)); "
            f"output discarded.\n{detail}{more}"
        )


@dataclass(frozen=True)
class Violation:
    """検出結果。値そのものは保持しない。"""

    rule: str
    column: str
    line: int  # ヘッダを 1 行目とする CSV の行番号

    def __str__(self) -> str:
        return f"rule={self.rule} column={self.column} line={self.line}"


def _is_decimal_only(token: str) -> bool:
    return token.isdigit()


def _mask_allowed_uuids(cell: str) -> str:
    """許可された仮名 UUID を伏せて、後続の MAC 判定の誤検出を防ぐ。"""
    def repl(m: re.Match[str]) -> str:
        return "" if _PSEUDO_UUID.match(m.group(0)) else m.group(0)

    return _UUID.sub(repl, cell)


def check_cell(column: str, cell: str, line: int, allowed_ips: frozenset[str]) -> list[Violation]:
    """1 セルを検査して違反リストを返す。"""
    violations: list[Violation] = []
    if not cell:
        return violations

    # 規則 5: 非 ASCII（日本語の施設名・SSID・ホスト名の残存）
    if any(ord(ch) > 127 for ch in cell):
        violations.append(Violation(RULE_NON_ASCII, column, line))

    # 規則 1: UUID の変換漏れ
    for m in _UUID.finditer(cell):
        if not _PSEUDO_UUID.match(m.group(0)):
            violations.append(Violation(RULE_UUID, column, line))
            break

    scan = _mask_allowed_uuids(cell)

    # 規則 2: 02 以外で始まる MAC（実在 OUI の残存）
    mac_tokens: list[str] = []
    for m in _MAC_SEPARATED.finditer(scan):
        mac_tokens.append(re.sub(r"[:-]", "", m.group(0)).lower())
    for m in _MAC_BARE.finditer(scan):
        token = m.group(0).lower()
        # 12 桁の 10 進数（tx_bytes 等）を MAC と誤認しないようにする。
        # ただし MAC 列は変換対象なので、数字だけでも厳密に判定する。
        if _is_decimal_only(token) and column not in MAC_COLUMNS:
            continue
        mac_tokens.append(token)
    if any(not token.startswith("02") for token in mac_tokens):
        violations.append(Violation(RULE_MAC, column, line))

    # 規則 3: プライベート IP の残存
    for m in _IPV4.finditer(scan):
        token = m.group(1)
        if _PRIVATE_192.match(token) or _PRIVATE_172.match(token):
            violations.append(Violation(RULE_PRIVATE_IP, column, line))
            break
        if _PRIVATE_10.match(token) and token not in allowed_ips:
            violations.append(Violation(RULE_PRIVATE_IP, column, line))
            break

    return violations


def check_output(
    header: list[str],
    rows: list[dict[str, str]],
    *,
    allowed_columns: frozenset[str],
    allowed_ips: frozenset[str],
) -> list[Violation]:
    """出力予定のヘッダと行を検査する。

    allowed_columns には、ホワイトリスト列に加えて
    ``--unknown-column keep`` で明示的に通すことを選んだ列を含める。
    """
    violations: list[Violation] = []

    # 規則 4: ホワイトリストに存在しない列名
    for column in header:
        if column not in allowed_columns:
            violations.append(Violation(RULE_UNKNOWN_COLUMN, column, 1))
        if any(ord(ch) > 127 for ch in column):
            violations.append(Violation(RULE_NON_ASCII, column, 1))

    for i, row in enumerate(rows, start=2):  # 1 行目はヘッダ
        for column in header:
            violations.extend(check_cell(column, row.get(column) or "", i, allowed_ips))

    return violations
