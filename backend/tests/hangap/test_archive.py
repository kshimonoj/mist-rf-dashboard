"""分析結果の保存とローテート（``hangap.archive`` / ``/api/hangap/results``）のテスト。

合成データのみを使う（実データ・実データ由来の値は fixtures に置かない）。

このテストの主眼は **ローテートが自分のディレクトリの外を一切触らないこと**。
``scheduler.rotate_logs`` には「サイズ判定は data/logs の全ファイル、削除できるのは
Snapshot に載った ap_metrics だけ」という食い違いがあり、キャップを 2MB 超えただけで
ap_metrics が全滅した。同じ構造を作っていないことをここで固定する。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _synth as S
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hangap import analysis, archive, loader
from routers import hangap as api

START = datetime(2026, 1, 1, 10, 0, 0)
INTERVAL = 60

#: 「1〜3 サンプル → ゼロ 7 サンプル → 回復」。既定の min_zero_samples=5 を満たす。
HANG_PATTERN = [1, 1, 1] + [0] * 7 + [1, 1, 1]

MB = 1024 * 1024


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path):
    """``data/logs`` と ``data/hangap_results`` を持つ隔離ディレクトリ。"""
    root = tmp_path / "data"
    (root / "logs").mkdir(parents=True)
    (root / archive.RESULTS_DIR_NAME).mkdir(parents=True)
    return root


@pytest.fixture
def results_dir(data_dir):
    return data_dir / archive.RESULTS_DIR_NAME


@pytest.fixture
def api_client(data_dir, monkeypatch):
    """LOGS_DIR / RESULTS_DIR を隔離した TestClient を返す。"""
    monkeypatch.setattr(api, "LOGS_DIR", str(data_dir / "logs"))
    monkeypatch.setattr(api, "RESULTS_DIR", str(data_dir / archive.RESULTS_DIR_NAME))
    _clear_jobs()
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, data_dir
    _clear_jobs()


def _clear_jobs() -> None:
    with api._LOCK:
        for job in list(api._JOBS.values()):
            api._discard(job)
        api._JOBS.clear()


def _write_hang_logs(logs_dir: Path) -> Path:
    rows = [
        S.metrics_row(
            START + timedelta(seconds=INTERVAL * i),
            ap_id="test-ap-0000", ap_name="TEST-AP-00", num_clients=v,
        )
        for i, v in enumerate(HANG_PATTERN)
    ]
    return S.write_metrics(logs_dir / "ap_metrics_20260101_1000_TST.csv", rows)


def _analyze(client: TestClient, body: dict | None = None) -> dict:
    r = client.post("/api/hangap/analyze", json=body if body is not None else {})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        state = client.get(f"/api/hangap/jobs/{job_id}").json()
        if state["status"] != api.STATUS_RUNNING:
            return state
        time.sleep(0.05)
    raise AssertionError(f"ジョブが終わりませんでした: {job_id}")


def _make_set(results_dir: Path, stamp: str, *, size: int = 100, meta: dict | None = None) -> str:
    """保存済みの 1 組を直接作る（xlsx / csv / json）。"""
    name = f"{archive.NAME_PREFIX}{stamp}"
    (results_dir / f"{name}.xlsx").write_bytes(b"x" * size)
    (results_dir / f"{name}.csv").write_bytes(b"c" * size)
    payload = {"version": archive.META_VERSION, "name": name, "detected_intervals": 0}
    payload.update(meta or {})
    (results_dir / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return name


def _stamp(i: int) -> str:
    return (datetime(2026, 1, 1, 0, 0, 0) + timedelta(minutes=i)).strftime(archive.STAMP_FORMAT)


def _names(results_dir: Path) -> list[str]:
    return [s.name for s in archive.list_sets(results_dir)]


def _listing(results_dir: Path) -> list[str]:
    return sorted(p.name for p in results_dir.iterdir())


# ---------------------------------------------------------------------------
# 1. done で完了したジョブの結果が 3 点セットで保存される
# ---------------------------------------------------------------------------


def test_done_job_saves_xlsx_csv_json(api_client):
    client, data = api_client
    _write_hang_logs(data / "logs")

    state = _analyze(client)
    assert state["status"] == api.STATUS_DONE

    sets = archive.list_sets(data / archive.RESULTS_DIR_NAME)
    assert len(sets) == 1
    assert sorted(sets[0].members) == ["csv", "json", "xlsx"]
    # ファイル名に分析窓の時刻も入力ファイル名も含めない
    assert archive.is_valid_name(sets[0].name)


def test_saved_json_holds_what_the_filename_cannot(api_client):
    client, data = api_client
    _write_hang_logs(data / "logs")
    _analyze(client)

    (result_set,) = archive.list_sets(data / archive.RESULTS_DIR_NAME)
    meta = archive.read_meta(result_set)
    assert meta["detected_intervals"] == 1
    assert set(meta["recovery_status"]) == set(analysis.STATUS_ORDER)
    assert set(meta["neighbor_verdict"]) == set(analysis.VERDICT_ORDER)
    assert meta["condition_text"].startswith("分析条件:")
    assert meta["warning_count"] == len(meta["warnings"])
    assert meta["metrics_period"] and len(meta["metrics_period"]) == 2
    assert meta["ap_count"] == 1
    assert meta["saved_at"].endswith("Z")


def test_saved_files_are_identical_to_the_job_download(api_client):
    """保存されるのはジョブが書き出したファイルそのもの（書式を作り直していない）。"""
    client, data = api_client
    _write_hang_logs(data / "logs")
    state = _analyze(client)
    job_id = state["job_id"]

    (result_set,) = archive.list_sets(data / archive.RESULTS_DIR_NAME)
    for fmt in ("xlsx", "csv"):
        downloaded = client.get(f"/api/hangap/jobs/{job_id}/download?format={fmt}")
        assert downloaded.status_code == 200
        assert result_set.members[fmt].read_bytes() == downloaded.content


# ---------------------------------------------------------------------------
# 2. failed のジョブでは何も保存しない
# ---------------------------------------------------------------------------


def test_failed_job_saves_nothing(api_client):
    client, data = api_client
    # ap_events だけ（ap_metrics が 1 行も無い）→ failed
    S.write_events(data / "logs" / "ap_events_20260101_1000_TST.csv", [S.event_row(START)])

    state = _analyze(client)
    assert state["status"] == api.STATUS_FAILED
    assert _listing(data / archive.RESULTS_DIR_NAME) == []
    assert client.get("/api/hangap/results").json()["results"] == []


def test_zero_detections_is_still_saved(api_client):
    """検出 0 件は done。「分析できなかった」ではないので記録として残す。"""
    client, data = api_client
    S.write_metrics(
        data / "logs" / "ap_metrics_20260101_1000_TST.csv",
        [
            S.metrics_row(START + timedelta(seconds=INTERVAL * i), num_clients=1)
            for i in range(20)
        ],
    )
    state = _analyze(client)
    assert state["status"] == api.STATUS_DONE
    assert len(archive.list_sets(data / archive.RESULTS_DIR_NAME)) == 1


# ---------------------------------------------------------------------------
# 3. 対の一貫性（片方だけ残らない）
# ---------------------------------------------------------------------------


def test_rotation_removes_the_whole_set(results_dir):
    for i in range(3):
        _make_set(results_dir, _stamp(i))
    removed, freed = archive.rotate(results_dir, keep_files=1, keep_bytes=10 * MB)

    assert removed == 2
    assert freed > 0
    # 消えた組は 3 点とも消えている（xlsx だけ残る、といった状態を作らない）
    assert _names(results_dir) == [f"{archive.NAME_PREFIX}{_stamp(2)}"]
    assert _listing(results_dir) == sorted(
        f"{archive.NAME_PREFIX}{_stamp(2)}{s}" for s in archive.MEMBER_SUFFIXES
    )


def test_rotation_removes_incomplete_sets_as_one_unit(results_dir):
    """保存が途中で落ちた組（xlsx だけ）も 1 組として扱い、まとめて消す。"""
    name = f"{archive.NAME_PREFIX}{_stamp(0)}"
    (results_dir / f"{name}.xlsx").write_bytes(b"x" * 100)
    _make_set(results_dir, _stamp(1))

    archive.rotate(results_dir, keep_files=1, keep_bytes=10 * MB)
    assert _names(results_dir) == [f"{archive.NAME_PREFIX}{_stamp(1)}"]


# ---------------------------------------------------------------------------
# 4. 件数ローテート
# ---------------------------------------------------------------------------


def test_rotation_by_file_count(results_dir):
    for i in range(6):
        _make_set(results_dir, _stamp(i))

    removed, _ = archive.rotate(results_dir, keep_files=4, keep_bytes=10 * MB)
    assert removed == 2
    # 古い組から消える／下回るまで消す（4 組ちょうどで止まる）
    assert _names(results_dir) == [f"{archive.NAME_PREFIX}{_stamp(i)}" for i in range(2, 6)]


def test_rotation_stops_once_under_the_limits(results_dir):
    for i in range(3):
        _make_set(results_dir, _stamp(i))
    removed, freed = archive.rotate(results_dir, keep_files=50, keep_bytes=500 * MB)
    assert (removed, freed) == (0, 0)
    assert len(_names(results_dir)) == 3


# ---------------------------------------------------------------------------
# 5. サイズローテート
# ---------------------------------------------------------------------------


def test_rotation_by_total_size(results_dir):
    # 1 組 = 3,000 バイト（xlsx 1,000 + csv 1,000 + json）
    for i in range(5):
        _make_set(results_dir, _stamp(i), size=1000)
    total = sum(s.total_bytes for s in archive.list_sets(results_dir))
    per_set = total // 5

    removed, freed = archive.rotate(results_dir, keep_files=50, keep_bytes=per_set * 2)
    assert removed == 3
    assert freed == per_set * 3
    assert _names(results_dir) == [f"{archive.NAME_PREFIX}{_stamp(i)}" for i in (3, 4)]


# ---------------------------------------------------------------------------
# 6. 最新の 1 組は必ず残る
# ---------------------------------------------------------------------------


def test_latest_set_is_never_deleted(results_dir):
    for i in range(3):
        _make_set(results_dir, _stamp(i), size=1000)

    archive.rotate(results_dir, keep_files=0, keep_bytes=0)

    latest = f"{archive.NAME_PREFIX}{_stamp(2)}"
    assert _names(results_dir) == [latest]
    # 残るのは組ごと（3 点とも）
    assert _listing(results_dir) == sorted(f"{latest}{s}" for s in archive.MEMBER_SUFFIXES)


def test_single_set_is_not_deleted(results_dir):
    _make_set(results_dir, _stamp(0), size=1000)
    removed, freed = archive.rotate(results_dir, keep_files=0, keep_bytes=0)
    assert (removed, freed) == (0, 0)
    assert len(_names(results_dir)) == 1


# ---------------------------------------------------------------------------
# 7. 自己完結（data/logs を一切触らない）
#
#    rotate_logs は「判定対象（data/logs 全体）」と「削除対象（ap_metrics のみ）」が
#    食い違っていたために ap_metrics を全滅させた。ここでは両者が一致していること、
#    および自分のディレクトリの外を参照しないことを固定する。
# ---------------------------------------------------------------------------


def test_rotation_never_touches_data_logs(data_dir):
    logs_dir = data_dir / "logs"
    results_dir = data_dir / archive.RESULTS_DIR_NAME
    for i in range(20):
        (logs_dir / f"ap_metrics_20260101_{1000 + i:04d}_TST.csv").write_bytes(b"m" * 100_000)
    before = {p.name: p.stat().st_size for p in logs_dir.iterdir()}

    for i in range(5):
        _make_set(results_dir, _stamp(i), size=1000)

    # 上限を極端に小さくしても、消えるのは hangap_results の中だけ
    archive.rotate(results_dir, keep_files=1, keep_bytes=1)

    after = {p.name: p.stat().st_size for p in logs_dir.iterdir()}
    assert after == before, "data/logs のファイルが削除・変更された"
    assert len(before) == 20
    assert len(_names(results_dir)) == 1


def test_rotation_ignores_files_it_cannot_delete(data_dir):
    """判定対象と削除対象を一致させる。

    組として認識できないファイルは合計サイズにも入れない。入れてしまうと
    「消しても下回れないので消し続ける」= rotate_logs と同じ全滅が起きる。
    """
    results_dir = data_dir / archive.RESULTS_DIR_NAME
    stray = results_dir / "README.txt"
    stray.write_bytes(b"z" * 5 * MB)  # 単独でキャップを超える大きさ
    for i in range(3):
        _make_set(results_dir, _stamp(i), size=1000)

    removed, _ = archive.rotate(results_dir, keep_files=50, keep_bytes=1 * MB)

    assert removed == 0, "組でないファイルの容量で組を消してはいけない"
    assert len(_names(results_dir)) == 3
    assert stray.is_file()


def test_rotation_does_not_recurse_into_subdirectories(data_dir):
    results_dir = data_dir / archive.RESULTS_DIR_NAME
    nested = results_dir / "keep"
    nested.mkdir()
    kept = _make_set(nested, _stamp(0))
    for i in range(1, 4):
        _make_set(results_dir, _stamp(i))

    archive.rotate(results_dir, keep_files=1, keep_bytes=1)

    assert (nested / f"{kept}.xlsx").is_file()
    assert len(_names(results_dir)) == 1


def test_rotation_on_missing_directory_is_a_noop(tmp_path):
    assert archive.rotate(tmp_path / "does-not-exist", keep_files=1, keep_bytes=1) == (0, 0)


# ---------------------------------------------------------------------------
# 8. ローダが保存済み結果を拾わない
# ---------------------------------------------------------------------------


def _write_fake_result_csv(results_dir: Path, stamp: str) -> Path:
    """保存済み結果に似せた CSV（ローダのヘッダー判定では種別不明になる）。"""
    path = results_dir / f"{archive.NAME_PREFIX}{stamp}.csv"
    path.write_text("ap_name,区間番号,ゼロ開始,回復状況\nTEST-AP-00,1,,回復\n", encoding="utf-8")
    return path


def test_loader_does_not_scan_the_results_directory(data_dir):
    logs_dir = data_dir / "logs"
    results_dir = data_dir / archive.RESULTS_DIR_NAME
    _write_hang_logs(logs_dir)
    _write_fake_result_csv(results_dir, _stamp(0))
    _make_set(results_dir, _stamp(1))

    # data ディレクトリ全体を走査しても、結果ファイルは入力にならない
    files = analysis.collect_files(data_dir)
    assert [f.name for f in files] == ["ap_metrics_20260101_1000_TST.csv"]

    report = loader.load([data_dir]).report
    assert report.unclassified == [], "保存済み結果が入力として拾われた"
    assert report.files_scanned == 1
    assert report.metrics_rows == len(HANG_PATTERN)


def test_saved_results_do_not_break_the_next_analysis(api_client):
    """保存済み結果がある状態でもう一度分析できること（自分の出力を読み込まない）。"""
    client, data = api_client
    _write_hang_logs(data / "logs")

    first = _analyze(client)
    assert first["status"] == api.STATUS_DONE
    second = _analyze(client)
    assert second["status"] == api.STATUS_DONE
    assert second["summary"]["loader"]["unclassified"] == 0
    assert second["summary"]["loader"]["files_scanned"] == 1


# ---------------------------------------------------------------------------
# 9. トラバーサルの拒否
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "..",
        "../hangap_result_20260101_000000",
        "..%2Fetc%2Fpasswd",
        "/etc/passwd",
        "hangap_result_20260101_000000/../../secret",
        "hangap_result_20260101_000000.xlsx",
        "hangap_result_2026",
        "",
    ],
)
def test_invalid_names_are_rejected(name):
    assert not archive.is_valid_name(name)


def test_valid_name_is_accepted():
    assert archive.is_valid_name("hangap_result_20260101_000000")


@pytest.mark.parametrize("encoded", ["%2e%2e", "%2e%2e%2e", "..%00", "hangap_result_20260101"])
def test_traversal_names_are_400(api_client, encoded):
    """パスに載った ``{name}`` が想定の形でなければ 400（ファイルには触らない）。"""
    client, _ = api_client
    assert client.get(f"/api/hangap/results/{encoded}/download?format=xlsx").status_code == 400
    assert client.delete(f"/api/hangap/results/{encoded}").status_code == 400


def test_absolute_path_name_does_not_route_to_a_file(api_client, tmp_path):
    """``/`` を含む名前はルートに一致しない（= ハンドラまで届かない）。"""
    client, _ = api_client
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    r = client.get(f"/api/hangap/results/{secret}/download?format=csv")
    assert r.status_code in (400, 404)
    assert "secret" not in r.text


def test_download_bad_format_is_400(api_client):
    client, data = api_client
    name = _make_set(data / archive.RESULTS_DIR_NAME, _stamp(0))
    r = client.get(f"/api/hangap/results/{name}/download?format=pdf")
    assert r.status_code == 400


def test_download_missing_set_is_404(api_client):
    client, _ = api_client
    name = f"{archive.NAME_PREFIX}{_stamp(0)}"
    assert client.get(f"/api/hangap/results/{name}/download?format=xlsx").status_code == 404
    assert client.delete(f"/api/hangap/results/{name}").status_code == 404


# ---------------------------------------------------------------------------
# 10. 一覧
# ---------------------------------------------------------------------------


def test_list_is_newest_first_and_includes_the_json(api_client):
    client, data = api_client
    results_dir = data / archive.RESULTS_DIR_NAME
    for i in range(3):
        _make_set(
            results_dir, _stamp(i),
            size=100 * (i + 1),
            meta={"detected_intervals": i, "warning_count": i, "condition_text": f"分析条件: #{i}"},
        )

    rows = client.get("/api/hangap/results").json()["results"]
    assert [r["name"] for r in rows] == [f"{archive.NAME_PREFIX}{_stamp(i)}" for i in (2, 1, 0)]
    assert [r["detected_intervals"] for r in rows] == [2, 1, 0]
    assert [r["condition_text"] for r in rows] == ["分析条件: #2", "分析条件: #1", "分析条件: #0"]
    for row in rows:
        assert sorted(row["files"]) == ["csv", "json", "xlsx"]
        assert row["total_bytes"] == sum(row["files"].values())


def test_list_survives_a_broken_json(api_client):
    client, data = api_client
    results_dir = data / archive.RESULTS_DIR_NAME
    name = _make_set(results_dir, _stamp(0))
    (results_dir / f"{name}.json").write_text("{ broken", encoding="utf-8")

    rows = client.get("/api/hangap/results").json()["results"]
    assert [r["name"] for r in rows] == [name]
    # 形は崩さない（保存日時は名前から復元する）
    assert rows[0]["detected_intervals"] == 0
    assert rows[0]["saved_at"] == "2026-01-01T00:00:00Z"
    assert rows[0]["recovery_status"] == {}


def test_list_is_empty_when_nothing_is_saved(api_client):
    client, _ = api_client
    assert client.get("/api/hangap/results").json()["results"] == []


def test_saved_result_download_returns_the_stored_bytes(api_client):
    client, data = api_client
    results_dir = data / archive.RESULTS_DIR_NAME
    name = _make_set(results_dir, _stamp(0), size=64)

    for fmt in ("xlsx", "csv"):
        r = client.get(f"/api/hangap/results/{name}/download?format={fmt}")
        assert r.status_code == 200
        assert r.content == (results_dir / f"{name}.{fmt}").read_bytes()


# ---------------------------------------------------------------------------
# 11. 削除 API
# ---------------------------------------------------------------------------


def test_delete_removes_one_set_and_keeps_the_others(api_client):
    client, data = api_client
    results_dir = data / archive.RESULTS_DIR_NAME
    names = [_make_set(results_dir, _stamp(i)) for i in range(3)]

    r = client.delete(f"/api/hangap/results/{names[1]}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert r.json()["freed_bytes"] > 0

    # 消えたのは 1 組（3 点とも）だけ
    assert _names(results_dir) == [names[0], names[2]]
    assert not any(p.name.startswith(names[1]) for p in results_dir.iterdir())


# ---------------------------------------------------------------------------
# 上限は環境変数で上書きできる
# ---------------------------------------------------------------------------


def test_limits_default_to_the_documented_values(monkeypatch):
    monkeypatch.delenv(archive.ENV_MAX_FILES, raising=False)
    monkeypatch.delenv(archive.ENV_MAX_TOTAL_MB, raising=False)
    assert archive.max_files() == 50
    assert archive.max_total_bytes() == 500 * MB


def test_limits_can_be_overridden_by_env(monkeypatch, results_dir):
    monkeypatch.setenv(archive.ENV_MAX_FILES, "2")
    monkeypatch.setenv(archive.ENV_MAX_TOTAL_MB, "1")
    assert archive.max_files() == 2
    assert archive.max_total_bytes() == MB

    for i in range(5):
        _make_set(results_dir, _stamp(i))
    removed, _ = archive.rotate(results_dir)  # 引数なし = 環境変数の上限
    assert removed == 3
    assert len(_names(results_dir)) == 2


@pytest.mark.parametrize("raw", ["0", "-1", "abc", ""])
def test_invalid_env_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv(archive.ENV_MAX_FILES, raw)
    assert archive.max_files() == archive.DEFAULT_MAX_FILES


# ---------------------------------------------------------------------------
# 保存名の衝突（同じ秒に 2 回保存しても上書きしない）
# ---------------------------------------------------------------------------


def test_unique_name_shifts_on_collision(results_dir):
    dt = datetime(2026, 1, 1, 0, 0, 0)
    assert archive.unique_name(results_dir, dt) == f"{archive.NAME_PREFIX}{_stamp(0)}"
    _make_set(results_dir, _stamp(0))
    shifted = archive.unique_name(results_dir, dt)
    assert shifted == f"{archive.NAME_PREFIX}20260101_000001"


def test_save_writes_the_set_next_to_each_other(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.xlsx").write_bytes(b"x" * 10)
    (src / "a.csv").write_bytes(b"c" * 10)
    dest = tmp_path / archive.RESULTS_DIR_NAME

    name = archive.name_for(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    saved = archive.save(
        dest, name,
        {"xlsx": src / "a.xlsx", "csv": src / "a.csv"},
        archive.build_meta(
            name=name,
            saved_at=datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc),
            summary={"detected_intervals": 3, "loader": {"ap_count": 7}},
            warnings=["w1", "w2"],
        ),
    )
    assert sorted(saved.members) == ["csv", "json", "xlsx"]
    meta = json.loads((dest / f"{name}.json").read_text(encoding="utf-8"))
    assert meta["detected_intervals"] == 3
    assert meta["ap_count"] == 7
    assert meta["warning_count"] == 2
    assert meta["saved_at"] == "2026-01-01T12:00:05Z"
    assert os.path.basename(name) == name  # 名前にパス要素が混ざらない
