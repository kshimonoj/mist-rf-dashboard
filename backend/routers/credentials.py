import os
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Credentials

router = APIRouter()


def _mask_token(token: str | None) -> str:
    if not token:
        return ""
    if len(token) <= 4:
        return "****"
    return token[:4] + "****"


def _apply_to_env(cred: Credentials) -> None:
    """DB の値を os.environ に反映する（再起動不要でクライアントに即反映）。"""
    if cred.mist_api_token:
        os.environ["MIST_API_TOKEN"] = cred.mist_api_token
    if cred.mist_org_id:
        os.environ["MIST_ORG_ID"] = cred.mist_org_id
    if cred.mist_base_url:
        os.environ["MIST_BASE_URL"] = cred.mist_base_url


class CredentialsUpdate(BaseModel):
    mist_api_token: Optional[str] = None
    mist_org_id: Optional[str] = None
    mist_base_url: Optional[str] = None


@router.get("/api/credentials")
async def get_credentials(db: Session = Depends(get_db)) -> dict[str, Any]:
    cred = db.query(Credentials).first()
    token = cred.mist_api_token if cred else None
    org_id = cred.mist_org_id if cred else None
    base_url = cred.mist_base_url if cred else None
    return {
        "mist_api_token": _mask_token(token),
        "mist_org_id": org_id or "",
        "mist_base_url": base_url or "",
    }


@router.post("/api/credentials")
async def update_credentials(
    body: CredentialsUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    cred = db.query(Credentials).first()
    if cred is None:
        cred = Credentials(id=1)
        db.add(cred)

    if body.mist_api_token:
        cred.mist_api_token = body.mist_api_token
    if body.mist_org_id is not None:
        cred.mist_org_id = body.mist_org_id
    if body.mist_base_url is not None:
        cred.mist_base_url = body.mist_base_url

    db.commit()
    db.refresh(cred)
    _apply_to_env(cred)

    return {
        "mist_api_token": _mask_token(cred.mist_api_token),
        "mist_org_id": cred.mist_org_id or "",
        "mist_base_url": cred.mist_base_url or "",
    }
