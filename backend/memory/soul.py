import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

_mtime: float | None = None
_cached: str | None = None


def get_soul() -> str:
    global _mtime, _cached
    path = DATA_DIR / "soul.md"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _cached or ""
    if _cached is None or mtime != _mtime:
        try:
            _cached = path.read_text(encoding="utf-8")
        except OSError:
            return _cached or ""
        _mtime = mtime
    return _cached


def set_soul(content: str) -> str:
    global _mtime, _cached
    path = DATA_DIR / "soul.md"
    path.write_text(content, encoding="utf-8")
    _cached = content
    try:
        _mtime = path.stat().st_mtime
    except OSError:
        pass
    return content


def soul_block() -> str:
    content = get_soul()
    if not content.strip():
        return ""
    return f"<persona>\n{content}\n</persona>"
