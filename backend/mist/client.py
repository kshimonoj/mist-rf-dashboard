import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RADIO_KEYWORDS = ["radio", "rf_template", "band_24", "band_5", "band_6",
                  "channel", "tx_power", "bandwidth", "antenna_gain"]

SLE_METRICS = ["capacity", "throughput", "coverage", "time-to-connect", "roaming", "ap-availability"]


def _sum_sle_samples(samples: dict) -> tuple[float, float]:
    """samples は {"total": [...], "degraded": [...]} 形式。total==1 はnull番兵として除外。"""
    if not isinstance(samples, dict):
        return 0.0, 0.0
    totals = samples.get("total") or []
    degradeds = samples.get("degraded") or []
    total_sum = 0.0
    degraded_sum = 0.0
    for t, d in zip(totals, degradeds):
        if t is None or t == 1:
            continue
        total_sum += t or 0.0
        degraded_sum += d or 0.0
    return total_sum, degraded_sum


def parse_sle_metric(data: dict, metric: str) -> dict:
    """Mist SLE summary レスポンスを共通フォーマットに変換する。

    実際のレスポンス構造:
      data["sle"]["samples"] = {"total": [...], "degraded": [...], "value": [...]}
      data["classifiers"]    = [{"name": "wifi-interference", "samples": {...}}, ...]
    """
    if not isinstance(data, dict):
        return {"score": None, "impact_users": 0, "total_users": 0}
    sle = data.get("sle") or {}
    samples = sle.get("samples") or {}
    total_sum, degraded_sum = _sum_sle_samples(samples)
    score = round((total_sum - degraded_sum) / total_sum * 100, 1) if total_sum > 0 else None
    result: dict = {
        "score": score,
        "impact_users": round(degraded_sum),
        "total_users": round(total_sum),
    }
    if metric == "capacity":
        # classifiers はトップレベル (data["classifiers"])
        clf_map: dict = {}
        for clf in (data.get("classifiers") or []):
            clf_name = clf.get("name", "").replace("-", "_")
            _, clf_deg = _sum_sle_samples(clf.get("samples") or {})
            clf_map[clf_name] = round(clf_deg / total_sum * 100, 1) if total_sum > 0 else None
        result["classifiers"] = {
            "wifi_interference": clf_map.get("wifi_interference"),
            "non_wifi_interference": clf_map.get("non_wifi_interference"),
            "client_count": clf_map.get("client_count"),
            "client_usage": clf_map.get("client_usage"),
        }
    if metric == "time-to-connect":
        values = samples.get("value") or []
        totals = samples.get("total") or []
        vals = [v for v, t in zip(values, totals)
                if v is not None and t not in (None, 1)]
        result["avg_sec"] = round(sum(vals) / len(vals), 2) if vals else None
    return result


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

    async def get_site_sle(self, site_id: str, metric: str, duration: str = "1h") -> dict:
        result = await self._get(
            f"/sites/{site_id}/sle/site/{site_id}/metric/{metric}/summary",
            params={"duration": duration},
        )
        return result if isinstance(result, dict) else {}

    async def get_ap_sle(self, site_id: str, ap_id: str, metric: str, duration: str = "1h") -> dict:
        result = await self._get(
            f"/sites/{site_id}/sle/ap/{ap_id}/metric/{metric}/summary",
            params={"duration": duration},
        )
        return result if isinstance(result, dict) else {}

    async def get_site_clients(self, site_id: str, ap_id: Optional[str] = None) -> list[dict]:
        params: dict = {"limit": 1000}
        if ap_id:
            params["ap_id"] = ap_id
        result = await self._get(f"/sites/{site_id}/stats/clients", params=params)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return []
