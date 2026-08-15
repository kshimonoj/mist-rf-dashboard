"""指示 10 パート A: rf_neighbors ログの取得と CSV 出力。

合成データのみを使う。実データ・実データ由来の値は一切扱わない。
"""
from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

import scheduler
from mist.client import MistClient
from scheduler import RF_NEIGHBORS_CSV_COLUMNS

SITE_ID = "test-site-id-0001"
SITE_NAME = "TestSite"
AP_A = "aabbccddee01"
AP_B = "aabbccddee02"
AP_C = "aabbccddee03"
#: ap_metrics に存在しない（サイト外の）AP を模した MAC
AP_OUTSIDE = "aabbccddeeff"

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
TZ = ZoneInfo("Asia/Tokyo")


class _FakeClient:
    """MistClient の差し替え。RRM 応答をテストから注入する。"""

    def __init__(self, per_band: dict[str, object], devices: list[dict] | None = None):
        self.org_id = "test-org-id"
        self._per_band = per_band
        self._devices = devices if devices is not None else [
            {"mac": AP_A, "name": "TEST-AP-01"},
            {"mac": AP_B, "name": "TEST-AP-02"},
            {"mac": AP_C, "name": "TEST-AP-03"},
        ]
        self.calls: list[tuple[str, str]] = []

    async def get_sites(self, org_id):
        return [{"id": SITE_ID, "name": SITE_NAME}]

    async def get_site_devices_stats(self, site_id):
        return self._devices

    async def get_rrm_neighbors(self, site_id, band, limit=100):
        self.calls.append((site_id, band))
        value = self._per_band.get(band, [])
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def logs_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setattr(scheduler, "LOGS_DIR", str(d))
    monkeypatch.setattr(scheduler, "_monitored_site_ids", [])
    monkeypatch.setattr(scheduler, "_app_timezone", "Asia/Tokyo")
    return d


def _install(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(scheduler, "MistClient", lambda: client)


def _run_save(suffix: str = "") -> str | None:
    return asyncio.run(scheduler.save_rf_neighbors_log(NOW, TZ, "JST", filename_suffix=suffix))


def _read(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# 1. ページング
# ---------------------------------------------------------------------------


def test_paged_response_is_fully_collected(monkeypatch):
    """2 ページに分かれた応答から全件（3 AP 分）が集まること。"""
    pages = {
        1: {"band": "5", "total": 3, "limit": 2, "page": 1, "results": [
            {"mac": AP_A, "neighbors": [{"mac": AP_B, "rssi": -58.0}]},
            {"mac": AP_B, "neighbors": [{"mac": AP_A, "rssi": -64.0}]},
        ]},
        2: {"band": "5", "total": 3, "limit": 2, "page": 2, "results": [
            {"mac": AP_C, "neighbors": [{"mac": AP_A, "rssi": -70.0}]},
        ]},
    }
    monkeypatch.setattr(
        "mist.client.get_active_credentials",
        lambda: {"token": "test-token", "org_id": "test-org-id",
                 "base_url": "https://api.example.invalid/api/v1"},
    )
    client = MistClient()
    seen: list[dict] = []

    async def fake_get(path, params=None):
        seen.append(dict(params or {}))
        return pages[(params or {}).get("page", 1)]

    monkeypatch.setattr(client, "_get", fake_get)

    results = asyncio.run(client.get_rrm_neighbors(SITE_ID, "5", limit=2))
    assert [r["mac"] for r in results] == [AP_A, AP_B, AP_C]
    assert [p["page"] for p in seen] == [1, 2]


def test_paged_response_reaches_csv(monkeypatch, logs_dir):
    """ページングされた応答の全件が CSV 行として出力されること。"""
    client = _FakeClient({
        "5": [
            {"mac": AP_A, "neighbors": [{"mac": AP_B, "rssi": -58.0}]},
            {"mac": AP_B, "neighbors": [{"mac": AP_A, "rssi": -64.0}]},
            {"mac": AP_C, "neighbors": [{"mac": AP_A, "rssi": -70.0}]},
        ],
    })
    _install(monkeypatch, client)
    filename = _run_save()
    rows = _read(logs_dir / filename)
    assert len(rows) == 3
    assert list(rows[0]) == RF_NEIGHBORS_CSV_COLUMNS
    # 3 バンドすべてを取りに行っている（収集時点では絞らない）
    assert sorted(b for _, b in client.calls) == ["24", "5", "6"]


# ---------------------------------------------------------------------------
# 2. 非対称性の保持
# ---------------------------------------------------------------------------


def test_asymmetric_pair_is_kept_as_two_rows(monkeypatch, logs_dir):
    """A→B と B→A で RSSI が異なる応答が、対称化されず 2 行として残ること。"""
    client = _FakeClient({
        "5": [
            {"mac": AP_A, "neighbors": [{"mac": AP_B, "rssi": -58.0}]},
            {"mac": AP_B, "neighbors": [{"mac": AP_A, "rssi": -64.0}]},
        ],
    })
    _install(monkeypatch, client)
    rows = _read(logs_dir / _run_save())

    directed = {(r["ap_mac"], r["neighbor_mac"]): r["rssi"] for r in rows}
    assert len(rows) == 2
    assert directed[(AP_A, AP_B)] == "-58.0"
    assert directed[(AP_B, AP_A)] == "-64.0"


def test_bands_are_kept_separate(monkeypatch, logs_dir):
    """band ごとに行が分かれること。"""
    client = _FakeClient({
        "24": [{"mac": AP_A, "neighbors": [{"mac": AP_B, "rssi": -50.0}]}],
        "5": [{"mac": AP_A, "neighbors": [{"mac": AP_B, "rssi": -58.0}]}],
        "6": [{"mac": AP_A, "neighbors": [{"mac": AP_B, "rssi": -66.0}]}],
    })
    _install(monkeypatch, client)
    rows = _read(logs_dir / _run_save())
    assert sorted(r["band"] for r in rows) == ["24", "5", "6"]


# ---------------------------------------------------------------------------
# 3. 名前解決の失敗
# ---------------------------------------------------------------------------


def test_unknown_mac_keeps_row_with_empty_name(monkeypatch, logs_dir):
    """既知 AP 一覧に無い MAC は、名前を空欄にしたまま行として残ること。"""
    client = _FakeClient({
        "5": [{"mac": AP_A, "neighbors": [
            {"mac": AP_B, "rssi": -58.0},
            {"mac": AP_OUTSIDE, "rssi": -80.0},
        ]}],
    })
    _install(monkeypatch, client)
    rows = _read(logs_dir / _run_save())

    by_neighbor = {r["neighbor_mac"]: r for r in rows}
    assert set(by_neighbor) == {AP_B, AP_OUTSIDE}
    assert by_neighbor[AP_B]["neighbor_name"] == "TEST-AP-02"
    assert by_neighbor[AP_OUTSIDE]["neighbor_name"] == ""
    assert by_neighbor[AP_OUTSIDE]["ap_name"] == "TEST-AP-01"


def test_mac_is_normalized_to_lowercase_without_separators(monkeypatch, logs_dir):
    client = _FakeClient(
        {"5": [{"mac": "AA:BB:CC:DD:EE:01", "neighbors": [{"mac": "AA-BB-CC-DD-EE-02", "rssi": -58.0}]}]},
        devices=[{"mac": "AA:BB:CC:DD:EE:01", "name": "TEST-AP-01"},
                 {"mac": AP_B, "name": "TEST-AP-02"}],
    )
    _install(monkeypatch, client)
    rows = _read(logs_dir / _run_save())
    assert rows[0]["ap_mac"] == AP_A
    assert rows[0]["neighbor_mac"] == AP_B
    assert rows[0]["ap_name"] == "TEST-AP-01"
    assert rows[0]["neighbor_name"] == "TEST-AP-02"


# ---------------------------------------------------------------------------
# 4. 取得失敗
# ---------------------------------------------------------------------------


def test_http_404_is_treated_as_empty_without_retry(monkeypatch):
    """RRM エンドポイントが 404 を返しても例外にせず空を返すこと（リトライもしない）。"""
    monkeypatch.setattr(
        "mist.client.get_active_credentials",
        lambda: {"token": "test-token", "org_id": "test-org-id",
                 "base_url": "https://api.example.invalid/api/v1"},
    )
    client = MistClient()
    attempts: list[str] = []

    class _FakeResponse:
        status_code = 404
        headers: dict = {}

        def json(self):  # pragma: no cover - 404 では呼ばれない
            return {}

        def raise_for_status(self):  # pragma: no cover - 404 では呼ばれない
            raise AssertionError("404 で raise_for_status に到達している")

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            attempts.append(url)
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    assert asyncio.run(client.get_rrm_neighbors(SITE_ID, "5")) == []
    assert len(attempts) == 1


def test_rrm_failure_does_not_stop_other_collection(monkeypatch, logs_dir):
    """あるバンドが失敗しても、他バンドの収集と CSV 出力は続くこと。"""
    client = _FakeClient({
        "24": httpx.HTTPError("boom"),
        "5": [{"mac": AP_A, "neighbors": [{"mac": AP_B, "rssi": -58.0}]}],
        "6": [],
    })
    _install(monkeypatch, client)
    rows = _read(logs_dir / _run_save())
    assert [r["band"] for r in rows] == ["5"]


def test_daily_job_swallows_failures(monkeypatch, logs_dir):
    """日次ジョブは取得失敗を外に伝播させないこと（他ジョブを止めないため）。"""
    client = _FakeClient({b: httpx.HTTPError("boom") for b in ("24", "5", "6")})
    _install(monkeypatch, client)
    asyncio.run(scheduler.save_rf_neighbors_daily())  # 例外が出ないこと
    assert not logs_dir.exists() or list(logs_dir.glob("rf_neighbors_*.csv")) == []


def test_no_data_writes_no_file(monkeypatch, logs_dir):
    client = _FakeClient({"24": [], "5": [], "6": []})
    _install(monkeypatch, client)
    assert _run_save() is None
    assert not logs_dir.exists() or list(logs_dir.glob("*.csv")) == []


# ---------------------------------------------------------------------------
# ファイル名
# ---------------------------------------------------------------------------


def test_filename_shapes(monkeypatch, logs_dir):
    client = _FakeClient({"5": [{"mac": AP_A, "neighbors": [{"mac": AP_B, "rssi": -58.0}]}]})
    _install(monkeypatch, client)
    assert _run_save() == "rf_neighbors_20260102_1204_JST.csv"
    assert _run_save("manual") == "rf_neighbors_20260102_120405_JST_manual.csv"
