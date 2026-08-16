"""分析結果の保存とローテート（``data/hangap_results/``）。

ジョブの一時ディレクトリは破棄されるため、そのままでは調査の記録が残らない。
このモジュールは ``done`` で完了した分析の出力を専用ディレクトリへ「組」として
複製し、溜まりすぎないようローテートする。

**ローテートの設計（過去に実データを全滅させた欠陥の再発防止）**

``scheduler.rotate_logs`` には「サイズ判定は ``data/logs`` の全ファイルを合計するが、
削除できるのは ``Snapshot`` テーブルに載っている ap_metrics だけ」という食い違いが
あり、他種別が容量を占めた状態でキャップを 2MB 超えただけで ap_metrics が全滅した。

ここではその構造を作らない。

- 見るのも消すのも ``data/hangap_results/`` の**中だけ**。他のディレクトリを
  一切参照しない（``data/logs`` には触れない）。
- 合計サイズは「**削除できるファイル**」＝ 認識できた組のメンバーだけで数える。
  組として認識できないファイルは合計にも入れず、削除もしない。判定対象と削除対象を
  必ず一致させること。
- 削除候補は DB を見ない。ファイル名（タイムスタンプ）だけで決まる。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)

#: 保存先ディレクトリ名。``loader.EXCLUDED_DIR_NAMES`` と一致させること
#: （入力の走査から外れなくなると、次の分析が自分の出力を読み込む）。
RESULTS_DIR_NAME = "hangap_results"

#: 1 組を構成する拡張子。**組を単位に扱う**（xlsx だけ消えて csv が残る状態を作らない）
MEMBER_SUFFIXES: tuple[str, ...] = (".xlsx", ".csv", ".json")

#: 保存名（= 組の名前）。``{name}`` を API のパスで受けるため、この形以外は拒否する
NAME_PATTERN = re.compile(r"^hangap_result_\d{8}_\d{6}$")

NAME_PREFIX = "hangap_result_"
STAMP_FORMAT = "%Y%m%d_%H%M%S"

#: json の書式バージョン（後から項目を足したときに読み手が判断できるようにする）
META_VERSION = 1

DEFAULT_MAX_FILES = 50
DEFAULT_MAX_TOTAL_MB = 500

ENV_MAX_FILES = "HANGAP_RESULTS_MAX_FILES"
ENV_MAX_TOTAL_MB = "HANGAP_RESULTS_MAX_TOTAL_MB"


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


def _env_number(key: str, default: float, *, integer: bool) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        logger.warning(f"hangap archive: {key}={raw!r} を数値として読めません。既定値 {default} を使います")
        return default
    if value <= 0:
        logger.warning(f"hangap archive: {key}={raw!r} は 0 より大きい値が必要です。既定値 {default} を使います")
        return default
    return value


def max_files() -> int:
    """保存する組数の上限（環境変数で上書き可能）。"""
    return int(_env_number(ENV_MAX_FILES, DEFAULT_MAX_FILES, integer=True))


def max_total_bytes() -> int:
    """``data/hangap_results/`` の合計サイズ上限（環境変数で上書き可能）。"""
    return int(_env_number(ENV_MAX_TOTAL_MB, DEFAULT_MAX_TOTAL_MB, integer=False) * 1024 * 1024)


# ---------------------------------------------------------------------------
# 組
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultSet:
    """保存済みの 1 組（xlsx / csv / json）。"""

    name: str
    #: 実在するメンバーだけを持つ（保存が途中で落ちた組も 1 組として扱い、まとめて消す）
    members: dict[str, Path]

    @property
    def total_bytes(self) -> int:
        return sum(_size(p) for p in self.members.values())


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def is_valid_name(name: str) -> bool:
    """``hangap_result_YYYYMMDD_HHMMSS`` か。

    パス区切り・``..``・絶対パスはこの形にマッチしないので、ここを通れば
    ``results_dir / name`` がディレクトリの外を指すことはない。
    """
    return bool(NAME_PATTERN.fullmatch(name))


def name_for(dt: datetime) -> str:
    """組の名前。**分析窓の時刻や入力ファイル名は含めない**（分析条件は中に入っている）。"""
    return f"{NAME_PREFIX}{dt.strftime(STAMP_FORMAT)}"


def unique_name(results_dir: str | Path, dt: datetime) -> str:
    """まだ使われていない組の名前を返す。

    名前の精度は秒なので、同じ秒に 2 回保存すると既存の組を上書きしてしまう。
    その場合は 1 秒ずつ後ろへずらす（名前順 = 古い順という前提も保たれる）。
    """
    root = Path(results_dir)
    candidate = dt
    for _ in range(60):
        name = name_for(candidate)
        if not any((root / f"{name}{s}").exists() for s in MEMBER_SUFFIXES):
            return name
        candidate = candidate + timedelta(seconds=1)
    return name_for(candidate)


def member_path(results_dir: str | Path, name: str, suffix: str) -> Path:
    return Path(results_dir) / f"{name}{suffix}"


def list_sets(results_dir: str | Path) -> list[ResultSet]:
    """保存済みの組を**古い順**に返す。

    走査は ``results_dir`` 直下のみ（``rglob`` は使わない）。名前が
    :data:`NAME_PATTERN` に合わないファイルは組として認識せず、以後一切触らない。
    """
    root = Path(results_dir)
    if not root.is_dir():
        return []
    groups: dict[str, dict[str, Path]] = {}
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix not in MEMBER_SUFFIXES:
            continue
        stem = entry.name[: -len(suffix)]
        if not is_valid_name(stem):
            continue
        groups.setdefault(stem, {})[suffix.lstrip(".")] = entry
    # 名前の日時部分は固定長なので、名前順 = 古い順
    return [ResultSet(name=n, members=groups[n]) for n in sorted(groups)]


def read_meta(result_set: ResultSet) -> dict[str, Any]:
    """組に添えた json を読む。壊れていても一覧を落とさない。"""
    path = result_set.members.get("json")
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"hangap archive: メタ情報を読めません: {path.name}: {e}")
        return {}
    return data if isinstance(data, dict) else {}


#: 一覧が必ず返す項目。json が壊れていても形を崩さない（利用側で分岐を増やさない）
_EMPTY_META: dict[str, Any] = {
    "version": META_VERSION,
    "saved_at": None,
    "detected_intervals": 0,
    "recovery_status": {},
    "neighbor_verdict": {},
    "exodus_suspected": 0,
    "event_matched_intervals": 0,
    "condition_text": "",
    "result_summary_text": "",
    "warning_count": 0,
    "warnings": [],
    "metrics_period": None,
    "events_period": None,
    "ap_count": 0,
    "files_scanned": 0,
}


def saved_at_from_name(name: str) -> str | None:
    """名前のタイムスタンプ（UTC）を ISO 文字列にする。json を読めないときの保険。"""
    try:
        dt = datetime.strptime(name[len(NAME_PREFIX):], STAMP_FORMAT)
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(result_set: ResultSet) -> dict[str, Any]:
    """一覧の 1 要素にする（json の内容 + ファイルサイズ）。"""
    meta = {**_EMPTY_META, **read_meta(result_set)}
    if not meta.get("saved_at"):
        meta["saved_at"] = saved_at_from_name(result_set.name)
    return {
        **meta,
        "name": result_set.name,
        "files": {
            suffix: _size(result_set.members[suffix])
            for suffix in ("xlsx", "csv", "json")
            if suffix in result_set.members
        },
        "total_bytes": result_set.total_bytes,
    }


def list_results(results_dir: str | Path) -> list[dict[str, Any]]:
    """保存済み結果の一覧を**新しい順**で返す。"""
    return [describe(s) for s in reversed(list_sets(results_dir))]


def delete_set(result_set: ResultSet) -> int:
    """1 組をまとめて消す。解放できたバイト数を返す。

    **組の一部だけを消さないこと。** 途中で失敗しても残りのメンバーは消しにいく。
    """
    freed = 0
    for path in result_set.members.values():
        size = _size(path)
        try:
            path.unlink()
        except OSError as e:
            logger.warning(f"hangap archive: 削除できません: {path.name}: {e}")
            continue
        freed += size
    return freed


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------


def build_meta(
    *,
    name: str,
    saved_at: datetime,
    summary: dict[str, Any],
    warnings: Sequence[str],
) -> dict[str, Any]:
    """json に書くメタ情報を組み立てる。

    ファイル名から復元できない情報（検出区間数・内訳・分析条件・警告件数・データ期間）
    だけを持たせる。結果テーブル本体は xlsx / csv 側にある。
    """
    loader_info = summary.get("loader") or {}
    return {
        "version": META_VERSION,
        "name": name,
        "saved_at": saved_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "detected_intervals": summary.get("detected_intervals", 0),
        "recovery_status": summary.get("recovery_status", {}),
        "neighbor_verdict": summary.get("neighbor_verdict", {}),
        "exodus_suspected": summary.get("exodus_suspected", 0),
        "event_matched_intervals": summary.get("event_matched_intervals", 0),
        "condition_text": summary.get("condition_text", ""),
        "result_summary_text": summary.get("result_summary_text", ""),
        "warning_count": len(warnings),
        "warnings": list(warnings),
        "metrics_period": loader_info.get("metrics_period"),
        "events_period": loader_info.get("events_period"),
        "ap_count": loader_info.get("ap_count", 0),
        "files_scanned": loader_info.get("files_scanned", 0),
    }


def save(
    results_dir: str | Path,
    name: str,
    sources: dict[str, Path],
    meta: dict[str, Any],
) -> ResultSet:
    """出力ファイルを組として保存する。

    ``sources`` はジョブが**すでに書き出した** xlsx / csv のパス。書式を再実装せず
    コピーするだけにしてあるのは、ダウンロードで受け取るファイルと保存されるファイルを
    確実に同一にするため。
    """
    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    members: dict[str, Path] = {}
    for suffix in ("xlsx", "csv"):
        src = sources.get(suffix)
        if src is None or not Path(src).is_file():
            continue
        dst = root / f"{name}.{suffix}"
        shutil.copyfile(src, dst)
        members[suffix] = dst
    json_path = root / f"{name}.json"
    json_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    members["json"] = json_path
    return ResultSet(name=name, members=members)


# ---------------------------------------------------------------------------
# ローテート
# ---------------------------------------------------------------------------


def rotate(
    results_dir: str | Path,
    *,
    keep_files: int | None = None,
    keep_bytes: int | None = None,
) -> tuple[int, int]:
    """組数・合計サイズの上限を超えた分を、古い組から**組ごと**削除する。

    :returns: ``(削除した組数, 解放したバイト数)``

    - 見るのも消すのも ``results_dir`` 直下だけ。他のディレクトリは参照しない。
    - 合計サイズは「組として認識できたファイル」だけで数える。数える対象と消せる
      対象が同じなので、``rotate_logs`` のように「消しても下回れず全滅する」状態に
      ならない。
    - **最新の 1 組は必ず残す。** 上限を極端に小さくしても消さない。
    """
    limit_files = max_files() if keep_files is None else keep_files
    limit_bytes = max_total_bytes() if keep_bytes is None else keep_bytes

    sets = list_sets(results_dir)  # 古い順
    total = sum(s.total_bytes for s in sets)
    count = len(sets)

    removed = 0
    freed = 0
    for result_set in sets[:-1]:  # 最新の 1 組は候補から外す
        if count <= limit_files and total <= limit_bytes:
            break
        size = result_set.total_bytes
        freed += delete_set(result_set)
        total -= size
        count -= 1
        removed += 1

    if removed:
        logger.info(
            f"[HANGAP-ROTATE] Deleted {removed} result set(s), freed {freed}B "
            f"in {results_dir} (limits: {limit_files} sets / {limit_bytes}B; "
            f"now {count} sets / {total}B)"
        )
    return removed, freed
