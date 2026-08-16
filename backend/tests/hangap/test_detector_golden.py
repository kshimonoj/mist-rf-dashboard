"""ゴールデン照合。手作業の分析結果を機械的に再現できているかを見る。

ゴールデンデータは **リポジトリ外** にある（顧客データ由来のため）。
既定のパスに無ければ skip する（fail にはしない）。

期待値はすべて外部ファイルから読む。件数も個々の値もこのファイルに書かない
（Public リポジトリであり、実装を期待値に合わせる誘惑を断つため）。
出力してよいのは **一致件数・不一致件数・不一致行の index** だけで、
AP 名・時刻・クライアント数などの値は出さない。

2 本に分ける。

  テスト1（互換再現・完全一致を維持）:
    「16:00-21:00 のログファイルだけを読み込んだ利用者」は、そもそも 15:59 台のサンプルを
    **持っていない**。その状況をテスト側のデータスライスで再現し（detector に互換フラグは
    足さない）、window_end は省略して回復状況=='回復' で絞ると、期待値シートの 52 行と
    完全一致する。このテストは今後の回帰検出の要であり、完全一致のアサートを外さない。

  テスト2（新仕様の検証・件数はハードコードしない）:
    フルデータ（1週間分のログを読み込んだ状態）で 16:00-21:00 を指定すると、detect() は
    **その期間のサンプルだけ** で分析する。期待値シートとの差分は次の 1 パターンだけで
    説明できるはずで、それ以外の差分が 0 件であることを確認する。

      - シートにあって出力に無い行 → すべて ゼロ終了 >= window_end
        （窓の外で始まった区間か、窓の右端で打ち切られて min_zero_samples を満たさなく
        なった区間。指定期間内で観測できたゼロが短ければ採用されない）
      - 出力にあってシートに無い行 → 0 件
        （窓の外のサンプルは見ないので、シートを作った利用者に見えなかった区間は出ない）
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from hangap.detector import CORE_RESULT_COLUMNS, NEIGHBOR_COLUMNS, STATUS_RECOVERED, detect
from hangap.loader import load

#: ゴールデンデータの既定パス（環境変数 HANGAP_GOLDEN_PATH で上書き可能）
DEFAULT_GOLDEN_PATH = Path.home() / "work" / "hang-ap-data" / "golden" / "golden_ap_log.xlsx"

#: 期待値シート（ヘッダーは 4 行目 = 0-based で index 3。上 3 行はタイトル・条件・空行）
EXPECTED_SHEET = "ゼロ継続AP_16-21"
EXPECTED_HEADER_ROW = 3

WINDOW_START = pd.Timestamp("2026-08-09 16:00")
WINDOW_END = pd.Timestamp("2026-08-09 21:00")

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

#: 行の同一性を跨いで突き合わせるためのキー（分単位の ap_name + ゼロ開始）
def _row_key(ap_name: str, zero_start) -> tuple[str, pd.Timestamp]:
    return (str(ap_name), pd.Timestamp(zero_start).floor("min"))


def golden_path() -> Path:
    return Path(os.environ.get("HANGAP_GOLDEN_PATH", str(DEFAULT_GOLDEN_PATH)))


@pytest.fixture(scope="module")
def loaded():
    """(loader の戻り値, 期待値シート)。ファイルが無ければ skip。"""
    path = golden_path()
    if not path.is_file():
        pytest.skip(f"ゴールデンデータが見つかりません: {path}")
    res = load(path)
    expected = pd.read_excel(path, sheet_name=EXPECTED_SHEET, header=EXPECTED_HEADER_ROW)
    return res, expected


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


# ---------------------------------------------------------------------------
# テスト1: 互換再現（完全一致を維持する）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compat_recovered(loaded):
    """16:00 より前のサンプルを持たない利用者を模したスライスでの検出結果。"""
    res, _ = loaded
    sliced_metrics = res.metrics[res.metrics["timestamp"] >= WINDOW_START]
    actual = detect(
        sliced_metrics, res.events, res.gaps,
        window_start=WINDOW_START, window_end=None,
    )
    return actual[actual["回復状況"] == STATUS_RECOVERED]


def test_compat_recovered_matches_golden_exactly(loaded, compat_recovered):
    """16:00-21:00 のログだけを読み込んだ場合、期待値シートと完全一致すること。"""
    _, expected = loaded
    actual_sorted = _sorted_by_key(compat_recovered)
    expected_sorted = _sorted_by_key(expected)

    assert len(actual_sorted) == len(expected_sorted), (
        f"区間数が一致しません: 期待 {len(expected_sorted)} / 実際 {len(actual_sorted)}"
    )

    for col in EXACT_COLUMNS:
        left, right = _normalize(actual_sorted[col]), _normalize(expected_sorted[col])
        bad = _mismatched_index(left, right)
        assert not bad, f"列 {col} が不一致: {len(bad)} 件 / index={bad}"

    print(f"PASS（互換再現）: {len(actual_sorted)}/{len(expected_sorted)} 区間一致")


def test_compat_timestamps_match_golden_at_minute_resolution(loaded, compat_recovered):
    """時刻列は分単位に丸めれば一致すること（実装側は秒を保持する）。"""
    _, expected = loaded
    actual_sorted = _sorted_by_key(compat_recovered)
    expected_sorted = _sorted_by_key(expected)

    for col in MINUTE_COLUMNS:
        bad = _mismatched_index(_to_minutes(actual_sorted[col]), _to_minutes(expected_sorted[col]))
        assert not bad, f"列 {col} が不一致: {len(bad)} 件 / index={bad}"

    # 実装側は秒を丸めていないこと（丸めた実装を通してしまわないための確認）
    assert (actual_sorted["ゼロ開始"].dt.second != 0).any()


def test_compat_event_correlation_matches_golden(loaded, compat_recovered):
    """イベントが検出された行の集合と、各行のイベント種別の並びが一致すること。"""
    _, expected = loaded
    actual_sorted = _sorted_by_key(compat_recovered)
    expected_sorted = _sorted_by_key(expected)

    actual_has = _normalize(actual_sorted["AP Event（±30分）"]) != ""
    expected_has = _normalize(expected_sorted["AP Event（±30分）"]) != ""
    assert actual_has.sum() > 0
    bad = _mismatched_index(actual_has, expected_has)
    assert not bad, f"イベント有無が不一致: {len(bad)} 件 / index={bad}"

    # 種別の並びは「分単位の時刻」で比べる。期待値シートは秒を持っておらず、
    # 同じ分に並んだイベントの順序はシート側の元の行順で決まっているため、
    # 同一分内の順序だけを正規化して比較する（分をまたぐ順序は厳密に見る）。
    left = actual_sorted.apply(lambda r: _event_pairs(r["Event時刻"], r["Event種別"]), axis=1)
    right = expected_sorted.apply(lambda r: _event_pairs(r["Event時刻"], r["Event種別"]), axis=1)
    bad_types = [int(i) for i in left.index if left[i] != right[i]]
    assert not bad_types, f"Event種別 が不一致: {len(bad_types)} 件 / index={bad_types}"

    # 4 列は同じ件数で並ぶこと
    for _, row in actual_sorted[actual_has].iterrows():
        counts = {
            len(str(row[col]).split(" | "))
            for col in ("Event時刻", "ゼロ終了との差(分)", "Event種別", "Event詳細")
        }
        assert len(counts) == 1

    print(f"イベント一致（互換再現）: {int(actual_has.sum())} 行")


def test_neighbor_columns_are_additive_and_do_not_break_the_golden_match(loaded):
    """周辺AP判定は **加算的な機能** であることの確認。

    座標（map_id / x_m / y_m）を渡さない状態では周辺AP列はすべて空になり、
    既存 22 列の結果は期待値シートと完全一致したまま変わらない。
    検証済みのコアを壊していないことの証明として、この確認は外さない。
    """
    res, expected = loaded
    sliced = res.metrics[res.metrics["timestamp"] >= WINDOW_START].copy()
    for col in ("map_id", "x_m", "y_m"):
        if col in sliced.columns:
            sliced[col] = pd.NA

    actual = detect(sliced, res.events, res.gaps, window_start=WINDOW_START, window_end=None)
    recovered = _sorted_by_key(actual[actual["回復状況"] == STATUS_RECOVERED])
    expected_sorted = _sorted_by_key(expected)

    assert len(recovered) == len(expected_sorted), (
        f"区間数が一致しません: 期待 {len(expected_sorted)} / 実際 {len(recovered)}"
    )
    for col in EXACT_COLUMNS:
        left, right = _normalize(recovered[col]), _normalize(expected_sorted[col])
        bad = _mismatched_index(left, right)
        assert not bad, f"列 {col} が不一致: {len(bad)} 件 / index={bad}"

    # 列の名前と順序（既存 22 列が先頭、周辺AP列は末尾に追加）
    assert tuple(actual.columns[: len(CORE_RESULT_COLUMNS)]) == CORE_RESULT_COLUMNS
    assert tuple(actual.columns[len(CORE_RESULT_COLUMNS):]) == NEIGHBOR_COLUMNS

    # 座標が無いので周辺AP列は空（判定不能）
    for col in ("周辺AP数", "周辺AP端末数合計", "周辺AP RF隣接数"):
        assert actual[col].isna().all(), f"座標なしなのに {col} に値が入っています"
    for col in ("周辺AP名", "周辺AP距離", "周辺AP端末数"):
        assert (actual[col].astype("string").fillna("") == "").all()
    assert set(actual["周辺AP判定"]) == {"判定不能"}

    print(f"PASS（ゴールデン非破壊）: {len(recovered)}/{len(expected_sorted)} 区間一致 / 周辺AP列は空")


# ---------------------------------------------------------------------------
# テスト2: 新仕様の検証（件数はハードコードしない・差分は構造として説明する）
# ---------------------------------------------------------------------------


def test_full_data_window_end_diff_is_fully_explained(loaded):
    """フルデータ + 窓 16:00-21:00 の出力と期待値シートの差分がすべて説明できること
    （件数はコードに書かず、実行時に算出する）。

      - シートにあって出力に無い行 → すべて ゼロ終了 >= window_end
      - 出力にあってシートに無い行 → 0 件（窓の外のサンプルは見ないため）
    """
    res, expected = loaded
    actual = detect(
        res.metrics, res.events, res.gaps,
        window_start=WINDOW_START, window_end=WINDOW_END,
    )

    expected_keys = {
        _row_key(r.ap_name, r.ゼロ開始): r for r in expected.itertuples()
    }
    actual_keys = {
        _row_key(r.ap_name, r.ゼロ開始): r for r in actual.itertuples()
    }

    expected_only = set(expected_keys) - set(actual_keys)
    actual_only = set(actual_keys) - set(expected_keys)

    unexplained_expected = [
        key for key in expected_only
        if not (pd.Timestamp(expected_keys[key].ゼロ終了) >= WINDOW_END)
    ]

    assert not unexplained_expected, (
        f"シートにあって出力に無い行のうち、ゼロ終了>=window_end で説明できないものが "
        f"{len(unexplained_expected)} 件あります"
    )
    assert not actual_only, (
        f"窓の外のサンプルを見ていないのに、シートに無い行が {len(actual_only)} 件あります"
    )

    # 窓の外へ伸びないこと（本タスクの目的）を実データ側でも確認する。
    # 値そのものは出さない（Public リポジトリのため）。
    assert (actual["ゼロ終了"] < WINDOW_END).all()
    assert (actual["ゼロ開始"] >= WINDOW_START).all()

    print(
        f"PASS（新仕様）: 一致 {len(set(expected_keys) & set(actual_keys))} 件 / "
        f"シートのみ（ゼロ終了が 21:00 以降）{len(expected_only)} 件 / "
        f"出力のみ 0 件 / 説明できない差分 0 件"
    )
