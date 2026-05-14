import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from mist.client import MistClient
import scheduler as sched_module

router = APIRouter()


@router.get("/api/sites/all")
async def get_all_sites() -> list[dict[str, Any]]:
    """全サイト一覧（設定用・軽量版。monitored_site_ids フィルタ非適用）"""
    client = MistClient()
    org_id = os.getenv("MIST_ORG_ID", "")
    sites = await client.get_sites(org_id)
    return [{"id": s.get("id", ""), "name": s.get("name", "")} for s in (sites or [])]


@router.get("/api/sites")
async def get_sites() -> list[dict[str, Any]]:
    client = MistClient()
    org_id = os.getenv("MIST_ORG_ID", "")

    sites = await client.get_sites(org_id)
    if not sites:
        return []

    if sched_module._monitored_site_ids:
        sites = [s for s in sites if s.get("id") in sched_module._monitored_site_ids]

    semaphore = asyncio.Semaphore(5)

    async def enrich_site(site: dict) -> dict:
        async with semaphore:
            site_id = site.get("id", "")
            devices = await client.get_site_devices_stats(site_id)
            total = len(devices)
            online = sum(1 for d in devices if d.get("status") == "connected")
            return {
                "id": site_id,
                "name": site.get("name", ""),
                "address": site.get("address", ""),
                "country_code": site.get("country_code", ""),
                "ap_count": total,
                "online_count": online,
                "offline_count": total - online,
            }

    return await asyncio.gather(*[enrich_site(s) for s in sites])


@router.get("/api/sites/{site_id}")
async def get_site(site_id: str) -> dict[str, Any]:
    client = MistClient()
    org_id = os.getenv("MIST_ORG_ID", "")
    sites = await client.get_sites(org_id)
    site = next((s for s in sites if s.get("id") == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return {
        "id": site_id,
        "name": site.get("name", ""),
        "address": site.get("address", ""),
        "country_code": site.get("country_code", ""),
    }


@router.get("/api/sites/{site_id}/aps")
async def get_site_aps(site_id: str) -> list[dict[str, Any]]:
    client = MistClient()
    devices = await client.get_site_devices_stats(site_id)

    result = []
    for d in devices:
        radio_stat = d.get("radio_stat", {}) or {}
        b24 = radio_stat.get("band_24", {}) or {}
        b5 = radio_stat.get("band_5", {}) or {}
        b6 = radio_stat.get("band_6", {}) or {}
        result.append({
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
        })
    return result
