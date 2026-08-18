"""復元 API（POST /api/pseudonymize/restore）のテスト。指示 24 の要件 12・13 を含む。

合成データのみ。実データは使わない。
"""
from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pseudonymizer import restore_service, service
from pseudonymizer.salt import DEFAULT_MAP_FILENAME, DEFAULT_SALT_FILENAME
from routers import hangap as hangap_router
from routers import logs as logs_router
from routers import pseudonymize as api
from test_restore import canonical_copy

#: vlan_id を持たない種別だけを使う（vlan の仮名は裸の整数で復元できない）
LOG_FIXTURES = (
    "ap_metrics_20240101_0900_TZT.csv",
    "ap_events_20240101_0900_TZT.csv",
    "rf_neighbors_20240101_0900_TZT.csv",
)

RESTORE_URL = "/api/pseudonymize/restore"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """LOGS_DIR / ソルト置き場を隔離した TestClient。"""
    data_dir = tmp_path / "data"
    logs_dir = data_dir / "logs"
    results_dir = data_dir / "hangap_results"
    results_dir.mkdir(parents=True)
    canonical_copy(logs_dir, names=LOG_FIXTURES)

    monkeypatch.setattr(logs_router, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(hangap_router, "RESULTS_DIR", str(results_dir))
    monkeypatch.setattr(service, "SALT_PATH", str(data_dir / DEFAULT_SALT_FILENAME))
    monkeypatch.setattr(service, "MAP_PATH", str(data_dir / DEFAULT_MAP_FILENAME))

    app = FastAPI()
    app.include_router(logs_router.router)
    app.include_router(api.router)
    with TestClient(app) as c:
        yield c, data_dir


def download_pseudonymized(c, names) -> dict[str, bytes]:
    """仮名化ダウンロードを実行して {ファイル名: 中身} を返す。"""
    res = c.get("/api/pseudonymize/logs", params={"files": ",".join(names)})
    assert res.status_code == 200, res.text
    if len(names) == 1:
        cd = res.headers["Content-Disposition"]
        filename = cd.split('filename="')[1].split('"')[0]
        return {filename: res.content}
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def post_restore(c, files: dict[str, bytes], **params):
    return c.post(
        RESTORE_URL,
        files=[("files", (name, data, "text/csv")) for name, data in files.items()],
        params=params,
    )


def report_of(res) -> dict:
    raw = res.headers[api.RESTORE_REPORT_HEADER]
    return json.loads(base64.b64decode(raw).decode("utf-8"))


# ---------------------------------------------------------------------------
# 往復（ダウンロード → 復元）
# ---------------------------------------------------------------------------


def test_download_then_restore_returns_the_original_bytes(client):
    """仮名化ダウンロードしたファイルをそのまま復元すると、元の中身に戻る。"""
    c, data_dir = client
    name = LOG_FIXTURES[0]
    pseudonymized = download_pseudonymized(c, [name])

    res = post_restore(c, pseudonymized)
    assert res.status_code == 200, res.text
    assert res.content == (Path(logs_router.LOGS_DIR) / name).read_bytes()
    # ファイル名の日付も戻り、復元済みであることが分かる印が付く
    assert "ap_metrics_20240101_0900_TZT_restored.csv" in res.headers["Content-Disposition"]


def test_multiple_files_come_back_as_a_zip(client):
    c, _ = client
    pseudonymized = download_pseudonymized(c, list(LOG_FIXTURES))
    res = post_restore(c, pseudonymized)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        restored = {n: zf.read(n) for n in zf.namelist()}
    assert len(restored) == len(LOG_FIXTURES)
    for name in LOG_FIXTURES:
        expected = (Path(logs_router.LOGS_DIR) / name).read_bytes()
        key = f"{os.path.splitext(name)[0]}_restored.csv"
        assert restored[key] == expected, name


def test_report_header_carries_the_replacement_counts(client):
    c, _ = client
    pseudonymized = download_pseudonymized(c, [LOG_FIXTURES[0]])
    res = post_restore(c, pseudonymized)
    report = report_of(res)
    assert report["counts"]["AP_NAME"] > 0
    assert report["counts"]["TIMESTAMP"] > 0
    assert report["residual_total"] == 0
    assert len(report["files"]) == 1


def test_no_time_leaves_the_timestamps_alone(client):
    c, _ = client
    pseudonymized = download_pseudonymized(c, [LOG_FIXTURES[0]])
    res = post_restore(c, pseudonymized, no_time="true")
    assert res.status_code == 200
    report = report_of(res)
    assert "TIMESTAMP" not in report["counts"]
    assert report["counts"]["AP_NAME"] > 0


def test_unmapped_pseudonyms_are_warned_without_values(client):
    """別環境で仮名化されたファイルを渡すと、残存が警告として返る（値は出さない）。"""
    c, _ = client
    download_pseudonymized(c, [LOG_FIXTURES[0]])  # ソルト・マッピングを作る
    foreign = b"ap_name,site_name\r\nAP_9999,SITE_998\r\n"
    res = post_restore(c, {"foreign.csv": foreign})
    assert res.status_code == 200
    report = report_of(res)
    assert report["residual_total"] == 2
    kinds = {g["kind"] for g in report["files"][0]["residuals"]}
    assert kinds == {"AP_NAME", "SITE_NAME"}
    body = json.dumps(report, ensure_ascii=False)
    assert "AP_9999" not in body and "SITE_998" not in body


# ---------------------------------------------------------------------------
# 要件 13: アップロード上限
# ---------------------------------------------------------------------------


def test_limits_are_published(client):
    c, _ = client
    limits = c.get("/api/pseudonymize/limits").json()
    assert limits["restore_max_files"] == restore_service.MAX_FILES
    assert limits["restore_max_upload_bytes"] == restore_service.MAX_UPLOAD_BYTES
    assert ".csv" in limits["restore_extensions"]


def test_upload_over_the_size_limit_is_rejected_with_the_limit(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(restore_service, "MAX_UPLOAD_BYTES", 1024)
    big = b"ap_name\r\n" + b"AP_0001\r\n" * 200
    res = post_restore(c, {"big.csv": big})
    assert res.status_code == 413
    assert "1MB" in res.json()["detail"] or "0MB" in res.json()["detail"]


def test_too_many_files_is_rejected(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(restore_service, "MAX_FILES", 2)
    files = {f"f{i}.csv": b"ap_name\r\nAP_0001\r\n" for i in range(3)}
    res = post_restore(c, files)
    assert res.status_code == 400
    assert "2 件まで" in res.json()["detail"]


def test_unsupported_extension_is_rejected(client):
    c, _ = client
    res = post_restore(c, {"notes.pdf": b"%PDF-1.4"})
    assert res.status_code == 400
    assert "対応していない形式です" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 要件 12: ソルト・マッピングが露出しないこと / アップロードを data/ に残さないこと
# ---------------------------------------------------------------------------


def test_restore_does_not_expose_the_salt_or_mapping(client):
    c, data_dir = client
    pseudonymized = download_pseudonymized(c, [LOG_FIXTURES[0]])
    salt_bytes = (data_dir / DEFAULT_SALT_FILENAME).read_bytes()
    map_bytes = (data_dir / DEFAULT_MAP_FILENAME).read_bytes()

    res = post_restore(c, pseudonymized)
    header = res.headers[api.RESTORE_REPORT_HEADER].encode("ascii")
    for secret in (salt_bytes, map_bytes):
        assert secret not in res.content
        assert secret not in header
    # ログ API の導線からも取れない（指示 23 のガードが効き続けていること）
    for name in (DEFAULT_SALT_FILENAME, DEFAULT_MAP_FILENAME):
        assert c.get(f"/api/logs/{name}/download").status_code == 400
        assert c.get("/api/pseudonymize/logs", params={"files": name}).status_code == 400


def test_uploads_are_not_kept_under_data(client, monkeypatch):
    """アップロードは一時ディレクトリで処理し、処理後に消える。"""
    c, data_dir = client
    pseudonymized = download_pseudonymized(c, [LOG_FIXTURES[0]])
    before = {p for p in data_dir.rglob("*")}

    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", spy)

    res = post_restore(c, pseudonymized)
    assert res.status_code == 200

    assert created, "一時ディレクトリが使われていない"
    for path in created:
        assert not os.path.exists(path), f"一時ディレクトリが残っている: {path}"
        assert not str(Path(path).resolve()).startswith(str(data_dir.resolve()))
    assert {p for p in data_dir.rglob("*")} == before


def test_restore_without_a_salt_is_a_clear_400(client):
    """一度も仮名化していないサーバでは、500 ではなく理由の分かる 400 を返す。"""
    c, _ = client
    res = post_restore(c, {"x.csv": b"ap_name\r\nAP_0001\r\n"})
    assert res.status_code == 400
    assert "ソルト" in res.json()["detail"]


def test_get_is_not_allowed(client):
    c, _ = client
    assert c.get(RESTORE_URL).status_code == 405
