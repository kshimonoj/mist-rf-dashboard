"""フロア名の解決（``floormap_*_summary.csv`` → ``map_id`` / ``ap_name`` → フロア名）。

**なぜ ap_metrics だけでは足りないか**

``ap_metrics`` は ``map_id``（フロア図の ID）を持つが、人が読めるフロア名
（``map_name``）は持たない。``ap_metrics`` の CSV スキーマは Hang AP 分析と
仮名化がヘッダー完全一致で種別を判定しているため **変更しない**。フロア名は
毎正時に収集している ``floormap_*_summary.csv`` から解決する。

**なぜ最優先は map_id で判定するか**

``floormap_summary`` の 1 行は (map_name × band × channel) 単位で、``ap_list`` は
その band/channel に載っている AP だけを含む。全無線が停止している AP は
どの行の ``ap_list`` にも現れないので、``ap_name`` の突合だけでは
フロアに載らない。``ap_list`` からはまず ``map_id → map_name`` の対応を作り、
**個々の AP のフロアは ap_metrics の map_id で決める**。こうすると、
無線が止まっていて floormap に出てこない AP も正しいフロアに載る。

**なぜ ap_name 経由のフォールバックが要るか**

座標列（``map_id`` / ``x_m`` / ``y_m``）を追加する前の 33 列版 ``ap_metrics``
（``ap_metrics_v1``）は ``map_id`` を持たない。この期間がピークに選ばれると
map_id 判定だけでは全 AP が未割当になってしまうが、``ap_list`` から作った
``ap_name → map_name`` の対応表そのものは ``map_id`` を必要としない。そこで
map_id で引けない AP は ``ap_name`` で直接引く（:meth:`FloorResolution.floor_of`）。
判定順は必ず「map_id → ap_name → 未割当」。map_id を先に試すのは、上記の
「全無線停止 AP を救う」利点を失わないため。

``floormap_ap_detail`` は定期収集されていない（単発の手動エクスポートのみ）ため、
任意時点の解決には使えない。

ネットワークアクセス・LLM 呼び出しは行わない。入力はローカルファイルのみ。
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from pseudonymizer.schemas import detect_file_type

#: フロアが決まらなかった AP の行き先。**除外しない**（台数が合わなくなる）
UNASSIGNED = "（未割当）"

#: ``floormap_<YYYYMMDD>_<HHMM|HHMMSS>_<TZ>[_manual]_summary.csv``
#: 種別判定はヘッダーで行うが、1,000 本規模のファイルを全部開くわけにはいかないので、
#: **候補の絞り込みだけ** ファイル名で行う（選んだ 1 本は必ずヘッダーで検証する）。
_NAME_RE = re.compile(
    r"^floormap_(?P<date>\d{8})_(?P<time>\d{4}|\d{6})(?:_[A-Za-z0-9]+)*_summary$",
    re.IGNORECASE,
)

#: これ以上離れた floormap しか無ければフロア名を解決しない（全 AP を未割当にする）
MAX_OFFSET_SECONDS: float = 24 * 3600.0

#: ファイル名の日時と中身の timestamp がこれ以上ずれていたら警告する
_CONTENT_MISMATCH_SECONDS: float = 3600.0

#: ヘッダー検証に失敗したファイルを読み飛ばす上限（壊れた 1 本で解決を諦めない）
_MAX_CANDIDATES: int = 5


@dataclass
class FloorResolution:
    """フロア名の解決結果。"""

    #: ``map_id`` → フロア名。ここに無い map_id は :data:`UNASSIGNED` になる
    map_id_to_name: dict[str, str] = field(default_factory=dict)
    #: ``ap_name`` → フロア名（``floormap_summary`` の ``ap_list`` から直接引く）。
    #: ``map_id`` で引けない AP（33 列版 ap_metrics 等）のフォールバックに使う
    ap_name_to_floor: dict[str, str] = field(default_factory=dict)
    #: 使った floormap ファイル名（解決できなければ None）
    source_file: str | None = None
    #: そのファイルの中身の timestamp
    source_timestamp: pd.Timestamp | None = None
    #: ピーク時点とのずれ（秒）。**必ず表示する**（古い構成で見ている可能性がある）
    offset_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)
    #: 異常ではなく正常動作の補足（例: ap_name 経由のフォールバックで解決できた）。
    #: **警告とは別チャンネル**（毎回・条件次第で起きるだけなので、警告として
    #: 目立たせると本当に読むべき警告が埋もれる）
    notes: list[str] = field(default_factory=list)

    def floor_of(self, map_id: object, ap_name: object = None) -> str:
        """AP のフロア名。

        判定順（必ずこの順）:

        1. ``map_id`` → :attr:`map_id_to_name`
        2. 上で引けなければ ``ap_name`` → :attr:`ap_name_to_floor`
           （座標列を追加する前の 33 列版 ap_metrics は map_id を持たないため）
        3. どちらでも引けなければ :data:`UNASSIGNED`
        """
        key = _text(map_id)
        if key:
            hit = self.map_id_to_name.get(key)
            if hit is not None:
                return hit
        name = _text(ap_name)
        if name:
            hit = self.ap_name_to_floor.get(name)
            if hit is not None:
                return hit
        return UNASSIGNED


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def parse_name_timestamp(path: Path) -> datetime | None:
    """ファイル名の日時トークンを返す。floormap summary でなければ None。"""
    m = _NAME_RE.match(path.stem)
    if m is None:
        return None
    token = m.group("date") + m.group("time")
    fmt = "%Y%m%d%H%M" if len(m.group("time")) == 4 else "%Y%m%d%H%M%S"
    try:
        return datetime.strptime(token, fmt)
    except ValueError:
        return None


def summary_candidates(files: Sequence[Path], at: pd.Timestamp) -> list[tuple[float, Path, datetime]]:
    """``at`` に近い順の ``floormap_*_summary.csv`` 候補（ずれ秒, パス, 名前の日時）。"""
    out: list[tuple[float, Path, datetime]] = []
    for path in files:
        if path.suffix.lower() != ".csv":
            continue
        stamp = parse_name_timestamp(path)
        if stamp is None:
            continue
        offset = abs((pd.Timestamp(stamp) - at).total_seconds())
        out.append((offset, path, stamp))
    # ずれが同じなら名前順（決定論的に選ぶ）
    out.sort(key=lambda t: (t[0], t[1].name))
    return out


def _read_summary(path: Path) -> list[dict[str, str]] | None:
    """1 本の floormap summary を読む。ヘッダーが一致しなければ None。"""
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return None
            columns = [c.strip() for c in header]
            ft = detect_file_type(columns)
            if ft is None or ft.key != "floormap_summary":
                return None
            return [dict(zip(columns, row)) for row in reader if row]
    except OSError:
        return None


def resolve_floors(
    files: Sequence[Path],
    site_name: str,
    at: pd.Timestamp,
    metrics_at_peak: pd.DataFrame,
) -> FloorResolution:
    """ピーク時点のフロア構成を解決する。

    :param files: 走査対象のファイル一覧（**ジョブ開始時に確定させたもの**。
        分析の途中で定期収集がファイルを足しても結果が揺れないようにする。
        ディレクトリではなく一覧を受けるのはこのため）
    :param site_name: 対象サイト名（``floormap_summary`` に site_id は無い）
    :param at: ピーク時点
    :param metrics_at_peak: ピーク時点の ap_metrics（``ap_name`` / ``map_id`` を使う）

    - ``floormap_summary`` は **1 本だけ** 読む（毎正時収集なのでずれは最大 30 分、
      フロア構成は分単位で変わらない）。
    - ``at`` から 24 時間以上離れた floormap しか無ければ解決しない（全 AP を未割当）。
    """
    resolution = FloorResolution()
    candidates = summary_candidates(files, at)
    if not candidates:
        resolution.warnings.append(
            "floormap_*_summary.csv が 1 本もありません。"
            f"すべての AP を「{UNASSIGNED}」として扱います"
        )
        return resolution

    offset, path, name_stamp = candidates[0]
    if offset > MAX_OFFSET_SECONDS:
        resolution.warnings.append(
            f"ピーク時点 {_fmt(at)} に最も近い floormap は {path.name} で、"
            f"{offset / 3600:.1f} 時間離れています"
            f"（上限 {MAX_OFFSET_SECONDS / 3600:g} 時間）。フロア名を解決せず、"
            f"すべての AP を「{UNASSIGNED}」として扱います"
        )
        return resolution

    rows: list[dict[str, str]] | None = None
    for cand_offset, cand_path, cand_stamp in candidates[:_MAX_CANDIDATES]:
        if cand_offset > MAX_OFFSET_SECONDS:
            break
        rows = _read_summary(cand_path)
        if rows is not None:
            offset, path, name_stamp = cand_offset, cand_path, cand_stamp
            break
        resolution.warnings.append(
            f"floormap として読めないファイルを読み飛ばしました: {cand_path.name}"
        )
    if rows is None:
        resolution.warnings.append(
            f"読み込める floormap_*_summary.csv がありません。"
            f"すべての AP を「{UNASSIGNED}」として扱います"
        )
        return resolution

    resolution.source_file = path.name

    # 中身の timestamp で検証する（ファイル名の日時と食い違ったら中身を信じる）
    content_ts = _content_timestamp(rows)
    if content_ts is not None:
        drift = abs((content_ts - pd.Timestamp(name_stamp)).total_seconds())
        if drift > _CONTENT_MISMATCH_SECONDS:
            resolution.warnings.append(
                f"{path.name} のファイル名の日時（{_fmt(name_stamp)}）と中身の timestamp"
                f"（{_fmt(content_ts)}）が {drift / 3600:.1f} 時間ずれています。中身の値を使います"
            )
        resolution.source_timestamp = content_ts
        offset = abs((content_ts - at).total_seconds())
    else:
        resolution.source_timestamp = pd.Timestamp(name_stamp)
    resolution.offset_seconds = float(offset)

    site_rows = [r for r in rows if _text(r.get("site_name")) == _text(site_name)]
    if not site_rows:
        available = sorted({_text(r.get("site_name")) for r in rows if _text(r.get("site_name"))})
        resolution.warnings.append(
            f"{path.name} に対象サイト {site_name!r} の行がありません"
            f"（含まれるサイト: {', '.join(available) or 'なし'}）。"
            f"すべての AP を「{UNASSIGNED}」として扱います"
        )
        return resolution

    # ap_name → map_name。同じ AP が 2.4G の行と 5G の行の両方に出るが行き先は同じ。
    # **band / channel では絞らない**（絞ると片方のバンドしか載っていない AP を落とす）。
    ap_to_floor: dict[str, str] = {}
    for row in site_rows:
        map_name = _text(row.get("map_name"))
        if not map_name:
            continue
        for ap_name in _text(row.get("ap_list")).split(","):
            name = ap_name.strip()
            if name:
                ap_to_floor.setdefault(name, map_name)

    resolution.map_id_to_name = _map_id_to_name(metrics_at_peak, ap_to_floor, resolution.warnings)
    resolution.ap_name_to_floor = ap_to_floor

    notes, fallback_warnings = _fallback_messages(metrics_at_peak, resolution)
    resolution.notes.extend(notes)
    resolution.warnings.extend(fallback_warnings)
    return resolution


def _fallback_messages(
    metrics_at_peak: pd.DataFrame, resolution: FloorResolution
) -> tuple[list[str], list[str]]:
    """フロア解決の結果に応じたメッセージ（注記 / 警告）を組み立てる。

    - ``map_id`` が空で ``ap_name`` 経由のフォールバックで解決できた AP →
      **これは異常ではなく正常動作**（33 列版 ap_metrics の期間で必ず起きる）
      なので、警告ではなく :attr:`FloorResolution.notes`（分析条件の補足）に出す
    - ``map_id`` が空のまま解決できなかった AP → 従来どおり警告。ただし原因は
      「33 列版だから」ではなく「``floormap_summary`` の ``ap_list`` にその
      AP 名が無いから」なので、その事実を書く（33 列版でも ap_list に載って
      いれば ap_name 経由で解決できる）
    - ``map_id`` はあるがどのフロアにも紐付かない AP → 現行のまま変更しない
    """
    notes: list[str] = []
    warnings: list[str] = []
    blank_resolved = 0
    blank_unresolved = 0
    unmapped = 0
    for map_id, ap_name in zip(
        metrics_at_peak.get("map_id", []), metrics_at_peak.get("ap_name", [])
    ):
        mid = _text(map_id)
        if mid and mid in resolution.map_id_to_name:
            continue  # map_id で解決
        name = _text(ap_name)
        if name and name in resolution.ap_name_to_floor:
            if not mid:
                blank_resolved += 1
            continue  # ap_name のフォールバックで解決
        if mid:
            unmapped += 1
        else:
            blank_unresolved += 1

    if blank_resolved:
        notes.append(
            f"map_id を持たない AP が {blank_resolved} 台ありましたが、"
            "floormap_summary の AP 名からフロアを特定しました"
            "（座標列を追加する前の 33 列版 ap_metrics の期間）"
        )
    if blank_unresolved:
        warnings.append(
            f"map_id を持たない AP が {blank_unresolved} 台あります。"
            "floormap_summary の AP 一覧（ap_list）に該当する AP 名が無いため、"
            f"フロアを特定できませんでした。「{UNASSIGNED}」として結果に残しています"
        )
    if unmapped:
        warnings.append(
            f"map_id はあるがどのフロアにも紐付かない AP が {unmapped} 台あります"
            f"（floormap に載っていない AP）。「{UNASSIGNED}」として結果に残しています"
        )
    return notes, warnings


def _map_id_to_name(
    metrics_at_peak: pd.DataFrame,
    ap_to_floor: dict[str, str],
    warnings: list[str],
) -> dict[str, str]:
    """ピーク時点の AP から ``map_id → map_name`` を導く。

    同じ ``map_id`` を持つ AP 群が指す ``map_name`` は 1 つのはず。複数あれば
    データ側の異常なので、**最多のものを採り、警告に列挙する**。
    """
    votes: dict[str, dict[str, int]] = {}
    if "map_id" not in metrics_at_peak.columns or "ap_name" not in metrics_at_peak.columns:
        return {}
    for map_id, ap_name in zip(metrics_at_peak["map_id"], metrics_at_peak["ap_name"]):
        key = _text(map_id)
        if not key:
            continue
        floor = ap_to_floor.get(_text(ap_name))
        if not floor:
            continue
        votes.setdefault(key, {})
        votes[key][floor] = votes[key].get(floor, 0) + 1

    out: dict[str, str] = {}
    for map_id, counts in sorted(votes.items()):
        # 最多 → 同数なら名前順（決定論的に選ぶ）
        best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out[map_id] = best
        if len(counts) > 1:
            detail = ", ".join(f"{name}({n}台)" for name, n in sorted(counts.items()))
            warnings.append(
                f"map_id={map_id} に複数のフロア名が対応しています（{detail}）。"
                f"最多の「{best}」を採用しました"
            )
    return out


def _content_timestamp(rows: Sequence[dict[str, str]]) -> pd.Timestamp | None:
    for row in rows:
        raw = _text(row.get("timestamp"))
        if not raw:
            continue
        ts = pd.to_datetime(raw, errors="coerce")
        if not pd.isna(ts):
            return pd.Timestamp(ts)
    return None


def _fmt(ts: object) -> str:
    if ts is None:
        return "-"
    try:
        if pd.isna(ts):
            return "-"
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
