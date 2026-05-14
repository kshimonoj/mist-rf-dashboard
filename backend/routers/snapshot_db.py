import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from zoneinfo import ZoneInfo

from mist.client import MistClient
import scheduler as sched_module

router = APIRouter()

SNAPSHOTS_DIR = "/app/data/snapshots"
MAIN_DB_PATH = "/app/data/mist.db"
MAX_SLOTS = 2


def _snapshot_path(slot: int) -> str:
    return os.path.join(SNAPSHOTS_DIR, f"snapshot_{slot}.db")


def _get_meta(slot: int) -> dict:
    path = _snapshot_path(slot)
    base = {"slot": slot, "saved_at": None, "ap_count": None, "site_count": None,
            "from_dt": None, "to_dt": None, "size_bytes": None}
    if not os.path.exists(path):
        return base
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT saved_at, ap_count, site_count, from_dt, to_dt FROM snapshot_meta LIMIT 1"
            ).fetchone()
            if row:
                return {**base, "saved_at": row[0], "ap_count": row[1], "site_count": row[2],
                        "from_dt": row[3], "to_dt": row[4], "size_bytes": os.path.getsize(path)}
        finally:
            conn.close()
    except Exception:
        pass
    return base


def _open_ro(slot: int) -> sqlite3.Connection:
    path = _snapshot_path(slot)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Snapshot slot {slot} not found")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, where: str = "", params: tuple = ()):
    schema = src.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not schema or not schema[0]:
        return 0
    dst.execute(schema[0])
    query = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
    rows = src.execute(query, params).fetchall()
    if rows:
        ph = ",".join(["?"] * len(rows[0]))
        dst.executemany(f"INSERT INTO {table} VALUES ({ph})", rows)
    return len(rows)


# ─── List / Create ────────────────────────────────────────────────────────────

@router.get("/api/snapshot-db")
async def list_snapshot_dbs() -> list[dict[str, Any]]:
    return [_get_meta(s) for s in range(1, MAX_SLOTS + 1)]


@router.post("/api/snapshot-db")
async def create_snapshot_db(slot: Optional[int] = Query(None)) -> dict[str, Any]:
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

    # Determine target slot
    if slot is not None:
        if slot < 1 or slot > MAX_SLOTS:
            raise HTTPException(status_code=400, detail=f"slot must be 1-{MAX_SLOTS}")
        target_slot = slot
    else:
        metas = [(s, _get_meta(s)) for s in range(1, MAX_SLOTS + 1)]
        target_slot = next((s for s, m in metas if m["saved_at"] is None), None)
        if target_slot is None:
            filled = [(s, m) for s, m in metas if m["saved_at"]]
            filled.sort(key=lambda x: x[1]["saved_at"])
            target_slot = filled[0][0] if filled else 1

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=72)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")

    # Get site names from Mist API
    org_id = os.getenv("MIST_ORG_ID", "")
    site_names: dict[str, str] = {}
    try:
        client = MistClient()
        sites_raw = await client.get_sites(org_id)
        site_names = {s.get("id", ""): s.get("name", "") for s in (sites_raw or [])}
    except Exception:
        pass

    snapshot_path = _snapshot_path(target_slot)

    try:
        src = sqlite3.connect(MAIN_DB_PATH)
        dst = sqlite3.connect(snapshot_path)
        try:
            for tbl in ["snapshot_meta", "ap_metrics", "radio_config_current", "radio_config_changes", "sites"]:
                dst.execute(f"DROP TABLE IF EXISTS {tbl}")
            dst.commit()

            dst.execute("""
                CREATE TABLE snapshot_meta (
                    id INTEGER PRIMARY KEY, saved_at TEXT NOT NULL,
                    ap_count INTEGER, site_count INTEGER, from_dt TEXT, to_dt TEXT
                )
            """)

            _copy_table(src, dst, "ap_metrics", "timestamp >= ?", (since_str,))
            _copy_table(src, dst, "radio_config_current")
            _copy_table(src, dst, "radio_config_changes", "detected_at >= ?", (since_str,))

            # sites table
            dst.execute("CREATE TABLE sites (site_id TEXT PRIMARY KEY, site_name TEXT)")
            site_ids = {r[0] for r in dst.execute("SELECT DISTINCT site_id FROM ap_metrics").fetchall()}
            for sid in site_ids:
                dst.execute("INSERT INTO sites VALUES (?,?)", (sid, site_names.get(sid, sid)))

            # Performance indexes
            dst.execute("CREATE INDEX IF NOT EXISTS idx_am_ap_ts ON ap_metrics(ap_id, timestamp)")
            dst.execute("CREATE INDEX IF NOT EXISTS idx_am_site ON ap_metrics(site_id)")

            ap_count = dst.execute("SELECT COUNT(DISTINCT ap_id) FROM ap_metrics").fetchone()[0] or 0
            from_row = dst.execute("SELECT MIN(timestamp) FROM ap_metrics").fetchone()[0]
            to_row = dst.execute("SELECT MAX(timestamp) FROM ap_metrics").fetchone()[0]

            dst.execute(
                "INSERT INTO snapshot_meta VALUES (1,?,?,?,?,?)",
                (now.isoformat(), ap_count, len(site_ids), from_row or since_str, to_row or now.isoformat())
            )
            dst.commit()
        finally:
            src.close()
            dst.close()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot creation failed: {e}")

    return _get_meta(target_slot)


# ─── Upload ───────────────────────────────────────────────────────────────────

@router.post("/api/snapshot-db/upload")
async def upload_snapshot_db(file: UploadFile, slot: Optional[int] = Query(None)) -> dict[str, Any]:
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

    content = await file.read()
    if not content[:16].startswith(b"SQLite format 3"):
        raise HTTPException(status_code=400, detail="有効なSQLiteファイルではありません")

    # Validate snapshot_meta
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        test = sqlite3.connect(tmp_path)
        try:
            row = test.execute("SELECT saved_at FROM snapshot_meta LIMIT 1").fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="snapshot_metaテーブルが見つかりません")
        finally:
            test.close()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="ファイルの検証に失敗しました")
    finally:
        os.unlink(tmp_path)

    if slot is not None:
        target_slot = slot
    else:
        metas = [(s, _get_meta(s)) for s in range(1, MAX_SLOTS + 1)]
        target_slot = next((s for s, m in metas if m["saved_at"] is None), None)
        if target_slot is None:
            filled = [(s, m) for s, m in metas if m["saved_at"]]
            filled.sort(key=lambda x: x[1]["saved_at"])
            target_slot = filled[0][0] if filled else 1

    with open(_snapshot_path(target_slot), "wb") as f:
        f.write(content)

    return _get_meta(target_slot)


# ─── Slot-specific queries ────────────────────────────────────────────────────

@router.get("/api/snapshot-db/{slot}/sites")
async def get_snapshot_sites(slot: int) -> list[dict[str, Any]]:
    conn = _open_ro(slot)
    try:
        rows = conn.execute("SELECT site_id, site_name FROM sites").fetchall()
        result = []
        for site_id, site_name in rows:
            cnt = conn.execute(
                "SELECT COUNT(DISTINCT ap_id) FROM ap_metrics WHERE site_id=?", (site_id,)
            ).fetchone()[0] or 0
            result.append({"id": site_id, "name": site_name or site_id, "ap_count": cnt})
        return result
    finally:
        conn.close()


@router.get("/api/snapshot-db/{slot}/sites/{site_id}/aps")
async def get_snapshot_site_aps(slot: int, site_id: str) -> list[dict[str, Any]]:
    conn = _open_ro(slot)
    try:
        rows = conn.execute("""
            SELECT m.ap_id, m.ap_name, m.mac, m.status, m.num_clients,
                   m.radio_24_channel, m.radio_24_utilization, m.radio_24_noise_floor, m.radio_24_tx_power,
                   m.radio_5_channel,  m.radio_5_utilization,  m.radio_5_noise_floor,  m.radio_5_tx_power,
                   m.radio_6_channel,  m.radio_6_utilization,  m.radio_6_noise_floor,  m.radio_6_tx_power
            FROM ap_metrics m
            INNER JOIN (
                SELECT ap_id, MAX(timestamp) as max_ts
                FROM ap_metrics WHERE site_id=? GROUP BY ap_id
            ) latest ON m.ap_id=latest.ap_id AND m.timestamp=latest.max_ts
            WHERE m.site_id=?
        """, (site_id, site_id)).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "mac": r[2], "status": r[3], "num_clients": r[4],
                "radio_24": {"channel": r[5], "utilization": r[6], "noise_floor": r[7], "tx_power": r[8]},
                "radio_5":  {"channel": r[9], "utilization": r[10], "noise_floor": r[11], "tx_power": r[12]},
                "radio_6":  {"channel": r[13], "utilization": r[14], "noise_floor": r[15], "tx_power": r[16]},
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/api/snapshot-db/{slot}/aps/{ap_id}/metrics")
async def get_snapshot_ap_metrics(slot: int, ap_id: str, hours: int = 24) -> list[dict[str, Any]]:
    conn = _open_ro(slot)
    try:
        to_dt_str = conn.execute("SELECT to_dt FROM snapshot_meta LIMIT 1").fetchone()
        if to_dt_str and to_dt_str[0]:
            raw = to_dt_str[0].replace("Z", "+00:00")
            to_dt = datetime.fromisoformat(raw)
            if to_dt.tzinfo is None:
                to_dt = to_dt.replace(tzinfo=timezone.utc)
        else:
            to_dt = datetime.now(timezone.utc)

        since_str = (to_dt - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S")

        cols = [
            "timestamp", "num_clients",
            "radio_24_channel", "radio_24_bandwidth", "radio_24_utilization",
            "radio_24_util_tx", "radio_24_util_rx_in_bss", "radio_24_util_non_wifi",
            "radio_24_noise_floor", "radio_24_tx_power",
            "radio_5_channel", "radio_5_bandwidth", "radio_5_utilization",
            "radio_5_util_tx", "radio_5_util_rx_in_bss", "radio_5_util_non_wifi",
            "radio_5_noise_floor", "radio_5_tx_power",
            "radio_6_channel", "radio_6_bandwidth", "radio_6_utilization",
            "radio_6_util_tx", "radio_6_util_rx_in_bss", "radio_6_util_non_wifi",
            "radio_6_noise_floor", "radio_6_tx_power", "status",
        ]
        rows = conn.execute(
            f"SELECT {','.join(cols)} FROM ap_metrics"
            " WHERE ap_id=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp ASC",
            (ap_id, since_str, to_str)
        ).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


@router.get("/api/snapshot-db/{slot}/aps/{ap_id}/radio-config")
async def get_snapshot_ap_radio_config(slot: int, ap_id: str) -> dict[str, Any]:
    conn = _open_ro(slot)
    try:
        col_info = conn.execute("PRAGMA table_info(radio_config_current)").fetchall()
        col_names = [c[1] for c in col_info]

        row = conn.execute("SELECT * FROM radio_config_current WHERE ap_id=?", (ap_id,)).fetchone()
        current_data = None
        if row:
            rec = dict(zip(col_names, row))
            def bdict(p: str):
                return {
                    "channel": rec.get(f"band_{p}_channel"),
                    "bandwidth": rec.get(f"band_{p}_bandwidth"),
                    "tx_power": rec.get(f"band_{p}_tx_power"),
                    "disabled": bool(rec.get(f"band_{p}_disabled", 0)),
                }
            current_data = {
                "ap_id": rec.get("ap_id"), "ap_name": rec.get("ap_name"), "site_id": rec.get("site_id"),
                "config_source": rec.get("config_source"),
                "config_source_24": rec.get("config_source_24"),
                "config_source_5": rec.get("config_source_5"),
                "config_source_6": rec.get("config_source_6"),
                "deviceprofile_name": rec.get("deviceprofile_name"),
                "rftemplate_name": rec.get("rftemplate_name"),
                "band_24": bdict("24"), "band_5": bdict("5"), "band_6": bdict("6"),
            }

        changes = conn.execute("""
            SELECT id, detected_at, band, changed_field, old_value, new_value, old_source, new_source
            FROM radio_config_changes WHERE ap_id=? ORDER BY detected_at DESC LIMIT 50
        """, (ap_id,)).fetchall()

        return {
            "current": current_data,
            "changes": [
                {"id": c[0], "detected_at": c[1], "band": c[2], "changed_field": c[3],
                 "old_value": c[4], "new_value": c[5], "old_source": c[6], "new_source": c[7]}
                for c in changes
            ],
        }
    finally:
        conn.close()


@router.get("/api/snapshot-db/{slot}/download")
async def download_snapshot_db(slot: int, tz: str = "Asia/Tokyo") -> FileResponse:
    path = _snapshot_path(slot)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Snapshot slot {slot} not found")

    try:
        tz_obj = ZoneInfo(tz)
    except Exception:
        tz_obj = ZoneInfo("Asia/Tokyo")

    meta = _get_meta(slot)
    if meta.get("saved_at"):
        raw = meta["saved_at"].replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(tz_obj)
        tz_abbr = local_dt.strftime("%Z")
        filename = f"mist_snapshot_{local_dt.strftime('%Y%m%d_%H%M')}_{tz_abbr}.db"
    else:
        filename = f"mist_snapshot_slot{slot}.db"

    return FileResponse(path=path, filename=filename, media_type="application/x-sqlite3")
