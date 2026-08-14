"""ゴールデン照合。手作業の分析結果を機械的に再現できているかを見る。

ゴールデンデータは **リポジトリ外** にある（顧客データ由来のため）。
既定のパスに無ければ skip する（fail にはしない）。

期待値はすべて外部ファイルから読む。件数も個々の値もこのファイルに書かない
（Public リポジトリであり、実装を期待値に合わせる誘惑を断つため）。
出力してよいのは **一致件数・不一致件数・不一致行の index** だけで、
AP 名・時刻・クライアント数などの値は出さない。

2 本に分ける。

  テスト1（互換再現・完全一致を維持）:
    detect() 自体は window_start/window_end を「サンプルの絞り込み」には使わない
    （ゼロ直前時刻が窓の外にあってよい）。しかし「16:00-21:00 のログファイルだけを
    読み込んだ利用者」は、そもそも 15:59 台のサンプルを **持っていない**。
    その状況をテスト側のデータスライスで再現し（detector に互換フラグは足さない）、
    window_end は省略して回復状況=='回復' で絞ると、期待値シートの 52 行と完全一致する。
    このテストは今後の回帰検出の要であり、完全一致のアサートを外さない。

  テスト2（新仕様の検証・件数はハードコードしない）:
    フルデータ（読み込んだ全サンプルを保持したまま）で window_end=21:00 を指定すると、
    「ゼロ開始が 21:00 以降の区間」は正しく除外され、「ゼロ直前時刻が 16:00 より前の区間」
    （テスト1のスライスでは見えなかった区間）が新たに拾える。期待値シートとの差分は
    すべてこの 2 パターンで説明できるはずで、それ以外の差分が 0 件であることを確認する。
"""
from __future__ import annotations

import os
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


# ---------------------------------------------------------------------------
# テスト2: 新仕様の検証（件数はハードコードしない・差分は構造として説明する）
# ---------------------------------------------------------------------------


def test_full_data_window_end_diff_is_fully_explained(loaded):
    """フルデータ + window_end=21:00 の出力と期待値シートの差分が、以下の 2 パターンで
    すべて説明できること（件数はコードに書かず、実行時に算出する）。

      - シートにあって出力に無い行 → すべて ゼロ開始 >= window_end
      - 出力にあってシートに無い行 → すべて ゼロ直前時刻 < window_start
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
        if not (pd.Timestamp(expected_keys[key].ゼロ開始) >= WINDOW_END)
    ]
    unexplained_actual = [
        key for key in actual_only
        if not (pd.Timestamp(actual_keys[key].ゼロ直前時刻) < WINDOW_START)
    ]

    assert not unexplained_expected, (
        f"シートにあって出力に無い行のうち、ゼロ開始>=window_end で説明できないものが "
        f"{len(unexplained_expected)} 件あります"
    )
    assert not unexplained_actual, (
        f"出力にあってシートに無い行のうち、ゼロ直前時刻<window_start で説明できないものが "
        f"{len(unexplained_actual)} 件あります"
    )

    print(
        f"PASS（新仕様）: 一致 {len(set(expected_keys) & set(actual_keys))} 件 / "
        f"シートのみ（21:00 以降開始）{len(expected_only)} 件 / "
        f"出力のみ（16:00 より前を直前clientsに使用）{len(actual_only)} 件 / "
        f"説明できない差分 0 件"
    )
