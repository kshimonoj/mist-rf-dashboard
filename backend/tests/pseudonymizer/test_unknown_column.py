"""未知列の 3 モード、種別判定、入出力ディレクトリの検証。"""
import csv

from conftest import read_csv, write_csv

from pseudonymizer.cli import main
from pseudonymizer.schemas import (
    AP_EVENTS_COLUMNS,
    AP_METRICS_COLUMNS,
    CLIENT_METRICS_COLUMNS,
    FLOORMAP_SUMMARY_COLUMNS,
    SLE_METRICS_COLUMNS,
    detect_file_type,
)


def _add_unknown_column(path, value="TESTVALUE"):
    rows = read_csv(path)
    header = list(AP_EVENTS_COLUMNS) + ["new_mist_column"]
    for r in rows:
        r["new_mist_column"] = value
    write_csv(path, header, rows)


def _header_of(path):
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def test_unknown_column_error_is_the_default(indir, tmp_path, capsys):
    _add_unknown_column(indir / "ap_events_20240101_0900_TZT.csv")
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "new_mist_column" in err
    assert not list(out.glob("*.csv"))


def test_unknown_column_drop(indir, tmp_path, capsys):
    _add_unknown_column(indir / "ap_events_20240101_0900_TZT.csv")
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out), "--unknown-column", "drop"]) == 0
    err = capsys.readouterr().err
    assert "dropping unknown column" in err
    header = _header_of(out / "ap_events_20240101_0900_TZT.csv")
    assert "new_mist_column" not in header
    assert header == list(AP_EVENTS_COLUMNS)


def test_unknown_column_keep_passes_value_through_with_a_warning(indir, tmp_path, capsys):
    _add_unknown_column(indir / "ap_events_20240101_0900_TZT.csv")
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out), "--unknown-column", "keep"]) == 0
    err = capsys.readouterr().err
    assert "KEEPING unknown column" in err
    rows = read_csv(out / "ap_events_20240101_0900_TZT.csv")
    assert all(r["new_mist_column"] == "TESTVALUE" for r in rows)


def test_output_directory_must_differ_from_input(indir, capsys):
    assert main([str(indir), "--out", str(indir)]) == 1
    assert "refusing to overwrite input files" in capsys.readouterr().err


def test_unknown_file_type_is_rejected(indir, tmp_path, capsys):
    (indir / "unknown_report_20240101.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 1
    assert "cannot determine file type" in capsys.readouterr().err


def test_file_type_detection():
    """種別判定はヘッダーの列集合だけで行う（ファイル名は一切見ない）。"""
    assert detect_file_type(AP_METRICS_COLUMNS).key == "ap_metrics"
    assert detect_file_type(AP_EVENTS_COLUMNS).key == "ap_events"
    assert detect_file_type(CLIENT_METRICS_COLUMNS).key == "client_metrics"
    assert detect_file_type(SLE_METRICS_COLUMNS).key == "sle_metrics"
    assert detect_file_type(FLOORMAP_SUMMARY_COLUMNS).key == "floormap_summary"
    assert detect_file_type(["a", "b"]) is None

    # 列順は無視する
    assert detect_file_type(list(reversed(AP_EVENTS_COLUMNS))).key == "ap_events"
    # 列の過不足（重複を含む）は不一致
    assert detect_file_type(list(AP_EVENTS_COLUMNS) + ["extra_col"]) is None
    assert detect_file_type(list(AP_EVENTS_COLUMNS)[:-1]) is None
    assert detect_file_type(list(AP_EVENTS_COLUMNS) + [AP_EVENTS_COLUMNS[0]]) is None


def test_dry_run_writes_nothing(indir, tmp_path, capsys):
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out), "--dry-run"]) == 0
    stdout = capsys.readouterr().out
    assert "dry-run: no files written." in stdout
    assert "[ap_metrics]" in stdout
    assert not out.exists() or not list(out.glob("*"))


def test_glob_input_pattern(indir, tmp_path):
    out = tmp_path / "out"
    assert main([str(indir / "ap_*.csv"), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.glob("*.csv")) == [
        "ap_events_20240101_0900_TZT.csv",
        "ap_metrics_20240101_0900_TZT.csv",
    ]
