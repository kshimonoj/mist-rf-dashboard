import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from mist.client import MistClient

router = APIRouter(prefix="/api/floor-map")


@router.get("/sites")
async def get_sites() -> list[dict[str, Any]]:
    client = MistClient()
    org_id = os.getenv("MIST_ORG_ID", "")
    sites = await client.get_sites(org_id)
    return [{"id": s.get("id", ""), "name": s.get("name", "")} for s in (sites or [])]


@router.get("/sites/{site_id}/maps")
async def get_maps(site_id: str) -> list[dict[str, Any]]:
    client = MistClient()
    result = await client._get(f"/sites/{site_id}/maps")
    if not isinstance(result, list):
        return []
    return [
        {
            "id": m.get("id", ""),
            "name": m.get("name", ""),
            "width": m.get("width"),
            "height": m.get("height"),
            "ppm": m.get("ppm"),
        }
        for m in result
    ]


@router.get("/sites/{site_id}/maps/{map_id}/image")
async def get_map_image(site_id: str, map_id: str):
    # Mist API returns a pre-signed JWT URL in the map object's `url` field.
    # GET /maps/{map_id}/image returns 405; we must fetch the signed URL instead.
    client = MistClient()
    maps = await client._get(f"/sites/{site_id}/maps")
    if not isinstance(maps, list):
        raise HTTPException(status_code=404, detail="Maps not found")

    target = next((m for m in maps if m.get("id") == map_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Map not found")

    signed_url = target.get("url")
    if not signed_url:
        raise HTTPException(status_code=404, detail="Map image URL not available")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http_client:
        try:
            resp = await http_client.get(signed_url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/png")
            return Response(content=resp.content, media_type=content_type)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Image fetch error: {e}")


@router.get("/sites/{site_id}/aps")
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
            "status": d.get("status", "disconnected"),
            "map_id": d.get("map_id"),
            "x": d.get("x"),
            "y": d.get("y"),
            "num_clients": d.get("num_clients", 0),
            "radio_24": {
                "channel": b24.get("channel"),
                "bandwidth": b24.get("bandwidth"),
                "tx_power": b24.get("power"),
                "noise_floor": b24.get("noise_floor"),
            },
            "radio_5": {
                "channel": b5.get("channel"),
                "bandwidth": b5.get("bandwidth"),
                "tx_power": b5.get("power"),
                "noise_floor": b5.get("noise_floor"),
            },
            "radio_6": {
                "channel": b6.get("channel"),
                "bandwidth": b6.get("bandwidth"),
                "tx_power": b6.get("power"),
                "noise_floor": b6.get("noise_floor"),
            },
        })
    return result
