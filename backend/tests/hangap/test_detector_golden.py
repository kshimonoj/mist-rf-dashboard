"""ゴールデン照合。手作業の分析結果を機械的に再現できているかを見る。

ゴールデンデータは **リポジトリ外** にある（顧客データ由来のため）。
既定のパスに無ければ skip する（fail にはしない）。

期待値はすべて外部ファイルから読む。件数も個々の値もこのファイルに書かない
（Public リポジトリであり、実装を期待値に合わせる誘惑を断つため）。
出力してよいのは **一致件数・不一致件数・不一致行の index** だけで、
AP 名・時刻・クライアント数などの値は出さない。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from hangap.detector import STATUS_RECOVERED, detect
from hangap.loader import load

#: ゴールデンデータの既定パス（環境変数 HANGAP_GOLDEN_PATH で上書き可能）
DEFAULT_GOLDEN_PATH = Path.home() / "work" / "hang-ap-data" / "golden" / "golden_ap_log.xlsx"

#: 期待値シート（ヘッダーは 4 行目 = 0-based で index 3。上 3 行はタイトル・条件・空行）
EXPECTED_SHEET = "ゼロ継続AP_16-21"
EXPECTED_HEADER_ROW = 3

WINDOW_START = datetime(2026, 8, 9, 16, 0)
#: 窓の右端はデータ末尾側に置く（21:00 では期待値シートを再現できない）。
#: 期待値シートには 21:00 以降に開始して 21:00 以降に回復した区間が含まれており、
#: 21:00 で切ると走査がそこで終わってしまう。右端をデータ末尾に置くと出力は
#: 「回復」＋「継続中」の 2 種だけになり、回復した区間がシートと一致する。
WINDOW_END = datetime(2026, 8, 9, 22, 0)

#: 完全一致を求める列
EXACT_COLUMNS = (
    "ap_name",
    "区間番号",
    "連続ゼロ回数",
    "直前clients",
    "直後clients（回復時）",
    "AP最大clients",
    "回復状況",
)

#: 分単位に丸めてから比較する列（期待値シート側が秒を切り捨てているため）
MINUTE_COLUMNS = ("ゼロ直前時刻", "ゼロ開始", "ゼロ終了", "回復時刻")

#: 突合のキー（AP 内で一意）
KEY_COLUMNS = ["ap_name", "区間番号"]


def golden_path() -> Path:
    return Path(os.environ.get("HANGAP_GOLDEN_PATH", str(DEFAULT_GOLDEN_PATH)))


@pytest.fixture(scope="module")
def golden() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(detect() の出力, 期待値シート) を返す。ファイルが無ければ skip。"""
    path = golden_path()
    if not path.is_file():
        pytest.skip(f"ゴールデンデータが見つかりません: {path}")

    res = load(path)
    actual = detect(
        res.metrics,
        res.events,
        res.gaps,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    expected = pd.read_excel(path, sheet_name=EXPECTED_SHEET, header=EXPECTED_HEADER_ROW)
    return actual, expected


def _sorted_by_key(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(KEY_COLUMNS, kind="stable").reset_index(drop=True)


def _to_minutes(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.floor("min")


def _normalize(series: pd.Series) -> pd.Series:
    """比較用に型を揃える（Int64 と int64、string と object の差を消す）。"""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").astype("Float64")
    return series.astype("string").fillna("").str.strip()


def _event_pairs(times: object, types: object) -> list[tuple[str, str]]:
    """(分単位の時刻, イベント種別) の並び。同一分内は種別で整列して差を消す。"""
    if pd.isna(times) or not str(times).strip():
        return []
    minutes = [
        pd.Timestamp(t.strip()).floor("min").isoformat() for t in str(times).split("|")
    ]
    kinds = [t.strip() for t in str(types).split("|")]
    pairs = list(zip(minutes, kinds))
    return sorted(pairs, key=lambda p: (p[0], p[1]))


def _mismatched_index(left: pd.Series, right: pd.Series) -> list[int]:
    both_na = left.isna() & right.isna()
    same = (left == right) | both_na
    return [int(i) for i in same[~same.fillna(False)].index]


def test_recovered_intervals_match_golden(golden):
    """回復した区間が期待値シートと 1 行ずつ一致すること。"""
    actual, expected = golden
    recovered = _sorted_by_key(actual[actual["回復状況"] == STATUS_RECOVERED])
    expected = _sorted_by_key(expected)

    assert len(recovered) == len(expected), (
        f"区間数が一致しません: 期待 {len(expected)} / 実際 {len(recovered)}"
    )

    for col in EXACT_COLUMNS:
        left, right = _normalize(recovered[col]), _normalize(expected[col])
        bad = _mismatched_index(left, right)
        assert not bad, f"列 {col} が不一致: {len(bad)} 件 / index={bad}"

    print(f"PASS: {len(recovered)}/{len(expected)} 区間一致")


def test_timestamps_match_golden_at_minute_resolution(golden):
    """時刻列は分単位に丸めれば一致すること（実装側は秒を保持する）。"""
    actual, expected = golden
    recovered = _sorted_by_key(actual[actual["回復状況"] == STATUS_RECOVERED])
    expected = _sorted_by_key(expected)

    for col in MINUTE_COLUMNS:
        bad = _mismatched_index(_to_minutes(recovered[col]), _to_minutes(expected[col]))
        assert not bad, f"列 {col} が不一致: {len(bad)} 件 / index={bad}"

    # 実装側は秒を丸めていないこと（丸めた実装を通してしまわないための確認）
    assert (recovered["ゼロ開始"].dt.second != 0).any()


def test_non_recovered_rows_are_not_recovered(golden):
    """期待値との突合から外した行は、すべて 回復状況 != 回復 であること。"""
    actual, _ = golden
    excluded = actual[actual["回復状況"] != STATUS_RECOVERED]
    assert (excluded["回復状況"] != STATUS_RECOVERED).all()
    assert excluded["回復時刻"].isna().all()
    print(f"除外 {len(excluded)} 行（すべて 回復状況 != 回復）")


def test_event_correlation_matches_golden(golden):
    """イベントが検出された行の集合と、各行のイベント種別の並びが一致すること。"""
    actual, expected = golden
    recovered = _sorted_by_key(actual[actual["回復状況"] == STATUS_RECOVERED])
    expected = _sorted_by_key(expected)

    actual_has = _normalize(recovered["AP Event（±30分）"]) != ""
    expected_has = _normalize(expected["AP Event（±30分）"]) != ""
    assert actual_has.sum() > 0
    bad = _mismatched_index(actual_has, expected_has)
    assert not bad, f"イベント有無が不一致: {len(bad)} 件 / index={bad}"

    # 種別の並びは「分単位の時刻」で比べる。期待値シートは秒を持っておらず、
    # 同じ分に並んだイベントの順序はシート側の元の行順で決まっているため、
    # 同一分内の順序だけを正規化して比較する（分をまたぐ順序は厳密に見る）。
    left = recovered.apply(lambda r: _event_pairs(r["Event時刻"], r["Event種別"]), axis=1)
    right = expected.apply(lambda r: _event_pairs(r["Event時刻"], r["Event種別"]), axis=1)
    bad_types = [int(i) for i in left.index if left[i] != right[i]]
    assert not bad_types, f"Event種別 が不一致: {len(bad_types)} 件 / index={bad_types}"

    # 4 列は同じ件数で並ぶこと
    for _, row in recovered[actual_has].iterrows():
        counts = {
            len(str(row[col]).split(" | "))
            for col in ("Event時刻", "ゼロ終了との差(分)", "Event種別", "Event詳細")
        }
        assert len(counts) == 1

    print(f"イベント一致: {int(actual_has.sum())} 行")
