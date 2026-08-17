"""サイト指定（ログに含まれるサイトの一覧 + 分析対象の絞り込み）のテスト。

合成データのみを使う（実データ・実データ由来の値は fixtures に置かない）。

このテストの要は 2 つある。

1. 選択肢は **ログから** 作ること（現在の監視対象からではない）。環境を切り替えると
   ``data/logs`` には監視していないサイトのログが残るため、監視対象だけを選択肢に
   すると、そのログを分析できなくなる。
2. サイトを絞っても **派生列の値が変わらない** こと。サイト全体トレンドは site_name 単位、
   周辺AP判定は map_id 単位で計算しているので、絞り込みで値が動くならどこかが
   サイト単位になっていないということ。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import _synth as S
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hangap import analysis, cli, sites as log_sites
from routers import hangap as api

START = datetime(2026, 1, 1, 10, 0, 0)
INTERVAL = 60

SITE_A = ("test-site-a", "TestSiteA", "test-map-a")
SITE_B = ("test-site-b", "TestSiteB", "test-map-b")
SITE_C = ("test-site-c", "TestSiteC", "test-map-c")

#: ハングする AP。既定の min_zero_samples=5 を満たす
HANG = [4, 4, 4] + [0] * 7 + [4, 4, 4]
#: 同じマップに居る周辺 AP。ゼロ区間の間もクライアントが居る
NEIGHBOR = [6, 6, 6] + [3] * 7 + [6, 6, 6]


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """LOGS_DIR / RESULTS_DIR を隔離したディレクトリに向けた TestClient を返す。"""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr(api, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(api, "RESULTS_DIR", str(tmp_path / "hangap_results"))

    _clear_jobs()
    log_sites.clear_cache()
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, logs_dir
    _clear_jobs()
    log_sites.clear_cache()


def _clear_jobs() -> None:
    with api._LOCK:
        for job in list(api._JOBS.values()):
            api._discard(job)
        api._JOBS.clear()


def _series(
    site: tuple[str, str, str],
    ap_id: str,
    ap_name: str,
    values: list[int],
    *,
    x_m: float,
    y_m: float,
) -> list[dict]:
    site_id, site_name, map_id = site
    return [
        S.metrics_row(
            START + timedelta(seconds=INTERVAL * i),
            ap_id=ap_id,
            ap_name=ap_name,
            mac=f"aabbccdd{ap_id[-4:]}",
            site_id=site_id,
            site_name=site_name,
            num_clients=v,
            map_id=map_id,
            x_m=x_m,
            y_m=y_m,
        )
        for i, v in enumerate(values)
    ]


def _site_rows(site: tuple[str, str, str], prefix: str) -> list[dict]:
    """1 サイト分（ハングする AP 1 台 + 同じマップの周辺 AP 1 台）。"""
    return (
        _series(site, f"test-ap-{prefix}001", f"TEST-{prefix}-01", HANG, x_m=0.0, y_m=0.0)
        + _series(site, f"test-ap-{prefix}002", f"TEST-{prefix}-02", NEIGHBOR, x_m=5.0, y_m=0.0)
    )


def _write_logs(logs_dir: Path, sites: list[tuple[str, str, str]]) -> Path:
    rows: list[dict] = []
    for i, site in enumerate(sites):
        rows += _site_rows(site, chr(ord("A") + i))
    return S.write_metrics(logs_dir / "ap_metrics_20260101_1000_TST.csv", rows)


def _write_rf_neighbors(logs_dir: Path, sites: list[tuple[str, str, str]]) -> Path:
    rows: list[dict] = []
    for i, (site_id, site_name, _map_id) in enumerate(sites):
        prefix = chr(ord("A") + i)
        a, b = f"aabbccdd{prefix}001"[-12:], f"aabbccdd{prefix}002"[-12:]
        rows.append(S.rf_neighbor_row(START, a, b, -60, site_id=site_id, site_name=site_name))
        rows.append(S.rf_neighbor_row(START, b, a, -62, site_id=site_id, site_name=site_name))
    return S.write_rf_neighbors(logs_dir / "rf_neighbors_20260101_TST.csv", rows)


def _wait_done(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/hangap/jobs/{job_id}").json()
        if state["status"] != api.STATUS_RUNNING:
            return state
        time.sleep(0.05)
    raise AssertionError(f"ジョブが {timeout}s 以内に終わりませんでした: {job_id}")


def _analyze(client: TestClient, body: dict | None = None) -> dict:
    r = client.post("/api/hangap/analyze", json=body if body is not None else {})
    assert r.status_code == 202, r.text
    return _wait_done(client, r.json()["job_id"])


def _rows(client: TestClient, state: dict) -> list[dict]:
    body = client.get(f"/api/hangap/jobs/{state['job_id']}/result?limit=1000").json()
    return body["rows"]


# ---------------------------------------------------------------------------
# 1. サイト一覧（ログから作る）
# ---------------------------------------------------------------------------


def test_log_sites_lists_sites_found_in_logs(api_client):
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B])

    body = client.get("/api/hangap/sites").json()
    assert [s["site_id"] for s in body["sites"]] == [SITE_A[0], SITE_B[0]]
    assert [s["site_name"] for s in body["sites"]] == [SITE_A[1], SITE_B[1]]

    for site in body["sites"]:
        assert site["ap_count"] == 2  # ハングする AP + 周辺 AP
        assert site["rows"] == 2 * len(HANG)
        assert site["files"] == 1
        assert site["first"] == "2026-01-01 10:00:00"
        assert site["last"] is not None and site["last"] > site["first"]

    assert body["files_scanned"] == 1
    assert body["metrics_files"] == 1
    assert body["scanned_at"]


def test_log_sites_are_not_taken_from_the_monitored_sites(api_client, monkeypatch):
    """監視対象（``/api/sites``）に無いサイトのログでも一覧に出ること。

    環境を切り替えると ``data/logs`` には現在監視していないサイトのログが残る。
    監視対象だけを選択肢にすると、そのログを分析できなくなる。
    """
    import scheduler

    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A])
    # 監視対象は別サイトだけ、という状態を作る
    monkeypatch.setattr(scheduler, "_monitored_site_ids", {"test-site-monitored-only"})

    body = client.get("/api/hangap/sites").json()
    assert [s["site_id"] for s in body["sites"]] == [SITE_A[0]]


def test_log_sites_ignore_other_file_types(api_client):
    """ap_events など ap_metrics 以外のファイルはサイト一覧に影響しないこと。"""
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A])
    S.write_events(logs_dir / "ap_events_20260101_1000_TST.csv", [S.event_row(START)])

    body = client.get("/api/hangap/sites").json()
    assert [s["site_id"] for s in body["sites"]] == [SITE_A[0]]
    assert body["files_scanned"] == 2
    assert body["metrics_files"] == 1


def test_log_sites_are_cached_and_refreshable(api_client):
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A])

    first = client.get("/api/hangap/sites").json()
    assert first["cached"] is False
    assert client.get("/api/hangap/sites").json()["cached"] is True
    # 明示的な再取得ではキャッシュを使わない
    assert client.get("/api/hangap/sites?refresh=true").json()["cached"] is False

    # ファイルが増えればキャッシュは自動で作り直される
    S.write_metrics(
        logs_dir / "ap_metrics_20260101_1100_TST.csv", _site_rows(SITE_B, "B")
    )
    after = client.get("/api/hangap/sites").json()
    assert after["cached"] is False
    assert {s["site_id"] for s in after["sites"]} == {SITE_A[0], SITE_B[0]}


def test_log_sites_empty_when_no_logs(api_client):
    client, _ = api_client
    body = client.get("/api/hangap/sites").json()
    assert body["sites"] == []
    assert body["files_scanned"] == 0


# ---------------------------------------------------------------------------
# 2. 分析対象の絞り込み
# ---------------------------------------------------------------------------


def test_single_site_limits_the_result(api_client):
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B, SITE_C])

    state = _analyze(client, {"sites": [SITE_A[0]]})
    assert state["status"] == api.STATUS_DONE
    rows = _rows(client, state)
    assert rows and {r["site_name"] for r in rows} == {SITE_A[1]}
    assert state["summary"]["detected_intervals"] == 1
    # ローダのレポートも絞り込んだ後のデータで作ること
    loader_info = state["summary"]["loader"]
    assert [sp["site_id"] for sp in loader_info["site_periods"]] == [SITE_A[0]]
    assert loader_info["ap_count"] == 2


def test_multiple_sites(api_client):
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B, SITE_C])

    state = _analyze(client, {"sites": [SITE_A[0], SITE_C[0]]})
    rows = _rows(client, state)
    assert {r["site_name"] for r in rows} == {SITE_A[1], SITE_C[1]}
    assert SITE_B[1] not in {r["site_name"] for r in rows}


def test_all_sites_when_omitted(api_client):
    """サイトを指定しない場合は従来どおり全サイトが対象になること。"""
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B, SITE_C])

    state = _analyze(client, {})
    rows = _rows(client, state)
    assert {r["site_name"] for r in rows} == {SITE_A[1], SITE_B[1], SITE_C[1]}
    assert state["summary"]["condition_text"].startswith(
        f"分析条件: 対象サイト={analysis.ALL_SITES_TEXT} /"
    )


def test_site_can_be_specified_by_name(api_client):
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B])

    state = _analyze(client, {"sites": [SITE_B[1]]})
    assert {r["site_name"] for r in _rows(client, state)} == {SITE_B[1]}


def test_unknown_site_fails_and_names_it(api_client):
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B])

    state = _analyze(client, {"sites": [SITE_A[0], "test-site-does-not-exist"]})
    assert state["status"] == api.STATUS_FAILED
    assert "test-site-does-not-exist" in state["error"]
    # 存在するサイトは提示する（指定し直せるように）
    assert SITE_A[0] in state["error"] and SITE_B[1] in state["error"]
    assert state["summary"] is None


@pytest.mark.parametrize("sites", [[], "test-site-a", [""], [123], {"a": 1}])
def test_invalid_sites_parameter_is_400(api_client, sites):
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A])
    r = client.post("/api/hangap/analyze", json={"sites": sites})
    assert r.status_code == 400, r.text
    assert "sites" in r.json()["detail"]


def test_condition_text_records_the_selected_sites(api_client):
    """保存済み結果を後から見たときに、何を対象にした分析か分かること。"""
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B])

    state = _analyze(client, {"sites": [SITE_A[0]]})
    condition = state["summary"]["condition_text"]
    assert f"対象サイト={SITE_A[1]} [{SITE_A[0]}]" in condition
    assert SITE_B[0] not in condition

    # 保存済み結果の一覧にも同じ条件が残ること
    saved = client.get("/api/hangap/results").json()["results"]
    assert saved and saved[0]["condition_text"] == condition


# ---------------------------------------------------------------------------
# 3. 絞り込みで派生列の値が変わらないこと（要件 8 / 9）
# ---------------------------------------------------------------------------


def _by_ap(rows: list[dict]) -> dict[tuple[str, int], dict]:
    return {(r["ap_name"], r["区間番号"]): r for r in rows}


@pytest.fixture
def filtered_and_unfiltered(api_client):
    """同じログを「全サイト」と「サイトA のみ」で分析した結果の組。"""
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B, SITE_C])
    _write_rf_neighbors(logs_dir, [SITE_A, SITE_B, SITE_C])

    everything = _by_ap(_rows(client, _analyze(client, {})))
    only_a = _by_ap(_rows(client, _analyze(client, {"sites": [SITE_A[0]]})))
    return everything, only_a


def test_site_trend_columns_are_unchanged_by_filtering(filtered_and_unfiltered):
    """サイト全体トレンド（退場疑い）は site_name 単位で集計しているので変わらない。"""
    everything, only_a = filtered_and_unfiltered
    assert only_a and set(only_a) <= set(everything)

    columns = (
        "サイト合計clients(ゼロ開始時)",
        "サイト合計clients(ゼロ終了時)",
        "サイト全体変化率",
        "退場疑い",
    )
    for key, row in only_a.items():
        for col in columns:
            assert row[col] == everything[key][col], f"{key} の {col} が変わった"
    # 値が入っていない列を比べて満足しないこと
    assert any(row["サイト合計clients(ゼロ開始時)"] for row in only_a.values())


def test_neighbor_verdict_is_unchanged_by_filtering(filtered_and_unfiltered):
    """周辺AP判定は map_id 単位（サイトごとに固有）なので変わらない。"""
    from hangap import neighbors

    everything, only_a = filtered_and_unfiltered
    verdicts = {row["周辺AP判定"] for row in only_a.values()}
    assert verdicts == {neighbors.VERDICT_PRESENT}, verdicts

    for key, row in only_a.items():
        for col in neighbors.NEIGHBOR_COLUMNS:
            assert row[col] == everything[key][col], f"{key} の {col} が変わった"


def test_all_result_columns_are_unchanged_by_filtering(filtered_and_unfiltered):
    """派生列に限らず、サイトA の行は絞り込みの有無で完全に一致すること。"""
    from hangap.detector import RESULT_COLUMNS

    everything, only_a = filtered_and_unfiltered
    for key, row in only_a.items():
        for col in RESULT_COLUMNS:
            assert row[col] == everything[key][col], f"{key} の {col} が変わった"


def test_rf_neighbors_are_filtered_too(api_client):
    """rf_neighbors も対象サイトの分だけを読み込むこと。"""
    client, logs_dir = api_client
    _write_logs(logs_dir, [SITE_A, SITE_B])
    _write_rf_neighbors(logs_dir, [SITE_A, SITE_B])

    everything = _analyze(client, {})["summary"]["loader"]["rf_neighbors_rows"]
    only_a = _analyze(client, {"sites": [SITE_A[0]]})["summary"]["loader"]["rf_neighbors_rows"]
    assert everything == 4 and only_a == 2


# ---------------------------------------------------------------------------
# 4. CLI
# ---------------------------------------------------------------------------


def test_cli_site_option(tmp_path, capsys):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    _write_logs(logs_dir, [SITE_A, SITE_B])
    out_dir = tmp_path / "out"

    code = cli.main([
        "analyze", str(logs_dir), "--site", SITE_A[0], "--out", str(out_dir), "--format", "csv",
    ])
    assert code == cli.EXIT_OK
    stdout = capsys.readouterr().out
    assert f"対象サイト={SITE_A[1]} [{SITE_A[0]}]" in stdout

    csv_text = next(out_dir.glob("*.csv")).read_text(encoding="utf-8-sig")
    assert SITE_A[1] in csv_text
    assert SITE_B[1] not in csv_text


def test_cli_without_site_option_covers_every_site(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    _write_logs(logs_dir, [SITE_A, SITE_B])
    out_dir = tmp_path / "out"

    assert cli.main([
        "analyze", str(logs_dir), "--out", str(out_dir), "--format", "csv",
    ]) == cli.EXIT_OK
    csv_text = next(out_dir.glob("*.csv")).read_text(encoding="utf-8-sig")
    assert SITE_A[1] in csv_text and SITE_B[1] in csv_text


def test_cli_unknown_site_is_an_input_error(tmp_path, capsys):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    _write_logs(logs_dir, [SITE_A])
    out_dir = tmp_path / "out"

    code = cli.main([
        "analyze", str(logs_dir), "--site", "test-site-nope", "--out", str(out_dir),
    ])
    assert code == cli.EXIT_INPUT_ERROR
    assert "test-site-nope" in capsys.readouterr().err
    assert not out_dir.exists()  # 途中まで書き出さない
