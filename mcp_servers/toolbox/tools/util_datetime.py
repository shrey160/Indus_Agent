import datetime
from zoneinfo import ZoneInfo, available_timezones


def _normalize_tz(tz: str) -> str:
    if tz is None or str(tz).lower() in ("local", "system", ""):
        return "local"
    name = str(tz).strip()
    if name in available_timezones():
        return name
    # Try common aliases
    aliases = {"utc": "UTC", "gmt": "GMT", "est": "America/New_York", "pst": "America/Los_Angeles"}
    if name.lower() in aliases:
        return aliases[name.lower()]
    return "local"


async def get_datetime(timezone: str = "local") -> dict:
    tz_name = _normalize_tz(timezone)
    if tz_name == "local":
        now = datetime.datetime.now()
        tz_label = datetime.datetime.now().astimezone().tzname() or "local"
    else:
        now = datetime.datetime.now(ZoneInfo(tz_name))
        tz_label = tz_name

    return {
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": tz_label,
        "source": "system",
    }
