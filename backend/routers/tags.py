import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from mist.client import MistClient
from models import ApTag, ClientTag

router = APIRouter()
logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def _norm_mac(mac: str | None) -> str:
    if not mac:
        return ""
    return mac.replace(":", "").replace("-", "").lower()


def _split_tags(tags: str | None) -> list[str]:
    if not tags:
        return []
    return [t.strip() for t in tags.split(",") if t.strip()]


def _clean_tags(tags: str | None) -> str:
    """カンマ区切り文字列を正規化（前後空白除去・空要素除去・重複除去）。"""
    seen: list[str] = []
    for t in _split_tags(tags):
        if t not in seen:
            seen.append(t)
    return ",".join(seen)


def _build_ap_dict(d: dict) -> dict:
    radio_stat = d.get("radio_stat", {}) or {}
    b24 = radio_stat.get("band_24", {}) or {}
    b5 = radio_stat.get("band_5", {}) or {}
    b6 = radio_stat.get("band_6", {}) or {}
    return {
        "id": d.get("id", ""),
        "name": d.get("name", ""),
        "mac": d.get("mac", ""),
        "model": d.get("model", ""),
        "ip": d.get("ip", ""),
        "status": d.get("status", "disconnected"),
        "uptime": d.get("uptime"),
        "num_clients": d.get("num_clients", 0),
        "radio_24": {
            "channel": b24.get("channel"),
            "utilization": b24.get("util_all"),
            "noise_floor": b24.get("noise_floor"),
            "tx_power": b24.get("power"),
        },
        "radio_5": {
            "channel": b5.get("channel"),
            "utilization": b5.get("util_all"),
            "noise_floor": b5.get("noise_floor"),
            "tx_power": b5.get("power"),
        },
        "radio_6": {
            "channel": b6.get("channel"),
            "utilization": b6.get("util_all"),
            "noise_floor": b6.get("noise_floor"),
            "tx_power": b6.get("power"),
        },
    }


class TagBody(BaseModel):
    tags: str = ""


# ── AP tags ──────────────────────────────────────────────────────────────────

@router.get("/api/tags/aps")
async def list_ap_tags(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """タグが設定されている全 AP を返す。"""
    rows = db.query(ApTag).filter(ApTag.tags.isnot(None), ApTag.tags != "").all()
    return [
        {
            "ap_id": r.ap_id,
            "site_id": r.site_id,
            "ap_name": r.ap_name,
            "tags": _split_tags(r.tags),
        }
        for r in rows
    ]


@router.put("/api/tags/aps/{ap_id}")
async def upsert_ap_tag(ap_id: str, body: TagBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    cleaned = _clean_tags(body.tags)
    row = db.query(ApTag).filter_by(ap_id=ap_id).first()
    if not cleaned:
        # 空文字なら削除扱い
        if row:
            db.delete(row)
            db.commit()
        return {"ap_id": ap_id, "tags": []}

    # site_id / ap_name を Mist のリアルタイムデータから補完（任意・ベストエフォート）
    site_id = row.site_id if row else None
    ap_name = row.ap_name if row else None
    if not site_id or not ap_name:
        try:
            client = MistClient()
            org_id = os.getenv("MIST_ORG_ID", "")
            sites = await client.get_sites(org_id)
            found = await _find_ap(client, sites, ap_id)
            if found:
                site_id = found.get("site_id") or site_id
                ap_name = found.get("name") or ap_name
        except Exception as e:
            logger.warning(f"upsert_ap_tag enrich failed: {e}")

    if row:
        row.tags = cleaned
        row.site_id = site_id
        row.ap_name = ap_name
    else:
        db.add(ApTag(ap_id=ap_id, site_id=site_id, ap_name=ap_name, tags=cleaned))
    db.commit()
    return {"ap_id": ap_id, "site_id": site_id, "ap_name": ap_name, "tags": _split_tags(cleaned)}


@router.delete("/api/tags/aps/{ap_id}")
async def delete_ap_tag(ap_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    db.query(ApTag).filter_by(ap_id=ap_id).delete()
    db.commit()
    return {"ap_id": ap_id, "deleted": True}


# ── Client tags ──────────────────────────────────────────────────────────────

@router.get("/api/tags/clients")
async def list_client_tags(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.query(ClientTag).filter(ClientTag.tags.isnot(None), ClientTag.tags != "").all()
    return [
        {
            "mac": r.mac,
            "site_id": r.site_id,
            "hostname": r.hostname,
            "tags": _split_tags(r.tags),
        }
        for r in rows
    ]


@router.put("/api/tags/clients/{mac}")
async def upsert_client_tag(mac: str, body: TagBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    norm = _norm_mac(mac)
    cleaned = _clean_tags(body.tags)
    row = db.query(ClientTag).filter_by(mac=norm).first()
    if not cleaned:
        if row:
            db.delete(row)
            db.commit()
        return {"mac": norm, "tags": []}

    site_id = row.site_id if row else None
    hostname = row.hostname if row else None
    if not site_id or not hostname:
        try:
            client = MistClient()
            org_id = os.getenv("MIST_ORG_ID", "")
            sites = await client.get_sites(org_id)
            found = await _find_client(client, sites, norm)
            if found:
                site_id = found.get("site_id") or site_id
                hostname = found.get("hostname") or hostname
        except Exception as e:
            logger.warning(f"upsert_client_tag enrich failed: {e}")

    if row:
        row.tags = cleaned
        row.site_id = site_id
        row.hostname = hostname
    else:
        db.add(ClientTag(mac=norm, site_id=site_id, hostname=hostname, tags=cleaned))
    db.commit()
    return {"mac": norm, "site_id": site_id, "hostname": hostname, "tags": _split_tags(cleaned)}


@router.delete("/api/tags/clients/{mac}")
async def delete_client_tag(mac: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    db.query(ClientTag).filter_by(mac=_norm_mac(mac)).delete()
    db.commit()
    return {"mac": _norm_mac(mac), "deleted": True}


# ── Tag page ─────────────────────────────────────────────────────────────────

@router.get("/api/tags")
async def list_all_tags(db: Session = Depends(get_db)) -> list[str]:
    """AP + Client で使われている全タグ名の重複除去リストを返す。"""
    names: list[str] = []
    for r in db.query(ApTag.tags).all():
        for t in _split_tags(r.tags):
            if t not in names:
                names.append(t)
    for r in db.query(ClientTag.tags).all():
        for t in _split_tags(r.tags):
            if t not in names:
                names.append(t)
    names.sort(key=lambda s: s.lower())
    return names


async def _find_ap(client: MistClient, sites: list[dict], ap_id: str) -> dict | None:
    """全サイトを横断して ap_id の device stats を探す。"""
    sem = asyncio.Semaphore(5)

    async def scan(site: dict) -> dict | None:
        async with sem:
            site_id = site.get("id", "")
            devices = await client.get_site_devices_stats(site_id)
            for d in devices:
                if d.get("id") == ap_id:
                    d = dict(d)
                    d["site_id"] = site_id
                    return d
        return None

    results = await asyncio.gather(*[scan(s) for s in sites])
    for r in results:
        if r:
            return r
    return None


async def _find_client(client: MistClient, sites: list[dict], norm_mac: str) -> dict | None:
    sem = asyncio.Semaphore(5)

    async def scan(site: dict) -> dict | None:
        async with sem:
            site_id = site.get("id", "")
            clients = await client.get_site_clients(site_id)
            for c in clients:
                if _norm_mac(c.get("mac")) == norm_mac:
                    c = dict(c)
                    c["site_id"] = site_id
                    return c
        return None

    results = await asyncio.gather(*[scan(s) for s in sites])
    for r in results:
        if r:
            return r
    return None


@router.get("/api/tags/{tag}/aps")
async def get_tag_aps(tag: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """指定タグが付いた AP のリアルタイムデータを返す。"""
    rows = db.query(ApTag).filter(ApTag.tags.isnot(None), ApTag.tags != "").all()
    tagged = {r.ap_id: r for r in rows if tag in _split_tags(r.tags)}
    if not tagged:
        return []

    # 関与するサイトのみ device stats を取得
    site_ids = {r.site_id for r in tagged.values() if r.site_id}
    client = MistClient()

    # site_id 不明のタグがある場合は全サイトを走査対象にする
    if any(not r.site_id for r in tagged.values()):
        org_id = os.getenv("MIST_ORG_ID", "")
        all_sites = await client.get_sites(org_id)
        site_ids = {s.get("id", "") for s in (all_sites or [])}

    sem = asyncio.Semaphore(5)

    async def fetch(site_id: str) -> list[dict]:
        async with sem:
            devices = await client.get_site_devices_stats(site_id)
            out = []
            for d in devices:
                if d.get("id") in tagged:
                    ap = _build_ap_dict(d)
                    ap["site_id"] = site_id
                    ap["tags"] = _split_tags(tagged[d["id"]].tags)
                    out.append(ap)
            return out

    results = await asyncio.gather(*[fetch(sid) for sid in site_ids if sid])
    return [ap for sub in results for ap in sub]


@router.get("/api/tags/{tag}/clients")
async def get_tag_clients(tag: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """指定タグが付いた Client のリアルタイムデータを返す。"""
    rows = db.query(ClientTag).filter(ClientTag.tags.isnot(None), ClientTag.tags != "").all()
    tagged = {r.mac: r for r in rows if tag in _split_tags(r.tags)}
    if not tagged:
        return []

    site_ids = {r.site_id for r in tagged.values() if r.site_id}
    client = MistClient()

    if any(not r.site_id for r in tagged.values()):
        org_id = os.getenv("MIST_ORG_ID", "")
        all_sites = await client.get_sites(org_id)
        site_ids = {s.get("id", "") for s in (all_sites or [])}

    sem = asyncio.Semaphore(5)

    async def fetch(site_id: str) -> list[dict]:
        async with sem:
            devices, clients = await asyncio.gather(
                client.get_site_devices_stats(site_id),
                client.get_site_clients(site_id),
            )
            ap_by_mac = {
                (d.get("mac") or "").lower(): {"id": d.get("id", ""), "name": d.get("name", "")}
                for d in devices
            }
            out = []
            for c in clients:
                norm = _norm_mac(c.get("mac"))
                if norm in tagged:
                    ap_info = ap_by_mac.get((c.get("ap_mac") or "").lower(), {})
                    c = dict(c)
                    c["ap_name"] = ap_info.get("name", "")
                    if not c.get("ap_id"):
                        c["ap_id"] = ap_info.get("id", "")
                    c["site_id"] = site_id
                    c["tags"] = _split_tags(tagged[norm].tags)
                    out.append(c)
            return out

    results = await asyncio.gather(*[fetch(sid) for sid in site_ids if sid])
    return [c for sub in results for c in sub]
