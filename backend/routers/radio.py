from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import RadioConfigCurrent
from utils import fmt_dt

router = APIRouter()


@router.get("/api/sites/{site_id}/radio-config-summary")
async def get_site_radio_config_summary(site_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    configs = db.query(RadioConfigCurrent).filter_by(site_id=site_id).all()
    return [
        {
            "ap_id": c.ap_id,
            "ap_name": c.ap_name,
            "config_source": c.config_source,
            "config_source_24": c.config_source_24,
            "config_source_5": c.config_source_5,
            "config_source_6": c.config_source_6,
            "deviceprofile_name": c.deviceprofile_name,
            "rftemplate_name": c.rftemplate_name,
            "band_24": {
                "channel": c.band_24_channel,
                "bandwidth": c.band_24_bandwidth,
                "tx_power": c.band_24_tx_power,
                "disabled": bool(c.band_24_disabled),
            },
            "band_5": {
                "channel": c.band_5_channel,
                "bandwidth": c.band_5_bandwidth,
                "tx_power": c.band_5_tx_power,
                "disabled": bool(c.band_5_disabled),
            },
            "band_6": {
                "channel": c.band_6_channel,
                "bandwidth": c.band_6_bandwidth,
                "tx_power": c.band_6_tx_power,
                "disabled": bool(c.band_6_disabled),
            },
            "updated_at": fmt_dt(c.updated_at),
        }
        for c in configs
    ]
