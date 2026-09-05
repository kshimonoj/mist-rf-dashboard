"""横断レポート API（/api/report）のテスト。合成データのみを使う。

このテストは「API が CLI と同じレポートを返すこと」を要にしている。組み立てを
ルーター側で書き直すと真っ先にここが落ちる。
"""
from __future__ import annotations

import time

import _repsynth as S
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pptx import Presentation

from report import analysis
from routers import report as api


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """DATA_DIR を隔離したディレクトリに向けた TestClient を返す。"""
    monkeypatch.setattr(api, "DATA_DIR", str(tmp_path))
    dirs = analysis.ResultsDirs.under(tmp_path)
    S.write_hangap(dirs.hangap)
    S.write_floorpeak(dirs.floorpeak)
    S.write_rrm(dirs.rrm)

    _clear_jobs()
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client
    _clear_jobs()


def _clear_jobs() -> None:
    with api._LOCK:
        for job in list(api._JOBS.values()):
            api._discard(job)
        api._JOBS.clear()


def _wait(client: TestClient, job_id: str, timeout: float = 30.0) -> dict:
    """ジョブが終わるまで待つ（生成は分析より速いが、スレッドなので待つ）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/report/jobs/{job_id}").json()
        if state["status"] != api.STATUS_RUNNING:
            return state
        time.sleep(0.05)
    raise AssertionError(f"ジョブが終わりませんでした: {job_id}")


def _generate(client: TestClient, body: dict) -> dict:
    started = client.post("/api/report/generate", json=body)
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    state = _wait(client, job_id)
    assert state["status"] == api.STATUS_DONE, state
    return client.get(f"/api/report/jobs/{job_id}/result").json()


# ---------------------------------------------------------------------------
# パラメータの検証
# ---------------------------------------------------------------------------


def test_generate_without_selection_is_400(api_client):
    res = api_client.post("/api/report/generate", json={})
    assert res.status_code == 400
    assert "選ばれていません" in res.json()["detail"]


def test_generate_with_only_nulls_is_400(api_client):
    res = api_client.post(
        "/api/report/generate",
        json={"hangap_result": None, "floorpeak_result": None, "rrm_result": None},
    )
    assert res.status_code == 400


def test_unknown_field_is_400(api_client):
    res = api_client.post("/api/report/generate", json={"hangap": S.HANGAP_NAME})
    assert res.status_code == 400
    assert "不明なフィールド" in res.json()["detail"]


def test_non_string_name_is_400(api_client):
    res = api_client.post("/api/report/generate", json={"rrm_result": 123})
    assert res.status_code == 400


def test_missing_result_fails_the_job(api_client):
    """存在しない結果名は 400 ではなくジョブの failed（読み込みはワーカーで行う）。"""
    started = api_client.post(
        "/api/report/generate", json={"rrm_result": "rrm_result_20991231_235959"}
    )
    assert started.status_code == 202
    state = _wait(api_client, started.json()["job_id"])
    assert state["status"] == api.STATUS_FAILED
    assert "見つかりません" in state["error"]


def test_invalid_name_fails_the_job(api_client):
    started = api_client.post("/api/report/generate", json={"rrm_result": "../etc/passwd"})
    assert started.status_code == 202
    state = _wait(api_client, started.json()["job_id"])
    assert state["status"] == api.STATUS_FAILED


# ---------------------------------------------------------------------------
# 生成 → 結果 → ダウンロード
# ---------------------------------------------------------------------------


def test_single_section_report(api_client):
    result = _generate(api_client, {"rrm_result": S.RRM_NAME})
    assert [s["section"] for s in result["sections"]] == ["rrm"]
    assert {s["section"] for s in result["slides"]} == {"cover", "rrm"}
    assert result["slide_count"] == 1 + 3
    assert result["filename"].startswith("report_")
    assert result["filename"].endswith(".pptx")


def test_section_order_is_fixed(api_client):
    """リクエストのキー順を変えても章の順序は Hang AP → Floor Peak → RRM。"""
    forward = _generate(api_client, {
        "hangap_result": S.HANGAP_NAME,
        "floorpeak_result": S.FLOORPEAK_NAME,
        "rrm_result": S.RRM_NAME,
    })
    backward = _generate(api_client, {
        "rrm_result": S.RRM_NAME,
        "floorpeak_result": S.FLOORPEAK_NAME,
        "hangap_result": S.HANGAP_NAME,
    })
    assert [s["section"] for s in forward["sections"]] == ["hangap", "floorpeak", "rrm"]
    assert [s["section"] for s in backward["sections"]] == ["hangap", "floorpeak", "rrm"]
    assert [s["title"] for s in backward["slides"]] == [s["title"] for s in forward["slides"]]


def test_download_returns_readable_pptx(api_client, tmp_path):
    started = api_client.post(
        "/api/report/generate",
        json={"hangap_result": S.HANGAP_NAME, "rrm_result": S.RRM_NAME},
    )
    job_id = started.json()["job_id"]
    _wait(api_client, job_id)
    result = api_client.get(f"/api/report/jobs/{job_id}/result").json()

    res = api_client.get(result["download_url"])
    assert res.status_code == 200
    assert res.headers["content-type"] == api.PPTX_MEDIA_TYPE

    path = tmp_path / result["filename"]
    path.write_bytes(res.content)
    prs = Presentation(str(path))
    assert len(prs.slides) == result["slide_count"] == 1 + 3 + 3


def test_result_before_done_is_409(api_client):
    """まだ done でないジョブの結果は 409（空のファイルを返さない）。"""
    started = api_client.post(
        "/api/report/generate", json={"rrm_result": "rrm_result_20991231_235959"}
    )
    job_id = started.json()["job_id"]
    _wait(api_client, job_id)
    assert api_client.get(f"/api/report/jobs/{job_id}/result").status_code == 409
    assert api_client.get(f"/api/report/jobs/{job_id}/download").status_code == 409


def test_unknown_job_is_404(api_client):
    assert api_client.get("/api/report/jobs/deadbeef").status_code == 404
    assert api_client.get("/api/report/jobs/deadbeef/result").status_code == 404


def test_delete_job_discards_the_file(api_client):
    started = api_client.post("/api/report/generate", json={"rrm_result": S.RRM_NAME})
    job_id = started.json()["job_id"]
    _wait(api_client, job_id)
    output = api._JOBS[job_id].output
    assert output is not None and output.is_file()

    assert api_client.delete(f"/api/report/jobs/{job_id}").json()["deleted"] is True
    assert not output.exists()
    assert api_client.get(f"/api/report/jobs/{job_id}").status_code == 404


def test_job_state_lists_requested_sections(api_client):
    started = api_client.post(
        "/api/report/generate",
        json={"rrm_result": S.RRM_NAME, "hangap_result": S.HANGAP_NAME},
    )
    state = _wait(api_client, started.json()["job_id"])
    assert [s["section"] for s in state["sections"]] == ["hangap", "rrm"]
    assert [s["name"] for s in state["sections"]] == [S.HANGAP_NAME, S.RRM_NAME]
