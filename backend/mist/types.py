from pydantic import BaseModel
from typing import Optional, List


class RadioBandStats(BaseModel):
    channel: Optional[int] = None
    utilization: Optional[float] = None
    noise_floor: Optional[float] = None
    tx_power: Optional[float] = None


class ApStats(BaseModel):
    id: str
    name: Optional[str] = None
    mac: Optional[str] = None
    model: Optional[str] = None
    ip: Optional[str] = None
    status: Optional[str] = None
    num_clients: Optional[int] = 0
    uptime: Optional[int] = None
    radio_24: Optional[RadioBandStats] = None
    radio_5: Optional[RadioBandStats] = None
    radio_6: Optional[RadioBandStats] = None


class SiteInfo(BaseModel):
    id: str
    name: str
    address: Optional[str] = None
    country_code: Optional[str] = None
