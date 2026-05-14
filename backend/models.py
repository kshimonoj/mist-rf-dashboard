from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.sql import func
from database import Base


class ApMetrics(Base):
    __tablename__ = "ap_metrics"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(String, index=True)
    ap_id = Column(String, index=True)
    ap_name = Column(String)
    mac = Column(String)
    timestamp = Column(DateTime, default=func.now(), index=True)
    num_clients = Column(Integer, default=0)
    # 2.4G
    radio_24_channel = Column(Integer, nullable=True)
    radio_24_bandwidth = Column(Integer, nullable=True)
    radio_24_utilization = Column(Float, nullable=True)
    radio_24_util_tx = Column(Float, nullable=True)
    radio_24_util_rx_in_bss = Column(Float, nullable=True)
    radio_24_util_non_wifi = Column(Float, nullable=True)
    radio_24_noise_floor = Column(Float, nullable=True)
    radio_24_tx_power = Column(Float, nullable=True)
    # 5G
    radio_5_channel = Column(Integer, nullable=True)
    radio_5_bandwidth = Column(Integer, nullable=True)
    radio_5_utilization = Column(Float, nullable=True)
    radio_5_util_tx = Column(Float, nullable=True)
    radio_5_util_rx_in_bss = Column(Float, nullable=True)
    radio_5_util_non_wifi = Column(Float, nullable=True)
    radio_5_noise_floor = Column(Float, nullable=True)
    radio_5_tx_power = Column(Float, nullable=True)
    # 6G
    radio_6_channel = Column(Integer, nullable=True)
    radio_6_bandwidth = Column(Integer, nullable=True)
    radio_6_utilization = Column(Float, nullable=True)
    radio_6_util_tx = Column(Float, nullable=True)
    radio_6_util_rx_in_bss = Column(Float, nullable=True)
    radio_6_util_non_wifi = Column(Float, nullable=True)
    radio_6_noise_floor = Column(Float, nullable=True)
    radio_6_tx_power = Column(Float, nullable=True)
    status = Column(String, default="connected")


class RadioConfigChange(Base):
    __tablename__ = "radio_config_changes"

    id = Column(Integer, primary_key=True, index=True)
    ap_id = Column(String, index=True)
    site_id = Column(String)
    ap_name = Column(String, nullable=True)
    detected_at = Column(DateTime, index=True)
    band = Column(String)          # '2.4G' | '5G' | '6G'
    changed_field = Column(String) # 'config_source' | 'channel' | 'bandwidth' | 'tx_power'
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    old_source = Column(String, nullable=True)
    new_source = Column(String, nullable=True)


class RadioConfigCurrent(Base):
    __tablename__ = "radio_config_current"

    id = Column(Integer, primary_key=True, index=True)
    ap_id = Column(String, unique=True, index=True)
    site_id = Column(String, index=True)
    ap_name = Column(String)
    band_24_channel = Column(Integer, nullable=True)
    band_24_bandwidth = Column(Integer, nullable=True)
    band_24_tx_power = Column(Integer, nullable=True)
    band_24_disabled = Column(Integer, default=0)
    band_5_channel = Column(Integer, nullable=True)
    band_5_bandwidth = Column(Integer, nullable=True)
    band_5_tx_power = Column(Integer, nullable=True)
    band_5_disabled = Column(Integer, default=0)
    band_6_channel = Column(Integer, nullable=True)
    band_6_bandwidth = Column(Integer, nullable=True)
    band_6_tx_power = Column(Integer, nullable=True)
    band_6_disabled = Column(Integer, default=0)
    config_source = Column(String, nullable=True)  # overall (backwards compat)
    config_source_24 = Column(String, nullable=True)
    config_source_5 = Column(String, nullable=True)
    config_source_6 = Column(String, nullable=True)
    deviceprofile_id = Column(String, nullable=True)
    deviceprofile_name = Column(String, nullable=True)
    rftemplate_id = Column(String, nullable=True)
    rftemplate_name = Column(String, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class Snapshot(Base):
    __tablename__ = "snapshots"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, unique=True)
    saved_at = Column(DateTime, default=func.now())
    triggered_by = Column(String, default="manual")
    site_count = Column(Integer, default=0)
    ap_count = Column(Integer, default=0)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    last_log_saved_at = Column(DateTime, nullable=True)
    timezone = Column(String, default="Asia/Tokyo")
    monitored_site_ids = Column(Text, nullable=True)  # JSON array string
