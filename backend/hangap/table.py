"""結果テーブル（:data:`detector.RESULT_COLUMNS`）の列の性質と、行の絞り込み。

**列の定義（どの列があるか・どの順で並ぶか・どう書式化するか）は
:mod:`hangap.detector` が持つ。** このモジュールはその列に対して
「どう絞り込めるか」だけを足すものであり、列を増やしたり並べ替えたり
書式を変えたりはしない。

ここに置く理由は 2 つある。

- 絞り込みは **サーバ側で** 行う必要がある（ページングと併用するため、
  表示中のページだけをクライアントで絞ると件数も次ページも壊れる）。
- 実行中ジョブの結果（メモリ上の DataFrame）と保存済みの結果（csv から
  読み戻した DataFrame）で、同じコードが同じように効かなければならない。
  そのため csv の読み戻し（:func:`read_result_csv`）もここに置き、
  :func:`detector._to_frame` と同じ dtype に揃える。

ダウンロード（xlsx / csv）は絞り込みの影響を受けない。ダウンロードは分析時に
書き出したファイルをそのまま返すので、このモジュールを通らない。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from .analysis import STATUS_ORDER, VERDICT_ORDER, ParamError, parse_time
from .detector import RESULT_COLUMNS

# ---------------------------------------------------------------------------
# 列の性質
# ---------------------------------------------------------------------------

#: 部分一致で絞り込む列
KIND_TEXT = "text"
#: 取りうる値が限られる列（値の選択。複数選択は OR）
KIND_ENUM = "enum"
#: 下限・上限で絞り込む列
KIND_NUMBER = "number"
#: 開始・終了で絞り込む列
KIND_TIME = "time"
#: 3 状態（指定なし / True / False）で絞り込む列
KIND_BOOL = "bool"

TIME_COLUMNS: tuple[str, ...] = ("ゼロ直前時刻", "ゼロ開始", "ゼロ終了", "回復時刻")

#: 整数として扱う列（``detector._to_frame`` の Int64 列と一致させること）
INT_COLUMNS: tuple[str, ...] = (
    "区間番号", "AP内区間数", "直前clients", "直後clients（回復時）",
    "連続ゼロ回数", "AP最大clients",
    "サイト合計clients(ゼロ開始時)", "サイト合計clients(ゼロ終了時)",
    "周辺AP数", "周辺AP RF隣接数", "周辺AP実測なし数",
)

#: 小数として扱う列（``detector._to_frame`` の float 列と一致させること）
FLOAT_COLUMNS: tuple[str, ...] = ("サイト全体変化率", "周辺AP端末数合計")

BOOL_COLUMNS: tuple[str, ...] = ("退場疑い",)

#: 値の選択で絞り込む列と、その選択肢。**フロント側で定義し直さないため API で返す。**
ENUM_CHOICES: dict[str, tuple[str, ...]] = {
    "回復状況": STATUS_ORDER,
    "周辺AP判定": VERDICT_ORDER,
}


def _build_kinds() -> dict[str, str]:
    """列 → 種類。RESULT_COLUMNS の全列をちょうど 1 回ずつ分類する。"""
    kinds: dict[str, str] = {}
    for col in TIME_COLUMNS:
        kinds[col] = KIND_TIME
    for col in (*INT_COLUMNS, *FLOAT_COLUMNS):
        kinds[col] = KIND_NUMBER
    for col in BOOL_COLUMNS:
        kinds[col] = KIND_BOOL
    for col in ENUM_CHOICES:
        kinds[col] = KIND_ENUM
    # 残りは文字列（部分一致）。列が増えたときの既定はこちらにする
    for col in RESULT_COLUMNS:
        kinds.setdefault(col, KIND_TEXT)

    unknown = sorted(set(kinds) - set(RESULT_COLUMNS))
    if unknown:  # 上の定義に結果に無い列が混ざっている（打ち間違い）
        raise RuntimeError(f"hangap.table: 結果に無い列を分類しています: {unknown}")
    return kinds


#: 列 → 種類（:data:`KIND_TEXT` などのいずれか）
COLUMN_KINDS: dict[str, str] = _build_kinds()


# ---------------------------------------------------------------------------
# 絞り込みの指定
# ---------------------------------------------------------------------------

OP_CONTAINS = "contains"
OP_IN = "in"
OP_MIN = "min"
OP_MAX = "max"
OP_FROM = "from"
OP_TO = "to"
OP_IS = "is"

#: 種類ごとに使える演算子
OPS_BY_KIND: dict[str, tuple[str, ...]] = {
    KIND_TEXT: (OP_CONTAINS,),
    KIND_ENUM: (OP_IN,),
    KIND_NUMBER: (OP_MIN, OP_MAX),
    KIND_TIME: (OP_FROM, OP_TO),
    KIND_BOOL: (OP_IS,),
}

#: 指定の書式。値に ``:`` が入る（時刻）ので、分割は 2 回だけにすること
SPEC_SEPARATOR = ":"

_TRUE_TEXTS = frozenset({"true", "1"})
_FALSE_TEXTS = frozenset({"false", "0"})


class FilterError(ValueError):
    """絞り込みの指定が不正。``field_name`` にどの項目が不正かを持つ（API は 400）。"""

    def __init__(self, message: str, field_name: str) -> None:
        super().__init__(message)
        self.field_name = field_name


@dataclass(frozen=True)
class Filter:
    """1 つの絞り込み条件。"""

    column: str
    op: str
    value: Any


def parse_filter(spec: str) -> Filter:
    """``列名:演算子:値`` を 1 件解釈する。

    値には ``:`` が入りうる（時刻）ため、区切りは先頭 2 つだけを見る。
    列名・演算子・値のいずれかが不正なら :class:`FilterError` を投げる。
    """
    parts = str(spec).split(SPEC_SEPARATOR, 2)
    if len(parts) != 3:
        raise FilterError(
            f"'列名:演算子:値' の形式で指定してください: {spec!r}", "filter"
        )
    column, op, raw = parts[0].strip(), parts[1].strip(), parts[2].strip()

    if column not in COLUMN_KINDS:
        raise FilterError(f"結果に無い列です: {column!r}", "filter")
    kind = COLUMN_KINDS[column]
    allowed = OPS_BY_KIND[kind]
    if op not in allowed:
        raise FilterError(
            f"{column} は {kind} 列です。演算子は次のいずれかで指定してください: "
            f"{', '.join(allowed)}（指定: {op!r}）",
            f"filter[{column}]",
        )
    if raw == "":
        raise FilterError(f"{column}: 値を指定してください", f"filter[{column}]")

    field_name = f"filter[{column}]"
    if op == OP_IN:
        choices = ENUM_CHOICES[column]
        if raw not in choices:
            raise FilterError(
                f"{column}: 次のいずれかで指定してください: {', '.join(choices)}（指定: {raw!r}）",
                field_name,
            )
        return Filter(column, op, raw)
    if op in (OP_MIN, OP_MAX):
        try:
            value: Any = float(raw)
        except ValueError:
            raise FilterError(f"{column}: 数値で指定してください: {raw!r}", field_name) from None
        if value != value:  # NaN
            raise FilterError(f"{column}: 数値で指定してください: {raw!r}", field_name)
        return Filter(column, op, value)
    if op in (OP_FROM, OP_TO):
        ts = pd.Timestamp(_parse_time(raw, column, field_name))
        return Filter(column, op, ts)
    if op == OP_IS:
        lowered = raw.lower()
        if lowered in _TRUE_TEXTS:
            return Filter(column, op, True)
        if lowered in _FALSE_TEXTS:
            return Filter(column, op, False)
        raise FilterError(f"{column}: true / false で指定してください: {raw!r}", field_name)
    return Filter(column, op, raw)  # contains


def _parse_time(raw: str, column: str, field_name: str) -> pd.Timestamp:
    """時刻を naive な Timestamp にする。

    解釈は分析条件の ``from`` / ``to`` と同じ :func:`analysis.parse_time` に委ねる
    （画面で同じ書式を入力して結果が違う、という状態を作らない）。ログの時刻は
    naive なので、TZ 付きの値は弾かれる。
    """
    try:
        ts = parse_time(raw, column, field_name)
    except ParamError as e:
        raise FilterError(f"{e}", field_name) from None
    if pd.isna(ts):
        raise FilterError(
            f"{column}: 'YYYY-MM-DD HH:MM' の形式で指定してください: {raw!r}", field_name
        )
    return ts


def parse_filters(specs: Iterable[str]) -> list[Filter]:
    return [parse_filter(spec) for spec in specs]


# ---------------------------------------------------------------------------
# 絞り込みの適用
# ---------------------------------------------------------------------------


def _as_bool_mask(mask: pd.Series, index: pd.Index) -> pd.Series:
    """欠損（NA / NaT / NaN との比較）を False に落とした bool の Series。"""
    return pd.Series(mask, index=index).fillna(False).astype(bool)


def _mask_for(df: pd.DataFrame, f: Filter) -> pd.Series:
    col = df[f.column]
    if f.op == OP_CONTAINS:
        hit = col.astype("string").str.contains(f.value, case=False, regex=False, na=False)
    elif f.op == OP_MIN:
        hit = pd.to_numeric(col, errors="coerce") >= f.value
    elif f.op == OP_MAX:
        hit = pd.to_numeric(col, errors="coerce") <= f.value
    elif f.op == OP_FROM:
        hit = pd.to_datetime(col, errors="coerce") >= f.value
    elif f.op == OP_TO:
        hit = pd.to_datetime(col, errors="coerce") <= f.value
    elif f.op == OP_IS:
        hit = col.fillna(False).astype(bool) == bool(f.value)
    else:  # pragma: no cover - parse_filter が弾く
        raise FilterError(f"不明な演算子です: {f.op!r}", "filter")
    return _as_bool_mask(hit, df.index)


def apply_filters(df: pd.DataFrame, filters: Sequence[Filter]) -> pd.DataFrame:
    """絞り込みを適用した DataFrame を返す（**複数列は AND**）。

    同じ列に対する値の選択（``in``）だけは OR で束ねる（複数選択のため）。
    行の並び順は変えない（ソートは呼び出し側で行う）。
    """
    if not filters:
        return df
    mask = pd.Series(True, index=df.index)
    chosen: dict[str, list[str]] = {}
    for f in filters:
        if f.op == OP_IN:
            chosen.setdefault(f.column, []).append(f.value)
            continue
        mask &= _mask_for(df, f)
    for column, values in chosen.items():
        mask &= _as_bool_mask(df[column].astype("string").isin(values), df.index)
    return df[mask]


# ---------------------------------------------------------------------------
# 保存済み csv の読み戻し
# ---------------------------------------------------------------------------


def read_result_csv(path: str | Path) -> pd.DataFrame:
    """保存済みの結果 csv を、実行中ジョブの結果と同じ dtype で読み戻す。

    **再分析はしない。** 分析時に書き出した csv（``utf-8-sig`` / 全 30 列）を読み、
    :func:`detector._to_frame` と同じ型（時刻・Int64・float・bool・string）に揃える。
    型を揃えないと、同じ絞り込みが実行中ジョブの結果と保存済み結果で違う結果になる。

    列は :data:`detector.RESULT_COLUMNS` に揃える（欠けている列は空、余分な列は捨てる）。
    古い書式の csv でも表示だけはできるようにするためで、列を作り直すことはしない。
    """
    raw = pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )
    out = pd.DataFrame(index=raw.index)
    for col in RESULT_COLUMNS:
        src = raw[col] if col in raw.columns else pd.Series(pd.NA, index=raw.index, dtype="object")
        if col in TIME_COLUMNS:
            out[col] = pd.to_datetime(src, errors="coerce")
        elif col in INT_COLUMNS:
            out[col] = pd.to_numeric(src, errors="coerce").astype("Int64")
        elif col in FLOAT_COLUMNS:
            out[col] = pd.to_numeric(src, errors="coerce")
        elif col in BOOL_COLUMNS:
            out[col] = (
                src.astype("string").fillna("").str.strip().str.lower().isin(_TRUE_TEXTS)
            )
        else:
            out[col] = src.astype("string").fillna("")
    return out
