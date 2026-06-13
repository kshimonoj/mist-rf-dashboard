import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import (
    ApMetrics,
    AppSettings,
    ClientMetrics,
    Credentials,
    Insight,
    RadioConfigChange,
    RadioConfigCurrent,
    Snapshot,
)
from scheduler import poll_all_sites, scheduler
import scheduler as sched_module
from utils import fmt_dt

router = APIRouter()
logger = logging.getLogger(__name__)

LOGS_DIR = "/app/data/logs"
SNAPSHOTS_DIR = "/app/data/snapshots"

# If SETTINGS_SECRET is set, mutating /api/credentials endpoints require X-Settings-Key header.
# Leave empty to disable the guard (suitable for local-only deployments).
_settings_secret: str = os.getenv("SETTINGS_SECRET", "")


def _check_settings_key(x_settings_key: str = Header(default="")) -> None:
    if _settings_secret and x_settings_key != _settings_secret:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Settings-Key header")


def _mask_token(token: str | None) -> str:
    """表示用に先頭10文字のみ返す。"""
    if not token:
        return ""
    return token[:10] + "..."


def _serialize(cred: Credentials) -> dict[str, Any]:
    return {
        "id": cred.id,
        "name": cred.name or "",
        "mist_api_token": _mask_token(cred.mist_api_token),
        "mist_org_id": cred.mist_org_id or "",
        "mist_base_url": cred.mist_base_url or "",
        "is_active": bool(cred.is_active),
        "created_at": fmt_dt(cred.created_at) if cred.created_at else None,
    }


def _clear_dir(path: str) -> int:
    """ディレクトリ直下のファイルを全削除し、削除件数を返す。"""
    if not os.path.isdir(path):
        return 0
    count = 0
    for fname in os.listdir(path):
        fpath = os.path.join(path, fname)
        if os.path.isfile(fpath):
            os.remove(fpath)
            count += 1
    return count


class CredentialCreate(BaseModel):
    name: str
    mist_api_token: str
    mist_org_id: str
    mist_base_url: str


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    mist_api_token: Optional[str] = None
    mist_org_id: Optional[str] = None
    mist_base_url: Optional[str] = None


class ActivateBody(BaseModel):
    clear_logs: bool = False
    clear_snapshots: bool = False


@router.get("/api/credentials")
async def list_credentials(db: Session = Depends(get_db)) -> dict[str, Any]:
    creds = db.query(Credentials).order_by(Credentials.id.asc()).all()
    return {
        "items": [_serialize(c) for c in creds],
        "secret_required": bool(_settings_secret),
    }


@router.post("/api/credentials", dependencies=[Depends(_check_settings_key)])
async def create_credential(
    body: CredentialCreate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="環境名（name）は必須です")
    cred = Credentials(
        name=body.name.strip(),
        mist_api_token=body.mist_api_token,
        mist_org_id=body.mist_org_id,
        mist_base_url=body.mist_base_url,
        is_active=0,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    logger.info(f"Credential created: {cred.name} (id={cred.id})")
    return _serialize(cred)


@router.put("/api/credentials/{cred_id}", dependencies=[Depends(_check_settings_key)])
async def update_credential(
    cred_id: int, body: CredentialUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    cred = db.query(Credentials).filter(Credentials.id == cred_id).first()
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    if body.name is not None and body.name.strip():
        cred.name = body.name.strip()
    if body.mist_api_token:  # 空文字は「変更しない」
        cred.mist_api_token = body.mist_api_token
    if body.mist_org_id is not None:
        cred.mist_org_id = body.mist_org_id
    if body.mist_base_url is not None:
        cred.mist_base_url = body.mist_base_url

    db.commit()
    db.refresh(cred)
    return _serialize(cred)


@router.delete("/api/credentials/{cred_id}", dependencies=[Depends(_check_settings_key)])
async def delete_credential(cred_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    cred = db.query(Credentials).filter(Credentials.id == cred_id).first()
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    if cred.is_active:
        raise HTTPException(status_code=400, detail="アクティブな環境は削除できません")
    db.delete(cred)
    db.commit()
    return {"status": "ok", "deleted": cred_id}


@router.post("/api/credentials/{cred_id}/activate", dependencies=[Depends(_check_settings_key)])
async def activate_credential(
    cred_id: int, body: ActivateBody, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """環境を切り替える。蓄積データ（メトリクス・Radio設定・Insights）を削除し、
    オプションで CSV ログ／スナップショットも削除する。タグは引き継ぐ。"""
    cred = db.query(Credentials).filter(Credentials.id == cred_id).first()
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    if cred.is_active:
        return {"status": "ok", "activated": cred.name}

    # 1. ポーリングスケジューラーを一時停止
    scheduler.pause()
    try:
        # 2. 蓄積データを全削除（ap_tags / client_tags は引き継ぐ）
        db.query(ApMetrics).delete(synchronize_session=False)
        db.query(ClientMetrics).delete(synchronize_session=False)
        db.query(RadioConfigCurrent).delete(synchronize_session=False)
        db.query(RadioConfigChange).delete(synchronize_session=False)
        db.query(Insight).delete(synchronize_session=False)

        # 3. CSV ログ削除（snapshots テーブルは CSV ファイルのメタデータ）
        if body.clear_logs:
            removed = _clear_dir(LOGS_DIR)
            db.query(Snapshot).delete(synchronize_session=False)
            logger.info(f"[ACTIVATE] Cleared {removed} log files")

        # 4. スナップショット削除
        if body.clear_snapshots:
            removed = _clear_dir(SNAPSHOTS_DIR)
            logger.info(f"[ACTIVATE] Cleared {removed} snapshot files")

        # 6. is_active を切り替え（旧=0、新=1）
        db.query(Credentials).update({"is_active": 0}, synchronize_session=False)
        cred.is_active = 1

        # 7. タイムスタンプをリセット
        now = datetime.now(timezone.utc)
        row = db.query(AppSettings).first()
        if row:
            row.last_log_saved_at = now
            row.last_insights_analyzed_at = None
        sched_module.last_log_saved_at = now
        sched_module.last_client_log_saved_at = now

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[ACTIVATE] Failed: {e}")
        raise HTTPException(status_code=500, detail=f"環境の切り替えに失敗しました: {e}")
    finally:
        # 8. ポーリングスケジューラーを再開
        scheduler.resume()

    # 新環境で即時ポーリング（バックグラウンド実行）
    asyncio.create_task(poll_all_sites())
    logger.info(f"[ACTIVATE] Switched to '{cred.name}' (id={cred.id})")
    return {"status": "ok", "activated": cred.name}
