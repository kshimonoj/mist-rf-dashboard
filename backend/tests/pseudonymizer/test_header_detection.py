"""指示 06 修正 1: 種別判定のヘッダーベース化。ファイル名は判定に使わない。"""
import os
import shutil

from conftest import FIXTURES_DIR, read_csv

from pseudonymizer.cli import main


def test_filename_does_not_affect_detection(tmp_path):
    """同一スキーマならファイル名が何であっても同じ種別に判定される。"""
    src = os.path.join(FIXTURES_DIR, "ap_events_20240101_0900_TZT.csv")
    din = tmp_path / "in"
    din.mkdir()
    names = ("foo.csv", "bar_manual.csv", "baz_test.csv")
    for name in names:
        shutil.copy(src, din / name)

    out = tmp_path / "out"
    assert main([str(din), "--out", str(out)]) == 0

    for name in names:
        rows = read_csv(out / name)
        assert rows, f"{name}: no rows written"
        assert all(r["ap_mac"].startswith("020") for r in rows), (
            f"{name} was not processed as ap_events (ap_mac not pseudonymized)"
        )


def test_ap_events_backfill_absorbed_without_new_definition(tmp_path):
    """ap_events_backfill_*.csv は ap_events と同一ヘッダーなので、追加定義なしで吸収される。"""
    src = os.path.join(FIXTURES_DIR, "ap_events_20240101_0900_TZT.csv")
    din = tmp_path / "in"
    din.mkdir()
    shutil.copy(src, din / "ap_events_backfill_20240101_0900_TZT.csv")

    out = tmp_path / "out"
    assert main([str(din), "--out", str(out)]) == 0

    rows = read_csv(out / "ap_events_backfill_20240101_0900_TZT.csv")
    assert rows
    assert all(r["ap_mac"].startswith("020") for r in rows)


def test_floormap_manual_summary_absorbed_without_new_definition(tmp_path):
    """floormap_*_manual_summary.csv は floormap_summary と同一ヘッダーなので吸収される。"""
    src = os.path.join(FIXTURES_DIR, "floormap_20240101_0900_TZT_summary.csv")
    din = tmp_path / "in"
    din.mkdir()
    shutil.copy(src, din / "floormap_20260516_111448_JST_manual_summary.csv")

    out = tmp_path / "out"
    assert main([str(din), "--out", str(out)]) == 0

    rows = read_csv(out / "floormap_20260516_111448_JST_manual_summary.csv")
    assert rows
    assert all(r["map_name"].startswith("FLOOR_") for r in rows)


def test_unknown_schema_errors_without_leaking_row_values(tmp_path, capsys):
    """既知のどの種別のヘッダーとも一致しない場合はエラー停止し、行データの値は出力しない。"""
    din = tmp_path / "in"
    din.mkdir()
    secret = "SECRET_CUSTOMER_VALUE_12345"
    (din / "mystery_report_20260101.csv").write_text(
        f"weird_col_a,weird_col_b\n{secret},other_value\n", encoding="utf-8"
    )

    out = tmp_path / "out"
    assert main([str(din), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "cannot determine file type" in err
    assert secret not in err
    assert "other_value" not in err
    assert not list(out.glob("*.csv"))
