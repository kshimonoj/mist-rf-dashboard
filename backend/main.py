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
from models import AppSettings, Credentials
from routers import aps, clients, credentials, floor_map, floorpeak, hangap, insights, logs, poll, pseudonymize, radio, rrm, settings, sites, sle, snapshot_db, snapshots, tags
import scheduler as sched_module
from scheduler import (
    poll_all_sites, poll_clients, prune_old_metrics,
    save_hourly_logs, save_rf_neighbors_daily, scheduler,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _init_credentials() -> None:
    """起動時に credentials テーブルを初期化する。
    テーブルが空なら .env の値から "Default" 環境を is_active=1 でシードする。
    （Mist API クライアントは毎回 is_active=1 のレコードを参照する）"""
    db = SessionLocal()
    try:
        if db.query(Credentials).count() == 0:
            cred = Credentials(
                name="Default",
                mist_api_token=os.getenv("MIST_API_TOKEN", ""),
                mist_org_id=os.getenv("MIST_ORG_ID", ""),
                mist_base_url=os.getenv("MIST_BASE_URL", "https://api.mist.com/api/v1"),
                is_active=1,
            )
            db.add(cred)
            db.commit()
            logger.info("Credentials seeded from .env (name=Default)")
        else:
            active = db.query(Credentials).filter(Credentials.is_active == 1).first()
            logger.info(f"Active credentials: {active.name if active else 'none'}")
    except Exception as e:
        logger.warning(f"_init_credentials failed: {e}")
    finally:
        db.close()


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

        client_interval = row.client_polling_interval_seconds
        if client_interval is None:
            client_interval = 600
            row.client_polling_interval_seconds = client_interval
            db.commit()
        sched_module._client_polling_interval_seconds = client_interval
        settings_module._current_client_polling = client_interval
        logger.info(f"Client polling interval: {client_interval}s")

        retention = row.metrics_retention_days
        if retention is None:
            retention = 7
            row.metrics_retention_days = retention
            db.commit()
        long_history = bool(row.long_history_enabled)
        sched_module._metrics_retention_days = retention
        sched_module._long_history_enabled = long_history
        settings_module._current_metrics_retention = retention
        settings_module._long_history_enabled = long_history
        logger.info(f"Metrics retention: {retention}d (long_history={long_history})")

        log_retention = row.log_retention_days
        if log_retention is None:
            log_retention = 30
            row.log_retention_days = log_retention
            db.commit()
        sched_module._log_retention_days = log_retention
        settings_module._current_retention_days = log_retention
        logger.info(f"Log retention: {log_retention}d")
    except Exception as e:
        logger.warning(f"_init_app_settings failed: {e}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("/app/data/snapshots", exist_ok=True)
    migrate_db()
    Base.metadata.create_all(bind=engine)
    _init_credentials()
    _init_app_settings()
    interval = int(os.getenv("POLLING_INTERVAL_SECONDS", "300"))
    scheduler.add_job(poll_all_sites, "interval", seconds=interval, id="poll_mist")
    client_interval = sched_module._client_polling_interval_seconds
    scheduler.add_job(poll_clients, "interval", seconds=client_interval, id="poll_clients")
    scheduler.add_job(save_hourly_logs, "cron", minute=0, id="hourly_csv_log", misfire_grace_time=600)
    scheduler.add_job(prune_old_metrics, "cron", hour=3, minute=0, id="prune_metrics", misfire_grace_time=3600)
    # RRM 隣接はクラウド側が毎晩 1 回更新するだけなので日次取得で足りる
    scheduler.add_job(save_rf_neighbors_daily, "cron", hour=4, minute=30,
                      id="rf_neighbors_daily", misfire_grace_time=3600)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Mist Dashboard API", lifespan=lifespan)

cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3007")
cors_origins = [origin.strip() for origin in cors_origins_str.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # ダウンロードのファイル名をフロントから読めるようにする
    # （仮名化ダウンロードはサーバ側で日付をずらした名前を付ける）
    # 復元レポート（置換件数・残存警告）もフロントから読めるようにする
    expose_headers=["Content-Disposition", "X-Restore-Report"],
)

app.include_router(sites.router)
app.include_router(aps.router)
app.include_router(clients.router)
app.include_router(tags.router)
app.include_router(insights.router)
app.include_router(sle.router)
app.include_router(floor_map.router)
app.include_router(radio.router)
app.include_router(logs.router)
app.include_router(snapshots.router)
app.include_router(snapshot_db.router)
app.include_router(settings.router)
app.include_router(credentials.router)
app.include_router(poll.router)
app.include_router(hangap.router)
app.include_router(floorpeak.router)
app.include_router(rrm.router)
app.include_router(pseudonymize.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
