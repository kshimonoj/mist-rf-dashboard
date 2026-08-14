"""レポート（種別集計・未判定ファイル・イベント範囲・site 期間・XLSX）のテスト。"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

import _synth as S

from hangap.loader import load
from pseudonymizer.schemas import AP_EVENTS_COLUMNS, AP_METRICS_COLUMNS, CLIENT_METRICS_COLUMNS

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 1, 1, 10, 0, 5)


def test_no_events_is_not_an_error(tmp_path):
    """ap_events が 1 件も無くてもエラーにせず、レポートに「イベントなし」を出す。"""
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 300, 24))
    res = load(tmp_path)

    assert res.events.empty
    assert list(res.events.columns) == list(AP_EVENTS_COLUMNS)
    assert res.report.events_period is None
    assert res.report.events_rows == 0
    rendered = res.report.render()
    assert "この期間のイベントログはありません" in rendered
    # メトリクス期間全体がイベント空白として出る
    assert len(res.report.event_blind_spots) == 1
    assert res.report.event_blind_spots[0] == res.report.metrics_period


def test_header_only_event_file_is_classified(tmp_path):
    """ヘッダーだけの ap_events ファイルも種別判定され、0 件として扱われる。"""
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 300, 24))
    S.write_events(tmp_path / "e.csv", [])
    res = load(tmp_path)

    assert res.report.file_stats["ap_events"].files == 1
    assert res.report.file_stats["ap_events"].rows == 0
    assert res.events.empty


def test_event_period_and_blind_spots(tmp_path):
    """イベントの期間と、イベントが存在しない区間がレポートに出る。"""
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 300, 60))  # 10:00 〜 14:55
    S.write_events(
        tmp_path / "e.csv",
        [
            S.event_row(START + timedelta(minutes=10)),
            S.event_row(START + timedelta(minutes=20), "AP_CONFIGURED"),
        ],
    )
    res = load(tmp_path)

    assert res.report.events_period == (
        START + timedelta(minutes=10),
        START + timedelta(minutes=20),
    )
    # 最後のイベント以降が空白区間として出る（先頭 10 分は 1 時間未満なので出ない）
    assert len(res.report.event_blind_spots) == 1
    assert res.report.event_blind_spots[0][0] == START + timedelta(minutes=20)
    assert "イベントが存在しない区間" in res.report.render()


def test_unclassified_file_is_reported_without_values(tmp_path):
    """どの種別にも一致しないファイルは名前だけ記録し、中身の値を漏らさない。"""
    res = load([FIXTURES / "unknown_table.csv", FIXTURES / "ap_metrics_part1.csv"])

    assert res.report.unclassified == ["unknown_table.csv"]
    rendered = res.report.render()
    assert "unknown_table.csv" in rendered
    assert "SENTINEL-VALUE-XYZ" not in rendered
    assert "alpha" not in rendered and "gamma" not in rendered


def test_other_file_types_are_counted_but_not_loaded(tmp_path):
    """既定では client_metrics は読み込まないが、種別判定と件数はレポートに出す。"""
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 300, 12))
    with open(tmp_path / "c.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CLIENT_METRICS_COLUMNS)
        for _ in range(3):
            w.writerow([""] * len(CLIENT_METRICS_COLUMNS))
    res = load(tmp_path)

    st = res.report.file_stats["client_metrics"]
    assert (st.files, st.rows, st.loaded) == (1, 3, False)
    assert res.report.file_stats["ap_metrics"].loaded is True
    assert len(res.metrics) == 12


def test_file_types_argument(tmp_path):
    """file_types で読み込む種別を限定できる。"""
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 300, 12))
    S.write_events(tmp_path / "e.csv", [S.event_row(START)])

    res = load(tmp_path, file_types=["ap_events"])
    assert res.metrics.empty
    assert len(res.events) == 1
    assert res.report.file_stats["ap_metrics"].files == 1
    assert res.report.file_stats["ap_metrics"].loaded is False
    assert res.report.file_stats["ap_metrics"].rows == 12


def test_xlsx_sheets_are_detected_by_header(tmp_path):
    """XLSX はシートごとにヘッダーで種別判定され、未一致シートは警告に残る。"""
    metrics_rows = S.metrics_series(START, 300, 6)
    event_rows = [S.event_row(START + timedelta(minutes=5))]
    S.write_xlsx(
        tmp_path / "logs.xlsx",
        {
            "sheet_a": (AP_METRICS_COLUMNS, metrics_rows),
            "sheet_b": (AP_EVENTS_COLUMNS, event_rows),
            "notes": (["memo", "author"], [{"memo": "SENTINEL-VALUE-XYZ", "author": "x"}]),
        },
    )
    res = load(tmp_path)

    assert len(res.metrics) == 6
    assert len(res.events) == 1
    assert res.report.file_stats["ap_metrics"].files == 1
    assert res.report.file_stats["ap_events"].files == 1
    assert res.report.unclassified == ["logs.xlsx#notes"]
    assert "SENTINEL-VALUE-XYZ" not in res.report.render()
    # 秒・型が CSV 経路と揃っていること
    assert res.metrics["timestamp"].min() == START
    assert res.metrics["timestamp"].dt.tz is None


def test_site_periods_and_disjoint_warning(tmp_path):
    """site_id ごとの出現期間が出て、期間が重ならなければ警告になる。"""
    S.write_metrics(
        tmp_path / "m1.csv",
        S.metrics_series(START, 300, 12, site_id="test-site-id-0001", site_name="SiteA"),
    )
    S.write_metrics(
        tmp_path / "m2.csv",
        S.metrics_series(
            START + timedelta(hours=5),
            300,
            12,
            ap_id="test-ap-0002",
            ap_name="TEST-AP-02",
            mac="aabbccddee02",
            site_id="test-site-id-0002",
            site_name="SiteB",
        ),
    )
    res = load(tmp_path)

    assert [sp.site_id for sp in res.report.site_periods] == [
        "test-site-id-0001",
        "test-site-id-0002",
    ]
    assert res.report.site_periods[0].ap_count == 1
    assert res.report.site_periods[0].rows == 12
    assert any("site_id" in w for w in res.report.warnings)
    assert res.report.ap_count == 2


def test_report_render_contains_all_required_sections(tmp_path):
    S.write_metrics(tmp_path / "m.csv", S.metrics_series(START, 300, 24, skip=(10,)))
    S.write_events(tmp_path / "e.csv", [S.event_row(START + timedelta(minutes=5))])
    res = load(tmp_path)

    rendered = res.report.render()
    for section in (
        "種別ごとのファイル数",
        "判定できなかったファイル",
        "サンプリング間隔の推定",
        "ギャップ（欠測）",
        "site_id ごとの出現期間",
        "期間",
        "イベントが存在しない区間",
        "警告",
    ):
        assert section in rendered
    assert res.report.files_scanned == 2
    assert isinstance(rendered, str)


def test_empty_input_is_not_an_error(tmp_path):
    res = load(tmp_path)

    assert res.metrics.empty and res.events.empty and res.gaps.empty
    assert list(res.metrics.columns) == list(AP_METRICS_COLUMNS)
    assert res.report.files_scanned == 0
    assert res.report.overall_interval_seconds is None
    assert isinstance(res.report.render(), str)


def test_glob_and_recursive_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    S.write_metrics(tmp_path / "sub" / "m1.csv", S.metrics_series(START, 300, 6))
    S.write_metrics(
        tmp_path / "sub" / "m2.csv", S.metrics_series(START + timedelta(hours=1), 300, 6)
    )

    by_dir = load(tmp_path)
    by_glob = load(str(tmp_path / "sub" / "m*.csv"))
    assert len(by_dir.metrics) == len(by_glob.metrics) == 12
    # 同じファイルを重ねて指定しても二重に読まない
    dupe = load([tmp_path, str(tmp_path / "sub" / "m1.csv")])
    assert dupe.report.files_scanned == 2
