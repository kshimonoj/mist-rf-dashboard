"""RRM / RADAR チャネル変更分析 API（/api/rrm）のテスト。

合成データのみを使う。このテストは「API が CLI と同じ結果を返すこと」を要に
している。ロジックを API 側で書き直すと真っ先にここが落ちる。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import _rrmsynth as S
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hangap import loader as hangap_loader
from rrm import analysis, archive, cli, loader
from rrm.analysis import RESULT_COLUMNS
from routers import rrm as api

START = datetime(2026, 1, 1, 10, 0, 0)


def _samples(n: int) -> list[dict[str, object]]:
    return [{"num_clients": 5, "util_24": 10, "util_5": 20, "util_6": 30} for _ in range(n)]


def write_logs(logs_dir: Path) -> None:
    rows = (
        S.series(START, _samples(24), ap=S.AP1)
        + S.series(START, _samples(24), ap=S.AP2)
        + S.series(START, _samples(24), ap=S.AP3,
                   site_id=S.OTHER_SITE_ID, site_name=S.OTHER_SITE_NAME)
    )
    S.write_metrics(logs_dir / "ap_metrics_20260101_1000_TZT.csv", rows)
    S.write_events(logs_dir / "ap_events_20260101_1000_TZT.csv", [
        S.rrm_action(START + timedelta(minutes=7), pre_channel=36, channel=44, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=17), pre_channel=44, channel=44, ap=S.AP1),
        S.radar_detected(START + timedelta(minutes=27), pre_channel=64, channel=36, ap=S.AP2),
        S.rrm_action(START + timedelta(minutes=27, seconds=2), reason="radar-detected",
                     pre_channel=64, channel=36, ap=S.AP2),
        S.rrm_action(START + timedelta(minutes=37), reason="post-radar",
                     pre_channel=36, channel=40, ap=S.AP2),
        S.radar_detected(START + timedelta(minutes=42), pre_channel=52, channel=40, ap=S.AP1),
        S.rrm_action(START + timedelta(minutes=47), pre_channel=36, channel=44, ap=S.AP3,
                     site_name=S.OTHER_SITE_NAME),
        S.config_changed_by_rrm(START + timedelta(minutes=7), ap=S.AP1),
    ])


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """LOGS_DIR / RESULTS_DIR を隔離したディレクトリに向けた TestClient を返す。"""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr(api, "LOGS_DIR", str(logs_dir))
    # 保存先は logs_dir の外に置く（配下に置くと次の分析が自分の出力を読む）
    monkeypatch.setattr(api, "RESULTS_DIR", str(tmp_path / "rrm_results"))

    _clear_jobs()
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, logs_dir
    _clear_jobs()


def _clear_jobs() -> None:
    with api._LOCK:
        for job in list(api._JOBS.values()):
            api._discard(job)
        api._JOBS.clear()


def _analyze(client, body: dict | None = None) -> dict:
    r = client.post("/api/rrm/analyze", json=body if body is not None else {})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    for _ in range(200):
        state = client.get(f"/api/rrm/jobs/{job_id}").json()
        if state["status"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("ジョブが終わりません")


def _done(client, body: dict | None = None) -> dict:
    state = _analyze(client, body)
    assert state["status"] == "done", state.get("error")
    return state


# ---------------------------------------------------------------------------
# 基本
# ---------------------------------------------------------------------------


def test_analyze_returns_rows_meta_and_warnings(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    state = _done(client)

    result = client.get(f"/api/rrm/jobs/{state['job_id']}/result").json()
    assert result["columns"] == list(RESULT_COLUMNS)
    assert len(result["rows"]) == 5
    assert set(result["rows"][0]) == set(RESULT_COLUMNS)

    meta = result["meta"]
    assert meta["event_count"] == 5
    assert meta["change_count"] == 4
    assert meta["noop_count"] == 1
    assert meta["radar_detected"] == 2
    assert meta["radar_without_action"] == 1
    assert meta["config_changed_by_rrm_count"] == 1
    assert meta["changes_by_class"] == {"RADAR": 1, "POST_RADAR": 1, "RRM": 2}


def test_sites_can_be_omitted_and_repeated(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)

    every = _done(client)["meta"]
    assert every["event_count"] == 5

    one = _done(client, {"sites": [S.SITE_ID]})["meta"]
    assert one["event_count"] == 4
    assert [s["site_name"] for s in one["by_site"]] == [S.SITE_NAME]

    both = _done(client, {"sites": [S.SITE_ID, S.OTHER_SITE_ID]})["meta"]
    assert both["event_count"] == 5


def test_unknown_body_field_is_rejected(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    r = client.post("/api/rrm/analyze", json={"site": S.SITE_ID})
    assert r.status_code == 400
    assert "site" in r.json()["detail"]


def test_sites_must_be_a_list_of_strings(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    assert client.post("/api/rrm/analyze", json={"sites": S.SITE_ID}).status_code == 400
    assert client.post("/api/rrm/analyze", json={"sites": [""]}).status_code == 400


def test_to_before_from_is_rejected(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    r = client.post(
        "/api/rrm/analyze",
        json={"from": "2026-01-01 11:00", "to": "2026-01-01 10:00"},
    )
    assert r.status_code == 400


def test_unknown_site_fails_the_job(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    state = _analyze(client, {"sites": ["no-such-site"]})
    assert state["status"] == "failed"
    assert "見つかりません" in state["error"]


def test_window_is_half_open_through_the_api(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    state = _done(client, {"from": "2026-01-01 10:07:00", "to": "2026-01-01 10:17:00"})
    result = client.get(f"/api/rrm/jobs/{state['job_id']}/result").json()
    stamps = [row["event_timestamp"] for row in result["rows"]]
    assert stamps == ["2026-01-01 10:07:00"]


def test_sites_endpoint_lists_log_sites(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    body = client.get("/api/rrm/sites").json()
    assert {s["site_id"] for s in body["sites"]} == {S.SITE_ID, S.OTHER_SITE_ID}


def test_second_job_conflicts(api_client, monkeypatch):
    """同時に走るジョブは 1 つまで。2 本目は 409 で実行中の job_id を返す。"""
    client, logs_dir = api_client
    write_logs(logs_dir)

    real_run = analysis.run_analysis

    def slow_run(files, params, on_phase=None):
        time.sleep(0.5)
        return real_run(files, params, on_phase=on_phase)

    monkeypatch.setattr(api.analysis, "run_analysis", slow_run)

    first = client.post("/api/rrm/analyze", json={})
    assert first.status_code == 202
    second = client.post("/api/rrm/analyze", json={})
    assert second.status_code == 409
    assert second.json()["detail"]["job_id"] == first.json()["job_id"]


# ---------------------------------------------------------------------------
# ダウンロード（CLI と同一のファイルであること）
# ---------------------------------------------------------------------------


def test_download_csv_matches_cli_output_byte_for_byte(api_client, tmp_path):
    client, logs_dir = api_client
    write_logs(logs_dir)
    job_id = _done(client)["job_id"]

    out_dir = tmp_path / "cli_out"
    assert cli.main([
        "analyze", "--logs", str(logs_dir), "--out", str(out_dir), "--format", "both",
    ]) == cli.EXIT_OK
    cli_csv = next(out_dir.glob("*.csv")).read_bytes()

    r = client.get(f"/api/rrm/jobs/{job_id}/download?format=csv")
    assert r.status_code == 200
    assert r.content == cli_csv


def test_download_xlsx_is_a_workbook(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    job_id = _done(client)["job_id"]
    r = client.get(f"/api/rrm/jobs/{job_id}/download?format=xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"


def test_unknown_download_format_is_400(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    job_id = _done(client)["job_id"]
    assert client.get(f"/api/rrm/jobs/{job_id}/download?format=pdf").status_code == 400


# ---------------------------------------------------------------------------
# 保存済みの分析結果
# ---------------------------------------------------------------------------


def test_done_job_is_archived_and_can_be_read_back(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    state = _done(client)

    results = client.get("/api/rrm/results").json()["results"]
    assert len(results) == 1
    name = results[0]["name"]
    assert archive.is_valid_name(name)
    assert results[0]["change_count"] == state["meta"]["change_count"]

    saved = client.get(f"/api/rrm/results/{name}/rows").json()
    live = client.get(f"/api/rrm/jobs/{state['job_id']}/result").json()
    assert saved["columns"] == live["columns"]
    assert saved["rows"] == live["rows"]


def test_saved_result_download_and_delete(api_client):
    client, logs_dir = api_client
    write_logs(logs_dir)
    _done(client)
    name = client.get("/api/rrm/results").json()["results"][0]["name"]

    assert client.get(f"/api/rrm/results/{name}/download?format=csv").status_code == 200
    assert client.get(f"/api/rrm/results/{name}/download?format=xlsx").status_code == 200

    assert client.delete(f"/api/rrm/results/{name}").json()["deleted"] is True
    assert client.get("/api/rrm/results").json()["results"] == []


def test_invalid_saved_name_is_rejected(api_client):
    client, _ = api_client
    assert client.get("/api/rrm/results/..%2Fetc/rows").status_code in (400, 404)
    assert client.get("/api/rrm/results/not_a_name/rows").status_code == 400


# ---------------------------------------------------------------------------
# 出力を入力として拾わないこと（EXCLUDED_DIR_NAMES）
# ---------------------------------------------------------------------------


def _result_csv_in_logs(logs_dir: Path) -> Path:
    """``data/logs`` の下に結果ディレクトリがある状況を作る（実運用では外に置く）。"""
    path = logs_dir / archive.RESULTS_DIR_NAME / "rrm_result_20260101_100000.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(RESULT_COLUMNS) + "\n", encoding="utf-8")
    return path


def test_results_dir_is_excluded_from_the_log_scan(tmp_path):
    path = _result_csv_in_logs(tmp_path / "logs")
    assert archive.RESULTS_DIR_NAME in hangap_loader.EXCLUDED_DIR_NAMES
    assert loader.is_data_file(path) is False
    assert path not in loader.collect_files(tmp_path / "logs")


def test_negative_control_removing_the_exclusion_lets_the_result_csv_be_scanned(
    tmp_path, monkeypatch
):
    """``EXCLUDED_DIR_NAMES`` から一時的に外すと、上のテストの前提が崩れること。

    除外が効いているのか、たまたま拾われていないだけなのかを区別するための
    負のコントロール。
    """
    path = _result_csv_in_logs(tmp_path / "logs")
    monkeypatch.setattr(
        hangap_loader, "EXCLUDED_DIR_NAMES",
        frozenset(hangap_loader.EXCLUDED_DIR_NAMES - {archive.RESULTS_DIR_NAME}),
    )
    assert loader.is_data_file(path) is True
    assert path in loader.collect_files(tmp_path / "logs")


def test_results_dir_is_not_read_as_input(api_client, monkeypatch, tmp_path):
    """保存済みの結果が ``data/logs`` 配下にあっても、次の分析が拾わないこと。

    保存先をわざと logs の中に向けて、除外がディレクトリ名で効いていることを見る。
    """
    client, logs_dir = api_client
    monkeypatch.setattr(api, "RESULTS_DIR", str(logs_dir / archive.RESULTS_DIR_NAME))
    write_logs(logs_dir)

    first = _done(client)["meta"]
    saved = client.get("/api/rrm/results").json()["results"]
    assert len(saved) == 1
    assert (logs_dir / archive.RESULTS_DIR_NAME / f"{saved[0]['name']}.csv").is_file()

    second = _done(client)["meta"]
    assert second["event_count"] == first["event_count"]
    assert second["change_count"] == first["change_count"]
    assert second["warnings"] == first["warnings"]
    # 走査ファイル数が増えず、種別不明も出ない（＝結果 csv を読みに行っていない）
    assert second["files_scanned"] == first["files_scanned"]
    assert second["unclassified_count"] == 0
