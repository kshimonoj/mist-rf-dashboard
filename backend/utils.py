from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def fmt_dt(dt: datetime | None) -> str | None:
    """datetime を UTC の ISO 文字列（Z 付き）に変換する。
    SQLite から読んだ naive datetime にも対応。"""
    if dt is None:
        return None
    s = dt.isoformat()
    if "+" not in s and not s.endswith("Z"):
        s += "Z"
    return s


def fmt_dt_tz(dt: datetime | None, tz_str: str) -> str:
    """datetime を指定タイムゾーンのローカル時刻文字列（YYYY-MM-DD HH:MM:SS）に変換する。"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz_str)).strftime("%Y-%m-%d %H:%M:%S")
