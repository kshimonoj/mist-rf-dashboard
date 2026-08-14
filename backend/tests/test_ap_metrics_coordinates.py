"""指示 09: ap_metrics への座標列(map_id/x_m/y_m)追加のテスト。

合成データのみを使う。実データは一切扱わない。
"""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import database
import models  # noqa: F401  (Base.metadata にテーブル定義を登録するため)
import scheduler
from database import Base, migrate_db
from models import ApMetrics

OLD_AP_METRICS_DDL = """
CREATE TABLE ap_metrics (
    id INTEGER NOT NULL PRIMARY KEY,
    site_id VARCHAR,
    ap_id VARCHAR,
    ap_name VARCHAR,
    model VARCHAR,
    mac VARCHAR,
    timestamp DATETIME,
    num_clients INTEGER,
    radio_24_channel INTEGER, radio_24_bandwidth INTEGER, radio_24_utilization FLOAT,
    radio_24_util_tx FLOAT, radio_24_util_rx_in_bss FLOAT, radio_24_util_non_wifi FLOAT,
    radio_24_noise_floor FLOAT, radio_24_tx_power FLOAT,
    radio_5_channel INTEGER, radio_5_bandwidth INTEGER, radio_5_utilization FLOAT,
    radio_5_util_tx FLOAT, radio_5_util_rx_in_bss FLOAT, radio_5_util_non_wifi FLOAT,
    radio_5_noise_floor FLOAT, radio_5_tx_power FLOAT,
    radio_6_channel INTEGER, radio_6_bandwidth INTEGER, radio_6_utilization FLOAT,
    radio_6_util_tx FLOAT, radio_6_util_rx_in_bss FLOAT, radio_6_util_non_wifi FLOAT,
    radio_6_noise_floor FLOAT, radio_6_tx_power FLOAT,
    status VARCHAR
)
"""


def _make_old_schema_db(tmp_path, monkeypatch, *, with_row=False):
    """map_id/x_m/y_m を持たない旧スキーマの DB を作り、database.engine を差し替える。"""
    test_engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(database, "engine", test_engine)

    # ap_metrics 以外は現行スキーマで作成（migrate_db がそちらを触っても失敗しないように）
    Base.metadata.create_all(bind=test_engine)
    with test_engine.connect() as conn:
        conn.execute(text("DROP TABLE ap_metrics"))
        conn.execute(text(OLD_AP_METRICS_DDL))
        if with_row:
            conn.execute(text(
                "INSERT INTO ap_metrics (site_id, ap_id, ap_name, model, mac, timestamp, "
                "num_clients, status) VALUES "
                "('test-site-id-0001', 'test-ap-0001', 'TEST-AP-01', 'AP-TEST', "
                "'aabbccddee01', '2026-01-01 10:00:00', 3, 'connected')"
            ))
        conn.commit()
    return test_engine


def test_migration_adds_three_columns(tmp_path, monkeypatch):
    test_engine = _make_old_schema_db(tmp_path, monkeypatch)
    migrate_db()

    with test_engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(ap_metrics)"))}
    assert {"map_id", "x_m", "y_m"} <= cols


def test_migration_is_idempotent(tmp_path, monkeypatch):
    test_engine = _make_old_schema_db(tmp_path, monkeypatch)
    migrate_db()
    migrate_db()  # 2 回目もエラーにならないこと

    with test_engine.connect() as conn:
        rows = list(conn.execute(text("PRAGMA table_info(ap_metrics)")))
    names = [r[1] for r in rows]
    assert names.count("map_id") == 1
    assert names.count("x_m") == 1
    assert names.count("y_m") == 1


def test_migration_preserves_existing_rows(tmp_path, monkeypatch):
    test_engine = _make_old_schema_db(tmp_path, monkeypatch, with_row=True)
    migrate_db()

    with test_engine.connect() as conn:
        rows = list(conn.execute(text(
            "SELECT ap_id, num_clients, map_id, x_m, y_m FROM ap_metrics"
        )))
    assert len(rows) == 1
    ap_id, num_clients, map_id, x_m, y_m = rows[0]
    assert ap_id == "test-ap-0001"
    assert num_clients == 3
    assert map_id is None
    assert x_m is None
    assert y_m is None


def test_missing_map_keys_save_as_none_without_error(tmp_path, monkeypatch):
    """マップ未配置 AP を模した応答（map_id/x_m/y_m キー自体が無い dict）から None を保存できる。"""
    test_engine = create_engine(f"sqlite:///{tmp_path}/test2.db")
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)

    device = {
        "id": "test-ap-0002", "name": "TEST-AP-02", "model": "AP-TEST",
        "mac": "aabbccddee02", "status": "connected", "num_clients": 0,
    }  # map_id / x_m / y_m キーが存在しない応答を模す

    map_id = device.get("map_id")
    x_m = device.get("x_m")
    y_m = device.get("y_m")
    assert map_id is None and x_m is None and y_m is None

    db = TestSession()
    try:
        db.add(ApMetrics(
            site_id="test-site-id-0001", ap_id=device["id"], ap_name=device["name"],
            model=device["model"], mac=device["mac"], status=device["status"],
            num_clients=device["num_clients"], map_id=map_id, x_m=x_m, y_m=y_m,
        ))
        db.commit()
    finally:
        db.close()

    with test_engine.connect() as conn:
        row = conn.execute(text("SELECT map_id, x_m, y_m FROM ap_metrics")).first()
    assert row == (None, None, None)


def test_csv_columns_append_coordinates_without_reordering_existing():
    """CSV 列は既存33列を変えず、末尾に map_id/x_m/y_m が3列追加されて36列になる。"""
    OLD_33_COLUMNS = [
        "timestamp", "site_id", "site_name", "ap_id", "ap_name", "model", "mac", "status",
        "num_clients",
        "radio_24_channel", "radio_24_bandwidth", "radio_24_tx_power",
        "radio_24_utilization", "radio_24_util_tx", "radio_24_util_rx_in_bss", "radio_24_util_non_wifi",
        "radio_24_noise_floor",
        "radio_5_channel", "radio_5_bandwidth", "radio_5_tx_power",
        "radio_5_utilization", "radio_5_util_tx", "radio_5_util_rx_in_bss", "radio_5_util_non_wifi",
        "radio_5_noise_floor",
        "radio_6_channel", "radio_6_bandwidth", "radio_6_tx_power",
        "radio_6_utilization", "radio_6_util_tx", "radio_6_util_rx_in_bss", "radio_6_util_non_wifi",
        "radio_6_noise_floor",
    ]
    assert len(OLD_33_COLUMNS) == 33
    assert len(scheduler.ALL_CSV_COLUMNS) == 36
    assert scheduler.ALL_CSV_COLUMNS[:33] == OLD_33_COLUMNS
    assert scheduler.ALL_CSV_COLUMNS[33:] == ["map_id", "x_m", "y_m"]
