"""仮名化ダウンロード API（/api/pseudonymize）のテスト。

要件 8（ソルト・マッピングが露出しないこと）・9（複数選択 ZIP）・10（上限）・
11（leak check）・12（xlsx 非対象）を固定する。合成データのみを使う。
"""
from __future__ import annotations

import csv
import io
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FIXTURES_DIR
from pseudonymizer import service
from pseudonymizer.salt import DEFAULT_MAP_FILENAME, DEFAULT_SALT_FILENAME
from routers import hangap as hangap_router
from routers import logs as logs_router
from routers import pseudonymize as api
from test_hangap_result import write_result_csv

LOG_FIXTURES = (
    "ap_metrics_20240101_0900_TZT.csv",
    "ap_events_20240101_0900_TZT.csv",
    "client_metrics_20240101_0900_TZT.csv",
)
RESULT_NAME = "hangap_result_20260101_120000"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """LOGS_DIR / RESULTS_DIR / ソルト置き場を隔離した TestClient。"""
    logs_dir = tmp_path / "data" / "logs"
    logs_dir.mkdir(parents=True)
    results_dir = tmp_path / "data" / "hangap_results"
    results_dir.mkdir(parents=True)

    for name in LOG_FIXTURES:
        shutil.copy(Path(FIXTURES_DIR) / name, logs_dir / name)
    write_result_csv(results_dir / f"{RESULT_NAME}.csv")
    (results_dir / f"{RESULT_NAME}.xlsx").write_bytes(b"PK\x03\x04 dummy")

    monkeypatch.setattr(logs_router, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(hangap_router, "RESULTS_DIR", str(results_dir))
    # ソルト・マッピングは data/ 直下（= logs_dir の外）に置く
    monkeypatch.setattr(service, "SALT_PATH", str(tmp_path / "data" / DEFAULT_SALT_FILENAME))
    monkeypatch.setattr(service, "MAP_PATH", str(tmp_path / "data" / DEFAULT_MAP_FILENAME))

    app = FastAPI()
    app.include_router(logs_router.router)
    app.include_router(api.router)
    with TestClient(app) as c:
        yield c, tmp_path / "data"


def parse_csv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


# ---------------------------------------------------------------------------
# ログ CSV
# ---------------------------------------------------------------------------


def test_single_file_returns_csv(client):
    c, _ = client
    res = c.get("/api/pseudonymize/logs", params={"files": LOG_FIXTURES[0]})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "_pseudonymized.csv" in res.headers["content-disposition"]
    rows = parse_csv(res.content)
    assert rows and rows[0]["ap_name"].startswith("AP_")
    assert rows[0]["site_name"].startswith("SITE_")


def test_multiple_files_return_a_zip_with_one_salt(client):
    """要件 9: 複数選択は ZIP。同一のソルトで変換されている。"""
    c, _ = client
    res = c.get("/api/pseudonymize/logs", params={"files": ",".join(LOG_FIXTURES[:2])})
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        names = zf.namelist()
        assert len(names) == 2
        assert all(n.endswith("_pseudonymized.csv") for n in names)
        metrics = parse_csv(zf.read([n for n in names if n.startswith("ap_metrics")][0]))
        events = parse_csv(zf.read([n for n in names if n.startswith("ap_events")][0]))

    # 同じ AP は同じ仮名、同じオフセット
    metrics_names = {r["ap_name"] for r in metrics}
    assert {r["ap_name"] for r in events} <= metrics_names
    assert metrics[0]["timestamp"][:10] == events[0]["event_timestamp"][:10]


def test_over_the_limit_is_rejected_with_the_limit(client):
    """要件 10: 上限を超えたらエラーで、上限が伝わる。"""
    c, _ = client
    files = ",".join(f"ap_metrics_20240101_{i:04d}_TZT.csv" for i in range(service.MAX_FILES + 1))
    res = c.get("/api/pseudonymize/logs", params={"files": files})
    assert res.status_code == 400
    assert str(service.MAX_FILES) in res.json()["detail"]


def test_unknown_and_missing_files_are_rejected(client):
    c, _ = client
    assert c.get("/api/pseudonymize/logs", params={"files": ""}).status_code == 400
    assert c.get("/api/pseudonymize/logs", params={"files": "../../etc/passwd"}).status_code == 400
    res = c.get("/api/pseudonymize/logs", params={"files": "ap_metrics_20991231_0000_TZT.csv"})
    assert res.status_code == 404


def test_limits_endpoint_reports_the_cap(client):
    c, _ = client
    # 復元側の上限も同じエンドポイントで返す（詳細は test_restore_api）
    assert c.get("/api/pseudonymize/limits").json()["max_files"] == service.MAX_FILES


# ---------------------------------------------------------------------------
# 要件 8: ソルト・マッピングが露出しないこと
# ---------------------------------------------------------------------------


def test_salt_and_map_live_outside_the_logs_dir(client):
    c, data_dir = client
    c.get("/api/pseudonymize/logs", params={"files": LOG_FIXTURES[0]})
    salt = Path(service.SALT_PATH)
    assert salt.is_file()
    assert salt.parent == data_dir
    assert salt.parent != Path(logs_router.LOGS_DIR)
    assert Path(logs_router.LOGS_DIR).parent == data_dir


def test_logs_api_neither_lists_nor_serves_the_salt(client):
    """ソルト・マッピングを logs ディレクトリに置いても、一覧にも出ず落とせない。"""
    c, _ = client
    logs_dir = Path(logs_router.LOGS_DIR)
    for name in (DEFAULT_SALT_FILENAME, DEFAULT_MAP_FILENAME):
        (logs_dir / name).write_text('{"secret": 1}')

    listed = {f["filename"] for f in c.get("/api/logs").json()["files"]}
    for name in (DEFAULT_SALT_FILENAME, DEFAULT_MAP_FILENAME):
        assert name not in listed
        assert c.get(f"/api/logs/{name}").status_code == 400
        assert c.get(f"/api/logs/{name}/download").status_code == 400
        assert c.get("/api/logs/download-zip", params={"files": name}).status_code == 400
        # 仮名化ダウンロードの導線からも取れない
        assert c.get("/api/pseudonymize/logs", params={"files": name}).status_code == 400


# ---------------------------------------------------------------------------
# 分析結果
# ---------------------------------------------------------------------------


def test_saved_result_csv_is_pseudonymized(client):
    c, _ = client
    res = c.get(f"/api/pseudonymize/results/{RESULT_NAME}")
    assert res.status_code == 200
    assert res.content.startswith(b"\xef\xbb\xbf")
    rows = parse_csv(res.content)
    assert rows[0]["ap_name"].startswith("AP_")
    assert rows[0]["回復状況"] == "回復"
    assert "20260101" not in res.headers["content-disposition"]


def test_xlsx_is_refused(client):
    """要件 12: xlsx は仮名化ダウンロードの対象外。"""
    c, _ = client
    res = c.get(f"/api/pseudonymize/results/{RESULT_NAME}", params={"format": "xlsx"})
    assert res.status_code == 400
    assert "xlsx" in res.json()["detail"]


def test_invalid_and_missing_result_names(client):
    c, _ = client
    assert c.get("/api/pseudonymize/results/not_a_result").status_code == 400
    assert c.get("/api/pseudonymize/results/hangap_result_20991231_235959").status_code == 404


def test_logs_and_result_agree_on_the_same_ap(client):
    """要件 5・6: 別々のリクエストで落としても同じ AP は同じ仮名になる。"""
    c, _ = client
    metrics = parse_csv(
        c.get("/api/pseudonymize/logs", params={"files": LOG_FIXTURES[0]}).content
    )
    result = parse_csv(c.get(f"/api/pseudonymize/results/{RESULT_NAME}").content)
    src = list(csv.DictReader((Path(logs_router.LOGS_DIR) / LOG_FIXTURES[0]).open()))
    mapping = {s["ap_name"]: m["ap_name"] for s, m in zip(src, metrics)}
    assert result[0]["ap_name"] == mapping["TEST-AP-01"]


def test_repeated_downloads_are_identical(client):
    """要件 6: 時間をおいて落としても仮名は変わらない。"""
    c, _ = client
    first = c.get(f"/api/pseudonymize/results/{RESULT_NAME}")
    second = c.get(f"/api/pseudonymize/results/{RESULT_NAME}")
    assert first.content == second.content
    assert first.headers["content-disposition"] == second.headers["content-disposition"]


# ---------------------------------------------------------------------------
# 要件 11: leak check
# ---------------------------------------------------------------------------


def test_leak_check_fails_without_returning_a_partial_file(client):
    """1 ファイルでも漏れたら 1 件も返さない。エラーに値は含めない。"""
    c, _ = client
    results_dir = Path(hangap_router.RESULTS_DIR)
    from test_hangap_result import SYNTH_ROWS

    leaky = [dict(SYNTH_ROWS[0])]
    leaky[0]["Event詳細"] = "reason=東京本社ビル"
    write_result_csv(results_dir / f"{RESULT_NAME}.csv", leaky)

    res = c.get(f"/api/pseudonymize/results/{RESULT_NAME}")
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "leak check" in detail
    assert "Event詳細" in detail
    assert "東京本社ビル" not in detail
    assert "AP_0" not in detail  # 変換後の行そのものも返さない


def test_a_leaky_file_blocks_the_whole_zip(client):
    """ZIP の一部が漏れていたら、ZIP ごと返さない。"""
    c, _ = client
    logs_dir = Path(logs_router.LOGS_DIR)
    broken = logs_dir / "ap_metrics_20240101_0901_TZT.csv"
    rows = list(csv.DictReader((logs_dir / LOG_FIXTURES[0]).open()))
    header = list(rows[0].keys())
    rows[0]["model"] = "ffeeddccbbaa"  # 変換されない列に実在 OUI 風の MAC を置く
    with broken.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)

    res = c.get(
        "/api/pseudonymize/logs", params={"files": f"{LOG_FIXTURES[0]},{broken.name}"}
    )
    assert res.status_code == 422
    assert res.headers["content-type"].startswith("application/json")
