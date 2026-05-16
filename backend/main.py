import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from database import Base, SessionLocal, engine, migrate_db
from models import AppSettings
from routers import aps, floor_map, logs, poll, radio, settings, sites, snapshot_db, snapshots
from scheduler import poll_all_sites, save_hourly_logs, scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _init_app_settings() -> None:
    """DBからアプリ設定を読み込み、各モジュール変数を初期化する。初回起動時はDBに初期値を書き込む。"""
    import scheduler as sched_module
    from routers import settings as settings_module

    db = SessionLocal()
    try:
        row = db.query(AppSettings).first()
        if row is None:
            tz = os.getenv("TIMEZONE", "Asia/Tokyo")
            row = AppSettings(id=1, timezone=tz, last_log_saved_at=None)
            db.add(row)
            db.commit()
            logger.info(f"AppSettings initialized: timezone={tz}")

        if row.last_log_saved_at is not None:
            dt = row.last_log_saved_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            sched_module.last_log_saved_at = dt
            logger.info(f"Restored last_log_saved_at: {dt.isoformat()}")

        tz = row.timezone or "Asia/Tokyo"
        sched_module._app_timezone = tz
        settings_module._current_timezone = tz
        logger.info(f"Active timezone: {tz}")

        if row.monitored_site_ids:
            try:
                ids = json.loads(row.monitored_site_ids)
                sched_module._monitored_site_ids = ids
                settings_module._monitored_site_ids = ids
                logger.info(f"Restored monitored_site_ids: {ids}")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"_init_app_settings failed: {e}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("/app/data/snapshots", exist_ok=True)
    migrate_db()
    Base.metadata.create_all(bind=engine)
    _init_app_settings()
    interval = int(os.getenv("POLLING_INTERVAL_SECONDS", "300"))
    scheduler.add_job(poll_all_sites, "interval", seconds=interval, id="poll_mist")
    scheduler.add_job(save_hourly_logs, "cron", minute=0, id="hourly_csv_log")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Mist Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3007"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sites.router)
app.include_router(aps.router)
app.include_router(floor_map.router)
app.include_router(radio.router)
app.include_router(logs.router)
app.include_router(snapshots.router)
app.include_router(snapshot_db.router)
app.include_router(settings.router)
app.include_router(poll.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
