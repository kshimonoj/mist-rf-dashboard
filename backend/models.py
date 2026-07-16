from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, Text
from sqlalchemy.sql import func
from database import Base


class ApMetrics(Base):
    __tablename__ = "ap_metrics"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(String, index=True)
    ap_id = Column(String, index=True)
    ap_name = Column(String)
    model = Column(String, nullable=True)
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


class ClientMetrics(Base):
    __tablename__ = "client_metrics"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=func.now(), index=True)
    site_id = Column(String, index=True)
    site_name = Column(String, nullable=True)
    ap_id = Column(String, nullable=True)
    ap_name = Column(String, nullable=True)
    ap_mac = Column(String, nullable=True)
    mac = Column(String, index=True)
    hostname = Column(String, nullable=True)
    ip = Column(String, nullable=True)
    manufacture = Column(String, nullable=True)
    family = Column(String, nullable=True)
    model = Column(String, nullable=True)
    os = Column(String, nullable=True)
    band = Column(String, nullable=True)
    channel = Column(Integer, nullable=True)
    proto = Column(String, nullable=True)
    ssid = Column(String, nullable=True)
    bssid = Column(String, nullable=True)
    rssi = Column(Float, nullable=True)
    snr = Column(Float, nullable=True)
    idle_time = Column(Float, nullable=True)
    uptime = Column(Integer, nullable=True)
    tx_rate = Column(Float, nullable=True)
    rx_rate = Column(Float, nullable=True)
    tx_bytes = Column(Integer, nullable=True)
    rx_bytes = Column(Integer, nullable=True)
    tx_pkts = Column(Integer, nullable=True)
    rx_pkts = Column(Integer, nullable=True)
    tx_retries = Column(Integer, nullable=True)
    rx_retries = Column(Integer, nullable=True)
    tx_bps = Column(Integer, nullable=True)
    rx_bps = Column(Integer, nullable=True)
    vlan_id = Column(String, nullable=True)
    key_mgmt = Column(String, nullable=True)
    dual_band = Column(Boolean, nullable=True)
    is_guest = Column(Boolean, nullable=True)


class ApTag(Base):
    __tablename__ = "ap_tags"

    ap_id = Column(String, primary_key=True)
    site_id = Column(String, nullable=True)
    ap_name = Column(String, nullable=True)
    tags = Column(Text, nullable=True)  # カンマ区切り文字列 e.g. "APTest,Down"


class ClientTag(Base):
    __tablename__ = "client_tags"

    mac = Column(String, primary_key=True)  # コロンなし小文字
    site_id = Column(String, nullable=True)
    hostname = Column(String, nullable=True)
    tags = Column(Text, nullable=True)  # カンマ区切り文字列


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
    client_polling_interval_seconds = Column(Integer, default=600)
    last_insights_analyzed_at = Column(DateTime, nullable=True)
    metrics_retention_days = Column(Integer, default=7)
    long_history_enabled = Column(Boolean, default=False)


class Insight(Base):
    __tablename__ = "insights"

    id = Column(Integer, primary_key=True, index=True)
    first_detected_at = Column(DateTime, default=func.now())  # 初回検知時刻
    last_detected_at = Column(DateTime, default=func.now())   # 最終検知時刻
    resolved_at = Column(DateTime, nullable=True)             # 解消時刻（NULL=アクティブ）
    status = Column(String, default="active", index=True)     # 'active' / 'resolved'
    category = Column(String, index=True)   # sticky_client / band24_stuck / high_retry / co_channel / flapping
    severity = Column(String)               # critical / warning
    site_id = Column(String, index=True)
    site_name = Column(String, nullable=True)
    target_type = Column(String)            # ap / client / ap_pair
    target_id = Column(String)              # ap_id / client mac / "ap_id1|ap_id2"
    target_name = Column(String, nullable=True)  # AP名 / hostname(なければMAC) / "AP03 ↔ AP04"
    detail = Column(Text, nullable=True)
    recommendation = Column(Text, nullable=True)
    metrics_json = Column(Text, nullable=True)   # 補足データ(JSON文字列)


class Credentials(Base):
    __tablename__ = "credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False, default="Default")  # 環境名 e.g. "Kyobashi"
    mist_api_token = Column(Text, nullable=True)
    mist_org_id = Column(Text, nullable=True)
    mist_base_url = Column(Text, nullable=True)
    is_active = Column(Integer, default=0)  # 1=アクティブ環境（常に1件のみ）
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
