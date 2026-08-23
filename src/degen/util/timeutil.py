"""Time helpers. Everything internal is UTC epoch seconds (float) or aware UTC datetimes."""
from __future__ import annotations

from datetime import datetime, timezone

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def now() -> float:
    return datetime.now(timezone.utc).timestamp()


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def parse_iso(s: str | None) -> float | None:
    """Parse the ISO-8601 shapes the upstream APIs emit. Returns epoch seconds."""
    if not s:
        return None
    t = s.strip().replace("Z", "+00:00")
    # Some payloads carry >6 fractional digits, which fromisoformat rejects on 3.11.
    if "." in t:
        head, _, tail = t.partition(".")
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            else:
                tail = tail[len(digits):]
                break
        else:
            tail = ""
        t = f"{head}.{digits[:6]}{tail}"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def human_age(seconds: float) -> str:
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"
