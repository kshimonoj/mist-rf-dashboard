def detect_band_source(
    ap_config: dict,
    band: str,
    dp_cache: dict,
    rftemplate_cache: dict,
    rftemplate_id: str | None,
) -> str:
    """APのバンド設定ソースを判定する。

    ap_config: GET /sites/{site_id}/devices/{ap_id} のレスポンス
    band: "24" | "5" | "6"
    dp_cache: {deviceprofile_id: {"name": str, "radio_config": dict}}
    rftemplate_cache: {rftemplate_id: rf_name}
    rftemplate_id: サイトに紐付いた RF Template ID
    """
    band_key = f"band_{band}"
    ap_band = (ap_config.get("radio_config") or {}).get(band_key) or {}
    deviceprofile_id = ap_config.get("deviceprofile_id")

    if ap_band:
        if deviceprofile_id:
            dp = dp_cache.get(deviceprofile_id, {})
            if (dp.get("radio_config") or {}).get(band_key):
                return "Device (Profile Override)"
        return "Device"

    if deviceprofile_id:
        dp = dp_cache.get(deviceprofile_id, {})
        if (dp.get("radio_config") or {}).get(band_key):
            dp_name = dp.get("name", "")
            return f"Device Profile: {dp_name}"

    if rftemplate_id:
        rf_name = rftemplate_cache.get(rftemplate_id, "Unknown RF Template")
        return f"Site (RF Template: {rf_name})"

    return "Org"


def overall_source(*sources: str) -> str:
    """複数バンドのソースから最も詳細なソースを返す。"""
    if any(s == "Device (Profile Override)" for s in sources):
        return "Device (Profile Override)"
    for s in sources:
        if s.startswith("Device Profile:"):
            return s
    if any(s == "Device" for s in sources):
        return "Device"
    for s in sources:
        if s.startswith("Site ("):
            return s
    return "Org"
