"""RugCheck.xyz - free, keyless token risk reports.

Two endpoints matter: `/report/summary` (cheap, risk list + score) and
`/report` (full: top holders with owners, markets and LP state, creator's other
tokens, Token-2022 extensions, insider graph). Results are cached in-process
because the same mint gets evaluated at several decision ages.
"""
from __future__ import annotations

import threading
from typing import Any

from ..util.http import get_json
from ..util.log import get
from ..util.timeutil import now

log = get("degen.src.rugcheck")
BASE = "https://api.rugcheck.xyz/v1"

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 180.0


def _cached(key: str) -> Any | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now() - hit[0] < CACHE_TTL:
            return hit[1]
    return None


def _store(key: str, val: Any) -> None:
    with _cache_lock:
        _cache[key] = (now(), val)
        if len(_cache) > 8000:
            cutoff = now() - CACHE_TTL
            for k in [k for k, v in _cache.items() if v[0] < cutoff]:
                _cache.pop(k, None)


def summary(mint: str) -> dict[str, Any] | None:
    key = f"s:{mint}"
    hit = _cached(key)
    if hit is not None:
        return hit
    d = get_json(f"{BASE}/tokens/{mint}/report/summary", tries=2)
    _store(key, d)
    return d


def report(mint: str) -> dict[str, Any] | None:
    key = f"r:{mint}"
    hit = _cached(key)
    if hit is not None:
        return hit
    d = get_json(f"{BASE}/tokens/{mint}/report", tries=2)
    _store(key, d)
    return d
