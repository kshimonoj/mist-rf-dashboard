"""結果テーブルの取得（列ごとの絞り込み・保存済み結果の再表示）のテスト。

合成データのみを使う（実データ・実データ由来の値は fixtures に置かない）。

主眼は 3 つ。

- ``GET /api/hangap/results/{name}/rows`` が ``GET /api/hangap/jobs/{id}/result`` と
  **同じ形式・同じ内容** を返すこと（表示側で分岐を増やさないため）。保存済みの csv を
  読み戻すだけで、再分析はしない。
- 絞り込みが **サーバ側** で効くこと（ページングと併用するため）。複数列は AND。
- **ダウンロードは絞り込みの影響を受けない**こと（常に全行・全列）。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import _synth as S
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hangap import analysis, archive
from hangap.detector import RESULT_COLUMNS
from routers import hangap as api

START = datetime(2026, 1, 1, 10, 0, 0)
INTERVAL = 60

SITE_A = ("test-site-id-0001", "TestSite")
SITE_B = ("test-site-id-0002", "TestSite2")

#: 検出される 4 区間（ap_name 昇順 = 保存される並び順）。
#: 期待値をテスト内で組み立てるための対応表であり、値は下の _write_logs が作る。
#: TEST-AP-00 / 01 は回復し、TEST-AP-DUO / SOLO はゼロのまま（継続中）。
EXPECTED_AP_NAMES = ["TEST-AP-00", "TEST-AP-01", "TEST-AP-DUO", "TEST-AP-SOLO"]


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr(api, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(api, "RESULTS_DIR", str(tmp_path / archive.RESULTS_DIR_NAME))
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


def _series(
    ap_id: str, ap_name: str, values: list[int], site: tuple[str, str] = SITE_A
) -> list[dict]:
    site_id, site_name = site
    return [
        S.metrics_row(
            START + timedelta(seconds=INTERVAL * i),
            ap_id=ap_id, ap_name=ap_name, num_clients=v,
            site_id=site_id, site_name=site_name,
        )
        for i, v in enumerate(values)
    ]


def _write_logs(logs_dir: Path) -> Path:
    """4 区間が出る合成ログ。列の種類ごとに違う値が入るように作る。

    - 回復状況: 回復 2 件 / 継続中 2 件
    - 連続ゼロ回数: 7 / 8 / 9 / 11（数値範囲の絞り込み用）
    - ゼロ開始: 10:02〜10:04 でばらす（時刻範囲の絞り込み用）
    - 退場疑い: TEST-AP-SOLO だけ True（サイト合計が半減する並びにしてある）
    """
    rows: list[dict] = []
    # TestSite: 2 台がハングして回復。TEST-AP-KEEP は端末を持ち続ける（サイト合計が減らない）
    rows += _series("test-ap-0000", "TEST-AP-00", [1, 1, 1] + [0] * 7 + [1, 1, 1])
    rows += _series("test-ap-0001", "TEST-AP-01", [1, 1, 1] + [0] * 8 + [1, 1])
    rows += _series("test-ap-0002", "TEST-AP-KEEP", [5] * 13)
    # TestSite2: 2 台が続けてゼロに落ちる（SOLO のゼロ区間中にサイト合計が 3 → 0）
    rows += _series("test-ap-0003", "TEST-AP-SOLO", [2, 2] + [0] * 11, site=SITE_B)
    rows += _series("test-ap-0004", "TEST-AP-DUO", [3, 3, 3, 3] + [0] * 9, site=SITE_B)
    return S.write_metrics(logs_dir / "ap_metrics_20260101_1000_TST.csv", rows)


def _analyze(client: TestClient) -> str:
    r = client.post("/api/hangap/analyze", json={})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        state = client.get(f"/api/hangap/jobs/{job_id}").json()
        if state["status"] != api.STATUS_RUNNING:
            assert state["status"] == api.STATUS_DONE, state
            return job_id
        time.sleep(0.05)
    raise AssertionError(f"ジョブが終わりませんでした: {job_id}")


def _saved_name(client: TestClient) -> str:
    results = client.get("/api/hangap/results").json()["results"]
    assert results, "分析結果が保存されていない"
    return results[0]["name"]


@pytest.fixture
def analyzed(api_client):
    """分析を 1 回走らせ、実行中ジョブ / 保存済み結果の両方の URL を返す。"""
    client, logs_dir = api_client
    _write_logs(logs_dir)
    job_id = _analyze(client)
    urls = {
        "job": f"/api/hangap/jobs/{job_id}/result",
        "saved": f"/api/hangap/results/{_saved_name(client)}/rows",
    }
    return client, urls


def _rows(client: TestClient, url: str, *filters: str, **params) -> dict:
    query: list[tuple[str, str]] = [("limit", str(params.pop("limit", 1000)))]
    for key, value in params.items():
        query.append((key, str(value)))
    query += [("filter", f) for f in filters]
    r = client.get(url, params=query)
    assert r.status_code == 200, r.text
    return r.json()


def _names(body: dict) -> list[str]:
    return [row["ap_name"] for row in body["rows"]]


SOURCES = ("job", "saved")


# ---------------------------------------------------------------------------
# 1. 保存済み結果の行取得（jobs/{id}/result と同じ形式・同じ内容）
# ---------------------------------------------------------------------------


def test_saved_rows_have_the_same_shape_and_content_as_the_job_result(analyzed):
    client, urls = analyzed
    job = _rows(client, urls["job"])
    saved = _rows(client, urls["saved"])

    # 形（キー）が同じであること。表示側で分岐を増やさないための要。
    assert set(job) == set(saved)
    assert saved["columns"] == job["columns"] == list(RESULT_COLUMNS)
    assert saved["column_kinds"] == job["column_kinds"]
    assert saved["enum_choices"] == job["enum_choices"]
    assert saved["total"] == job["total"] == len(EXPECTED_AP_NAMES)
    # 内容も同じ（保存済みの csv を読み戻すだけで、再分析はしない）
    assert saved["rows"] == job["rows"]
    assert _names(saved) == EXPECTED_AP_NAMES

    assert job["job_id"] and job["name"] is None
    assert saved["name"] and saved["job_id"] is None


def test_saved_rows_do_not_rerun_the_analysis(analyzed, monkeypatch):
    """保存済み結果の取得で run_analysis を呼ばないこと。"""
    client, urls = analyzed

    def fail(*args, **kwargs):  # pragma: no cover - 呼ばれたら失敗
        raise AssertionError("保存済み結果の取得で再分析が走った")

    monkeypatch.setattr(api.analysis, "run_analysis", fail)
    assert _rows(client, urls["saved"])["total"] == len(EXPECTED_AP_NAMES)


def test_saved_rows_column_kinds_cover_every_column(analyzed):
    client, urls = analyzed
    body = _rows(client, urls["saved"])
    assert set(body["column_kinds"]) == set(RESULT_COLUMNS)
    assert set(body["enum_choices"]) <= set(RESULT_COLUMNS)
    assert body["enum_choices"]["回復状況"] == list(analysis.STATUS_ORDER)
    assert body["enum_choices"]["周辺AP判定"] == list(analysis.VERDICT_ORDER)


# ---------------------------------------------------------------------------
# 2. ページング・ソート（保存済み結果でも効く）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_paging_and_sort(analyzed, source):
    client, urls = analyzed
    url = urls[source]

    first = _rows(client, url, limit=2, offset=0)
    assert first["total"] == 4 and _names(first) == EXPECTED_AP_NAMES[:2]
    second = _rows(client, url, limit=2, offset=2)
    assert second["total"] == 4 and _names(second) == EXPECTED_AP_NAMES[2:]
    past_end = _rows(client, url, limit=2, offset=99)
    assert past_end["total"] == 4 and past_end["rows"] == []

    desc = _rows(client, url, sort="ap_name", order="desc")
    assert _names(desc) == list(reversed(EXPECTED_AP_NAMES))
    by_zeros = _rows(client, url, sort="連続ゼロ回数", order="asc")
    zeros = [row["連続ゼロ回数"] for row in by_zeros["rows"]]
    assert zeros == sorted(zeros)

    # ソートとページングの併用（並び替えた結果の 2 件目以降）
    paged = _rows(client, url, sort="ap_name", order="desc", limit=1, offset=1)
    assert _names(paged) == [EXPECTED_AP_NAMES[-2]]


# ---------------------------------------------------------------------------
# 3. 列ごとの絞り込み（文字列 / 値の選択 / 数値範囲 / 時刻範囲 / 真偽値）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_text_filter_is_a_case_insensitive_substring_match(analyzed, source):
    client, url = analyzed[0], analyzed[1][source]

    assert _names(_rows(client, url, "ap_name:contains:SOLO")) == ["TEST-AP-SOLO"]
    assert _names(_rows(client, url, "ap_name:contains:solo")) == ["TEST-AP-SOLO"]
    assert _names(_rows(client, url, "ap_name:contains:TEST-AP-0")) == EXPECTED_AP_NAMES[:2]
    assert _names(_rows(client, url, "site_name:contains:TestSite2")) == [
        "TEST-AP-DUO", "TEST-AP-SOLO",
    ]
    assert _rows(client, url, "ap_name:contains:該当なし")["total"] == 0


@pytest.mark.parametrize("source", SOURCES)
def test_enum_filter_selects_values_and_multiple_values_are_or(analyzed, source):
    client, url = analyzed[0], analyzed[1][source]

    recovered = _rows(client, url, "回復状況:in:回復")
    assert recovered["total"] == 2
    assert {row["回復状況"] for row in recovered["rows"]} == {"回復"}

    ongoing = _rows(client, url, "回復状況:in:継続中")
    assert _names(ongoing) == ["TEST-AP-DUO", "TEST-AP-SOLO"]

    both = _rows(client, url, "回復状況:in:回復", "回復状況:in:継続中")
    assert _names(both) == EXPECTED_AP_NAMES, "同じ列の複数選択は OR"


@pytest.mark.parametrize("source", SOURCES)
def test_number_range_filter(analyzed, source):
    client, url = analyzed[0], analyzed[1][source]

    assert _names(_rows(client, url, "連続ゼロ回数:min:9")) == ["TEST-AP-DUO", "TEST-AP-SOLO"]
    assert _names(_rows(client, url, "連続ゼロ回数:max:7")) == ["TEST-AP-00"]
    # 下限と上限の併用
    mid = _rows(client, url, "連続ゼロ回数:min:8", "連続ゼロ回数:max:9")
    assert _names(mid) == ["TEST-AP-01", "TEST-AP-DUO"]
    assert _rows(client, url, "連続ゼロ回数:min:1000")["total"] == 0


@pytest.mark.parametrize("source", SOURCES)
def test_time_range_filter(analyzed, source):
    client, url = analyzed[0], analyzed[1][source]

    # ゼロ開始: SOLO 10:02 / AP-00・AP-01 10:03 / DUO 10:04
    assert _names(_rows(client, url, "ゼロ開始:from:2026-01-01 10:04")) == ["TEST-AP-DUO"]
    assert _names(_rows(client, url, "ゼロ開始:to:2026-01-01 10:02")) == ["TEST-AP-SOLO"]
    window = _rows(
        client, url, "ゼロ開始:from:2026-01-01 10:03", "ゼロ開始:to:2026-01-01 10:03:59"
    )
    assert _names(window) == ["TEST-AP-00", "TEST-AP-01"]
    # datetime-local が出す書式（T 区切り）も受け付ける
    assert _names(_rows(client, url, "ゼロ開始:from:2026-01-01T10:04")) == ["TEST-AP-DUO"]
    # 値が無い行（回復時刻が空の継続中）は範囲指定で残らない
    assert _names(_rows(client, url, "回復時刻:from:2026-01-01 00:00")) == [
        "TEST-AP-00", "TEST-AP-01",
    ]


@pytest.mark.parametrize("source", SOURCES)
def test_bool_filter_has_three_states(analyzed, source):
    client, url = analyzed[0], analyzed[1][source]

    assert _names(_rows(client, url)) == EXPECTED_AP_NAMES  # 指定なし
    assert _names(_rows(client, url, "退場疑い:is:true")) == ["TEST-AP-SOLO"]
    assert _names(_rows(client, url, "退場疑い:is:false")) == [
        "TEST-AP-00", "TEST-AP-01", "TEST-AP-DUO",
    ]


# ---------------------------------------------------------------------------
# 4. 複数列の AND 結合
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_filters_on_different_columns_are_combined_with_and(analyzed, source):
    client, url = analyzed[0], analyzed[1][source]

    # 継続中（DUO / SOLO）かつ 連続ゼロ回数 >= 10（SOLO のみ）
    both = _rows(client, url, "回復状況:in:継続中", "連続ゼロ回数:min:10")
    assert _names(both) == ["TEST-AP-SOLO"]

    # 3 列（サイト・真偽値・文字列）
    triple = _rows(
        client, url, "site_name:contains:TestSite2", "退場疑い:is:false", "ap_name:contains:DUO"
    )
    assert _names(triple) == ["TEST-AP-DUO"]

    # 交差が空になる組み合わせ
    assert _rows(client, url, "回復状況:in:回復", "退場疑い:is:true")["total"] == 0


@pytest.mark.parametrize("source", SOURCES)
def test_filters_apply_before_paging_and_sorting(analyzed, source):
    """絞り込みはサーバ側で先に効くこと（total も絞り込み後の件数になる）。"""
    client, url = analyzed[0], analyzed[1][source]

    body = _rows(client, url, "回復状況:in:継続中", sort="ap_name", order="desc", limit=1)
    assert body["total"] == 2, "total が絞り込み後の件数になっていない"
    assert _names(body) == ["TEST-AP-SOLO"]
    assert body["limit"] == 1

    second = _rows(
        client, url, "回復状況:in:継続中", sort="ap_name", order="desc", limit=1, offset=1
    )
    assert _names(second) == ["TEST-AP-DUO"]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("spec,field_name", [
    ("no_such_column:contains:x", "filter"),
    ("ap_name:min:3", "filter[ap_name]"),
    ("連続ゼロ回数:contains:3", "filter[連続ゼロ回数]"),
    ("連続ゼロ回数:min:たくさん", "filter[連続ゼロ回数]"),
    ("ゼロ開始:from:いつか", "filter[ゼロ開始]"),
    ("ゼロ開始:from:2026-01-01T10:00:00+09:00", "filter[ゼロ開始]"),
    ("回復状況:in:そんな状態", "filter[回復状況]"),
    ("退場疑い:is:たぶん", "filter[退場疑い]"),
    ("ap_name:contains:", "filter[ap_name]"),
    ("ap_name-contains-x", "filter"),
])
def test_invalid_filters_are_400_and_name_the_field(analyzed, source, spec, field_name):
    client, url = analyzed[0], analyzed[1][source]
    r = client.get(url, params=[("filter", spec)])
    assert r.status_code == 400, r.text
    assert field_name in r.json()["detail"]


# ---------------------------------------------------------------------------
# 5. ダウンロードは絞り込みの影響を受けない（常に全行・全列）
# ---------------------------------------------------------------------------


def test_download_ignores_filters(analyzed):
    client, urls = analyzed
    job_id = urls["job"].split("/")[4]
    name = urls["saved"].split("/")[4]

    # 画面側で 1 件に絞り込んでいても…
    assert _rows(client, urls["job"], "ap_name:contains:SOLO")["total"] == 1
    assert _rows(client, urls["saved"], "ap_name:contains:SOLO")["total"] == 1

    for url in (
        f"/api/hangap/jobs/{job_id}/download",
        f"/api/hangap/results/{name}/download",
    ):
        plain = client.get(url, params=[("format", "csv")])
        assert plain.status_code == 200
        filtered = client.get(
            url,
            params=[
                ("format", "csv"),
                ("filter", "ap_name:contains:SOLO"),
                ("status", "継続中"),
                ("sort", "ap_name"),
            ],
        )
        assert filtered.status_code == 200
        # …ダウンロードは常に同じファイル（全行・全列）
        assert filtered.content == plain.content, url
        text = filtered.content.decode("utf-8-sig")
        header, *body = [line for line in text.splitlines() if line.strip()]
        assert header.split(",") == list(RESULT_COLUMNS)
        assert len(body) == len(EXPECTED_AP_NAMES)
        for ap_name in EXPECTED_AP_NAMES:
            assert ap_name in text


# ---------------------------------------------------------------------------
# 6. 名前の検証（download / delete と同じ扱い）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("encoded", ["%2e%2e", "%2e%2e%2e", "..%00", "hangap_result_20260101"])
def test_traversal_names_are_400(api_client, encoded):
    client, _ = api_client
    assert client.get(f"/api/hangap/results/{encoded}/rows").status_code == 400


def test_absolute_path_name_does_not_route_to_a_file(api_client, tmp_path):
    client, _ = api_client
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    r = client.get(f"/api/hangap/results/{secret}/rows")
    assert r.status_code in (400, 404)
    assert "secret" not in r.text


def test_missing_saved_result_is_404(api_client):
    client, _ = api_client
    name = f"{archive.NAME_PREFIX}20260101_000000"
    assert client.get(f"/api/hangap/results/{name}/rows").status_code == 404


def test_unreadable_saved_csv_is_409(api_client):
    """csv が壊れていても 500 にせず、ダウンロードで確認できると伝える。"""
    client, _ = api_client
    results_dir = Path(api.RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    name = f"{archive.NAME_PREFIX}20260101_000000"
    (results_dir / f"{name}.csv").write_bytes(b"")  # 空ファイル（ヘッダーすら無い）

    r = client.get(f"/api/hangap/results/{name}/rows")
    assert r.status_code == 409
    assert "ダウンロード" in r.json()["detail"]
