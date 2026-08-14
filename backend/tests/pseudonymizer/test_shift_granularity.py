"""指示 06 修正 3: タイムシフト粒度(既定 day)と旧ソルトファイルとの後方互換。"""
import json
from datetime import datetime

from conftest import read_csv

from pseudonymizer.cli import main
from pseudonymizer.salt import SaltMaterial, save_salt

TS_FMT = "%Y-%m-%d %H:%M:%S"


def test_default_day_granularity_preserves_time_of_day_and_shifts_date(indir, tmp_path):
    """既定(day)では時刻は保存され、日付(曜日)はずれる。"""
    salt_path = tmp_path / "salt" / ".pseudonym_salt.json"
    material = SaltMaterial(
        salt=b"\x02" * 32,
        time_offset_seconds=-3 * 24 * 3600,  # 3日シフト。週の倍数ではない
        created_at="2024-01-01T00:00:00+00:00",
        shift_granularity="day",
    )
    save_salt(str(salt_path), material)

    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out), "--salt-file", str(salt_path)]) == 0

    src = read_csv(indir / "ap_metrics_20240101_0900_TZT.csv")
    dst = read_csv(out / "ap_metrics_20240101_0900_TZT.csv")
    before = datetime.strptime(src[0]["timestamp"], TS_FMT)
    after = datetime.strptime(dst[0]["timestamp"], TS_FMT)

    assert after.time() == before.time(), "時刻は保存されるはず"
    assert (before - after).days == 3
    assert after.weekday() != before.weekday(), "3日シフトなら曜日はずれるはず"


def test_shift_granularity_week_flag_still_available(indir, tmp_path):
    """--shift-granularity week で新規ソルトを生成すると、週単位のオフセットになる。"""
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out), "--shift-granularity", "week"]) == 0
    salt_path = out / ".pseudonym_salt.json"
    data = json.loads(salt_path.read_text(encoding="utf-8"))
    assert data["shift_granularity"] == "week"
    assert data["time_offset_seconds"] % (7 * 24 * 3600) == 0


def test_salt_file_without_granularity_field_is_treated_as_week_with_warning(indir, tmp_path, capsys):
    """粒度の記録が無い既存ソルトファイルは week として扱われ、警告が出る。"""
    salt_path = tmp_path / "salt" / ".pseudonym_salt.json"
    material = SaltMaterial(
        salt=b"\x04" * 32,
        time_offset_seconds=-7 * 24 * 3600,
        created_at="2024-01-01T00:00:00+00:00",
    )
    save_salt(str(salt_path), material)

    # 粒度フィールドが無い旧形式を模倣する
    data = json.loads(salt_path.read_text(encoding="utf-8"))
    del data["shift_granularity"]
    salt_path.write_text(json.dumps(data), encoding="utf-8")

    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out), "--salt-file", str(salt_path)]) == 0
    err = capsys.readouterr().err
    assert "shift granularity" in err
    assert "week" in err

    # クラッシュせず、記録されていたオフセットがそのまま使われること
    src = read_csv(indir / "ap_metrics_20240101_0900_TZT.csv")
    dst = read_csv(out / "ap_metrics_20240101_0900_TZT.csv")
    before = datetime.strptime(src[0]["timestamp"], TS_FMT)
    after = datetime.strptime(dst[0]["timestamp"], TS_FMT)
    assert (before - after).days == 7
