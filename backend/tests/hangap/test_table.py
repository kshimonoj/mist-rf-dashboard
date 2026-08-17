"""結果テーブルの列の性質（``hangap.table``）のテスト。

このモジュールの列分類は :func:`detector._to_frame` の dtype と一致していなければ
ならない。食い違うと、同じ絞り込みが実行中ジョブの結果と保存済み結果で違う結果を
返す（例: 数値列を文字列として部分一致させてしまう）。列が増えたときに気づけるよう、
分類は「実際の結果 DataFrame の dtype」と突き合わせて確認する。

合成データのみを使う。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import _synth as S
import pandas as pd
import pytest

from hangap import table
from hangap.detector import RESULT_COLUMNS, detect
from hangap.loader import load

START = datetime(2026, 1, 1, 10, 0, 0)
INTERVAL = 60
HANG_PATTERN = [1, 1, 1] + [0] * 7 + [1, 1, 1]


@pytest.fixture
def result(tmp_path) -> pd.DataFrame:
    """1 区間だけ検出される結果 DataFrame（dtype の参照元）。"""
    rows = [
        S.metrics_row(
            START + timedelta(seconds=INTERVAL * i),
            ap_id="test-ap-0000", ap_name="TEST-AP-00", num_clients=v,
        )
        for i, v in enumerate(HANG_PATTERN)
    ]
    S.write_metrics(tmp_path / "ap_metrics.csv", rows)
    res = load(tmp_path)
    df = detect(res.metrics, res.events, res.gaps)
    assert len(df) == 1
    return df


# ---------------------------------------------------------------------------
# 列の分類
# ---------------------------------------------------------------------------


def test_every_result_column_is_classified_exactly_once():
    assert set(table.COLUMN_KINDS) == set(RESULT_COLUMNS)
    assert len(table.COLUMN_KINDS) == len(RESULT_COLUMNS)
    assert set(table.ENUM_CHOICES) <= set(RESULT_COLUMNS)
    for kind in table.COLUMN_KINDS.values():
        assert kind in table.OPS_BY_KIND


def test_column_kinds_match_the_actual_dtypes(result):
    """分類が結果 DataFrame の dtype と食い違っていないこと。"""
    for col, kind in table.COLUMN_KINDS.items():
        dtype = result[col].dtype
        if kind == table.KIND_TIME:
            assert pd.api.types.is_datetime64_any_dtype(dtype), col
        elif kind == table.KIND_NUMBER:
            assert pd.api.types.is_numeric_dtype(dtype), col
            assert not pd.api.types.is_bool_dtype(dtype), col
        elif kind == table.KIND_BOOL:
            assert pd.api.types.is_bool_dtype(dtype), col
        else:  # text / enum はどちらも文字列列
            assert isinstance(dtype, pd.StringDtype), col

    # 整数列 / 小数列の振り分けも dtype と一致していること（read_result_csv で使う）
    for col in table.INT_COLUMNS:
        assert str(result[col].dtype) == "Int64", col
    for col in table.FLOAT_COLUMNS:
        assert pd.api.types.is_float_dtype(result[col].dtype), col


# ---------------------------------------------------------------------------
# csv の読み戻し
# ---------------------------------------------------------------------------


def test_read_result_csv_restores_the_same_dtypes_and_values(result, tmp_path):
    """保存済み csv を読み戻した DataFrame が、元の結果と同じ型・同じ値になること。"""
    from hangap.analysis import write_csv

    path = write_csv(tmp_path / "saved.csv", result)
    restored = table.read_result_csv(path)

    assert list(restored.columns) == list(RESULT_COLUMNS)
    for col in RESULT_COLUMNS:
        assert str(restored[col].dtype) == str(result[col].dtype), col
    pd.testing.assert_frame_equal(restored, result, check_like=False)


def test_read_result_csv_keeps_empty_cells_empty(result, tmp_path):
    """空セルは NA / 空文字のまま（"nan" のような文字列にしない）。"""
    from hangap.analysis import write_csv

    path = write_csv(tmp_path / "saved.csv", result)
    restored = table.read_result_csv(path)

    # 回復した区間なので「回復時刻」は入るが、イベントは無いので Event 列は空
    assert restored["Event時刻"].tolist() == [""]
    assert restored["回復時刻"].notna().all()
    assert restored["直後clients（回復時）"].notna().all()


def test_read_result_csv_tolerates_missing_columns(tmp_path):
    """列が欠けた古い csv でも表示だけはできる（列は作り直さない）。"""
    path = tmp_path / "old.csv"
    path.write_text("ap_name,回復状況\nTEST-AP-00,回復\n", encoding="utf-8-sig")

    df = table.read_result_csv(path)
    assert list(df.columns) == list(RESULT_COLUMNS)
    assert df["ap_name"].tolist() == ["TEST-AP-00"]
    assert df["連続ゼロ回数"].isna().all()


# ---------------------------------------------------------------------------
# 絞り込みの適用（DataFrame レベル）
# ---------------------------------------------------------------------------


def test_apply_filters_does_not_change_the_row_order(result):
    df = pd.concat([result, result], ignore_index=True)
    df.loc[1, "ap_name"] = "TEST-AP-99"
    filtered = table.apply_filters(df, table.parse_filters(["ap_name:contains:TEST"]))
    assert filtered["ap_name"].tolist() == df["ap_name"].tolist()


def test_apply_filters_on_an_empty_frame_returns_empty(result):
    empty = result.iloc[0:0]
    filtered = table.apply_filters(empty, table.parse_filters(["ap_name:contains:TEST"]))
    assert len(filtered) == 0
    assert list(filtered.columns) == list(RESULT_COLUMNS)


def test_no_filters_returns_the_frame_unchanged(result):
    assert table.apply_filters(result, []) is result
