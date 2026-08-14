"""決定論性・ファイル間一貫性・タイムシフト一貫性。"""
import shutil
from datetime import datetime

from conftest import ALL_FIXTURES, read_csv

from pseudonymizer.cli import main

TS_FMT = "%Y-%m-%d %H:%M:%S"


def _run(indir, out, extra=None, salt_file=None):
    argv = [str(indir), "--out", str(out)]
    if salt_file:
        argv += ["--salt-file", str(salt_file)]
    argv += extra or []
    assert main(argv) == 0


def _read_bytes(d):
    return {name: (d / name).read_bytes() for name in ALL_FIXTURES}


def test_same_salt_twice_is_byte_identical(indir, tmp_path):
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    _run(indir, out1)
    # 1 回目に生成されたソルトを使い回す（マッピングもソルトの隣に残る）
    _run(indir, out2, salt_file=out1 / ".pseudonym_salt.json")
    assert _read_bytes(out1) == _read_bytes(out2)


def test_same_salt_without_mapping_cache_is_byte_identical(indir, tmp_path):
    """マッピングファイルが無くても、同じソルト・同じ入力なら同じ結果になる。"""
    out1 = tmp_path / "out1"
    _run(indir, out1)

    salt_copy_dir = tmp_path / "saltdir"
    salt_copy_dir.mkdir()
    shutil.copy(out1 / ".pseudonym_salt.json", salt_copy_dir / ".pseudonym_salt.json")

    out2 = tmp_path / "out2"
    _run(indir, out2, salt_file=salt_copy_dir / ".pseudonym_salt.json")
    assert _read_bytes(out1) == _read_bytes(out2)


def test_different_salt_gives_different_pseudonyms(indir, tmp_path):
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    _run(indir, out1)
    _run(indir, out2)  # out2 は独自のソルトを生成する
    a = {r["mac"] for r in read_csv(out1 / "ap_metrics_20240101_0900_TZT.csv")}
    b = {r["mac"] for r in read_csv(out2 / "ap_metrics_20240101_0900_TZT.csv")}
    # 番号空間が小さいので値が一致することはあり得るが、タイムオフセットは必ず変わる
    ts_a = read_csv(out1 / "ap_metrics_20240101_0900_TZT.csv")[0]["timestamp"]
    ts_b = read_csv(out2 / "ap_metrics_20240101_0900_TZT.csv")[0]["timestamp"]
    assert ts_a != ts_b or a != b


def test_ap_name_consistent_across_files(indir, tmp_path):
    """ap_metrics / ap_events / client_metrics / sle / floormap で同じ AP は同じ仮名。"""
    out = tmp_path / "out"
    _run(indir, out)

    src_metrics = read_csv(indir / "ap_metrics_20240101_0900_TZT.csv")
    dst_metrics = read_csv(out / "ap_metrics_20240101_0900_TZT.csv")
    name_map = {s["ap_name"]: d["ap_name"] for s, d in zip(src_metrics, dst_metrics)}
    assert len(name_map) == 2

    for filename in ("ap_events_20240101_0900_TZT.csv",
                     "client_metrics_20240101_0900_TZT.csv",
                     "sle_metrics_20240101_0900_TZT.csv"):
        src = read_csv(indir / filename)
        dst = read_csv(out / filename)
        for s, d in zip(src, dst):
            assert d["ap_name"] == name_map[s["ap_name"]]

    # floormap の ap_list も同じ仮名になる
    src_fm = read_csv(indir / "floormap_20240101_0900_TZT_summary.csv")
    dst_fm = read_csv(out / "floormap_20240101_0900_TZT_summary.csv")
    for s, d in zip(src_fm, dst_fm):
        expected = ",".join(name_map[n] for n in s["ap_list"].split(","))
        assert d["ap_list"] == expected


def test_ap_mac_and_ap_id_and_ap_name_share_the_same_number(indir, tmp_path):
    out = tmp_path / "out"
    _run(indir, out)
    for row in read_csv(out / "ap_metrics_20240101_0900_TZT.csv"):
        number = int(row["ap_name"].removeprefix("AP_"))
        assert row["ap_id"] == f"10000000-0000-4000-8000-{number:012d}"
        assert row["mac"] == f"020{number:09x}"


def test_site_pseudonym_consistent_across_files(indir, tmp_path):
    out = tmp_path / "out"
    _run(indir, out)
    names = set()
    for filename in ALL_FIXTURES:
        names |= {r["site_name"] for r in read_csv(out / filename)}
    assert len(names) == 1
    assert names.pop().startswith("SITE_")


def test_time_offset_identical_across_files_and_preserves_deltas(indir, tmp_path):
    out = tmp_path / "out"
    _run(indir, out)

    offsets = set()
    for filename in ALL_FIXTURES:
        col = "event_timestamp" if filename.startswith("ap_events") else "timestamp"
        src = read_csv(indir / filename)
        dst = read_csv(out / filename)
        for s, d in zip(src, dst):
            before = datetime.strptime(s[col], TS_FMT)
            after = datetime.strptime(d[col], TS_FMT)
            offsets.add(int((after - before).total_seconds()))

    assert len(offsets) == 1, "ファイル／行ごとにオフセットが異なる"
    offset = offsets.pop()
    assert offset != 0
    assert offset % (7 * 24 * 3600) == 0, "週単位のシフトなら曜日と時刻が保存される"

    # ファイル間の時刻差が保存されていること
    ap = read_csv(out / "ap_metrics_20240101_0900_TZT.csv")
    ev = read_csv(out / "ap_events_20240101_0900_TZT.csv")
    delta = (datetime.strptime(ev[1]["event_timestamp"], TS_FMT)
             - datetime.strptime(ap[0]["timestamp"], TS_FMT))
    assert delta.total_seconds() == 300


def test_no_time_shift_option(indir, tmp_path):
    out = tmp_path / "out"
    _run(indir, out, extra=["--no-time-shift"])
    src = read_csv(indir / "ap_metrics_20240101_0900_TZT.csv")
    dst = read_csv(out / "ap_metrics_20240101_0900_TZT.csv")
    assert [r["timestamp"] for r in src] == [r["timestamp"] for r in dst]


def test_incremental_run_reuses_previous_pseudonyms(indir, tmp_path):
    """後から一部のファイルだけ処理しても、同じ値には同じ仮名が付く。"""
    out1 = tmp_path / "out1"
    _run(indir, out1)
    full = read_csv(out1 / "ap_events_20240101_0900_TZT.csv")

    partial_in = tmp_path / "partial"
    partial_in.mkdir()
    shutil.copy(indir / "ap_events_20240101_0900_TZT.csv", partial_in)

    out2 = tmp_path / "out2"
    _run(partial_in, out2, salt_file=out1 / ".pseudonym_salt.json")
    partial = read_csv(out2 / "ap_events_20240101_0900_TZT.csv")
    assert full == partial
