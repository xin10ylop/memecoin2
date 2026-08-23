"""GeckoTerminal — free OHLCV. This is what makes honest backtesting possible.

The critical capability: given any pool address, `/ohlcv/{timeframe}` returns
up to 1000 candles and accepts `before_timestamp`, so a token's entire minute-
by-minute price path from launch can be reconstructed by paging backwards.
Rate limit on the free tier is ~30 calls/min, which the shared limiter respects.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..util.http import get_json
from ..util.log import get
from ..util.timeutil import now, parse_iso

log = get("degen.src.gecko")
BASE = "https://api.geckoterminal.com/api/v2"
HDRS = {"Accept": "application/json;version=20230302"}
MAX_CANDLES = 1000


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_pool(p: dict[str, Any], observed_at: float | None = None) -> dict[str, Any]:
    ts = observed_at if observed_at is not None else now()
    a = p.get("attributes") or {}
    rel = p.get("relationships") or {}
    base_id = (((rel.get("base_token") or {}).get("data")) or {}).get("id", "")
    mint = base_id.split("_", 1)[1] if "_" in base_id else None
    created = parse_iso(a.get("pool_created_at"))
    txn = a.get("transactions") or {}
    vol = a.get("volume_usd") or {}
    chg = a.get("price_change_percentage") or {}
    row: dict[str, Any] = {
        "mint": mint,
        "observed_at": ts,
        "source": "geckoterminal",
        "pool": a.get("address"),
        "pool_name": a.get("name"),
        "price_usd": _f(a.get("base_token_price_usd")),
        "price_native": _f(a.get("base_token_price_native_currency")),
        "fdv": _f(a.get("fdv_usd")),
        "mcap": _f(a.get("market_cap_usd")),
        "reserve_usd": _f(a.get("reserve_in_usd")),
        "created_at": created,
        "age_s": (ts - created) if created else None,
    }
    for w in ("m5", "m15", "m30", "h1", "h6", "h24"):
        t = txn.get(w) or {}
        row[f"gt_{w}_buys"] = t.get("buys")
        row[f"gt_{w}_sells"] = t.get("sells")
        row[f"gt_{w}_buyers"] = t.get("buyers")
        row[f"gt_{w}_sellers"] = t.get("sellers")
        row[f"gt_{w}_vol"] = _f(vol.get(w))
        row[f"gt_{w}_chg"] = _f(chg.get(w))
    return row


def new_pools(network: str = "solana", page: int = 1) -> list[dict[str, Any]]:
    data = get_json(f"{BASE}/networks/{network}/new_pools?page={page}", headers=HDRS)
    items = (data or {}).get("data") or []
    ts = now()
    return [normalize_pool(p, ts) for p in items]


def trending_pools(network: str = "solana", duration: str = "5m", page: int = 1) -> list[dict[str, Any]]:
    url = f"{BASE}/networks/{network}/trending_pools?page={page}&duration={duration}"
    data = get_json(url, headers=HDRS)
    items = (data or {}).get("data") or []
    ts = now()
    return [normalize_pool(p, ts) for p in items]


def pools_for_token(mint: str, network: str = "solana") -> list[dict[str, Any]]:
    data = get_json(f"{BASE}/networks/{network}/tokens/{mint}/pools", headers=HDRS)
    items = (data or {}).get("data") or []
    ts = now()
    return [normalize_pool(p, ts) for p in items]


def ohlcv(
    pool: str,
    network: str = "solana",
    timeframe: str = "minute",
    aggregate: int = 1,
    limit: int = MAX_CANDLES,
    before_timestamp: int | None = None,
    currency: str = "usd",
) -> list[list[float]]:
    """Returns [[ts, open, high, low, close, volume], ...] newest-first."""
    url = (
        f"{BASE}/networks/{network}/pools/{pool}/ohlcv/{timeframe}"
        f"?aggregate={aggregate}&limit={min(limit, MAX_CANDLES)}&currency={currency}"
    )
    if before_timestamp:
        url += f"&before_timestamp={int(before_timestamp)}"
    data = get_json(url, headers=HDRS)
    try:
        rows = data["data"]["attributes"]["ohlcv_list"]
    except (TypeError, KeyError):
        return []
    return rows or []


def ohlcv_full(
    pool: str,
    network: str = "solana",
    timeframe: str = "minute",
    aggregate: int = 1,
    max_pages: int = 12,
    stop_before: float | None = None,
) -> list[list[float]]:
    """Page backwards to reconstruct a full history. Returns oldest-first."""
    out: list[list[float]] = []
    cursor: int | None = None
    seen: set[float] = set()
    for _ in range(max_pages):
        chunk = ohlcv(pool, network, timeframe, aggregate, MAX_CANDLES, cursor)
        if not chunk:
            break
        fresh = [c for c in chunk if c and c[0] not in seen]
        if not fresh:
            break
        for c in fresh:
            seen.add(c[0])
        out.extend(fresh)
        oldest = min(c[0] for c in fresh)
        if stop_before is not None and oldest <= stop_before:
            break
        if len(chunk) < MAX_CANDLES:
            break
        cursor = int(oldest)
    out.sort(key=lambda c: c[0])
    return out
