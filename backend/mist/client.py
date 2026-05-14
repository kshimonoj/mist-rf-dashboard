import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RADIO_KEYWORDS = ["radio", "rf_template", "band_24", "band_5", "band_6",
                  "channel", "tx_power", "bandwidth", "antenna_gain"]


class MistClient:
    def __init__(self):
        self.base_url = os.getenv("MIST_BASE_URL", "https://api.mist.com/api/v1").rstrip("/")
        token = os.getenv("MIST_API_TOKEN", "")
        self.headers = {"Authorization": f"Token {token}"}

    async def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    resp = await client.get(url, headers=self.headers, params=params)
                    if resp.status_code == 429:
                        wait = 2 ** attempt
                        logger.warning(f"Rate limited on {path}, retrying in {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code in (401, 403):
                        logger.error(f"Auth error {resp.status_code} on {path}")
                        return []
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPError as e:
                    if attempt == 2:
                        logger.error(f"HTTP error on {path}: {e}")
                        return []
                    await asyncio.sleep(2 ** attempt)
        return []

    async def _get_with_headers(self, path: str) -> dict:
        """レスポンスボディと X-Page-Total ヘッダーを返す。"""
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    resp = await client.get(url, headers=self.headers)
                    if resp.status_code == 429:
                        wait = 2 ** attempt
                        logger.warning(f"Rate limited on {path}, retrying in {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code in (401, 403):
                        logger.error(f"Auth error {resp.status_code} on {path}")
                        return {"data": [], "total": 0}
                    resp.raise_for_status()
                    data = resp.json()
                    if not isinstance(data, list):
                        data = []
                    total_header = resp.headers.get("X-Page-Total")
                    total = int(total_header) if total_header else len(data)
                    return {"data": data, "total": total}
                except httpx.HTTPError as e:
                    if attempt == 2:
                        logger.error(f"HTTP error on {path}: {e}")
                        return {"data": [], "total": 0}
                    await asyncio.sleep(2 ** attempt)
        return {"data": [], "total": 0}

    async def get_sites(self, org_id: str) -> list[dict]:
        result = await self._get(f"/orgs/{org_id}/sites")
        return result if isinstance(result, list) else []

    async def get_site_devices(self, site_id: str) -> list[dict]:
        result = await self._get(f"/sites/{site_id}/devices", params={"type": "ap"})
        return result if isinstance(result, list) else []

    async def get_site_devices_all(self, site_id: str) -> list[dict]:
        """GET /sites/{site_id}/devices?type=ap&limit=1000 をページネーションで全件取得。"""
        response = await self._get_with_headers(f"/sites/{site_id}/devices?type=ap&limit=1000")
        devices = response["data"]
        total = response["total"]
        page = 2
        while len(devices) < total:
            next_resp = await self._get_with_headers(
                f"/sites/{site_id}/devices?type=ap&limit=1000&page={page}"
            )
            if not next_resp["data"]:
                break
            devices.extend(next_resp["data"])
            page += 1
        return devices

    async def get_site_devices_stats(self, site_id: str) -> list[dict]:
        result = await self._get(f"/sites/{site_id}/stats/devices", params={"type": "ap"})
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return []

    async def get_ap_radio_config(self, site_id: str, ap_id: str) -> dict:
        result = await self._get(f"/sites/{site_id}/devices/{ap_id}")
        return result if isinstance(result, dict) else {}

    async def get_org_audit_logs(self, org_id: str, start: int, end: int) -> list[dict]:
        result = await self._get(
            f"/orgs/{org_id}/logs",
            params={"start": start, "end": end, "limit": 200}
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return []

    async def get_site_setting(self, site_id: str) -> dict:
        result = await self._get(f"/sites/{site_id}/setting")
        return result if isinstance(result, dict) else {}

    async def get_org_rf_templates(self, org_id: str) -> list[dict]:
        result = await self._get(f"/orgs/{org_id}/rftemplates")
        return result if isinstance(result, list) else []

    async def get_org_device_profiles(self, org_id: str) -> list[dict]:
        result = await self._get(f"/orgs/{org_id}/deviceprofiles", params={"type": "ap"})
        return result if isinstance(result, list) else []
