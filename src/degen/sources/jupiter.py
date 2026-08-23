"""Jupiter Token API v2 — the backbone of discovery and tracking.

Why this is the primary source: `/tokens/v2/recent` returns tokens seconds after
their first pool exists, and every record already carries the fields that would
otherwise cost several RPC round-trips per token — creator wallet, how many
tokens that creator has previously minted, holder count, top-holder
concentration, mint/freeze authority state, and buy/sell counts over four
windows. `/tokens/v2/search` accepts up to 100 comma-separated mints per call,
which makes forward-tracking thousands of tokens cheap.

Everything is free and unauthenticated on lite-api. `api.jup.ag` is the same
surface with an API key and higher limits; set DEGEN_JUP_KEY to use it.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Iterator

from ..util.http import get_json
from ..util.log import get
from ..util.timeutil import now, parse_iso

log = get("degen.src.jupiter")

_KEY = os.getenv("DEGEN_JUP_KEY", "").strip()
BASE = "https://api.jup.ag" if _KEY else "https://lite-api.jup.ag"
_HDRS = {"x-api-key": _KEY} if _KEY else {}

MAX_BATCH = 100
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

_STAT_WINDOWS = ("5m", "1h", "6h", "24h")
_STAT_FIELDS = (
    "priceChange",
    "liquidityChange",
    "volumeChange",
    "buyVolume",
    "sellVolume",
    "numBuys",
    "numSells",
    "numTraders",
    "numNetBuyers",
    "holderChange",
)


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def normalize(raw: dict[str, Any], observed_at: float | None = None) -> dict[str, Any]:
    """Flatten a Jupiter token record into one wide, stable row.

    Nested stats become `s5m_numBuys`-style scalars so the whole thing lands in
    a dataframe column without further massaging.
    """
    ts = observed_at if observed_at is not None else now()
    audit = raw.get("audit") or {}
    first_pool = raw.get("firstPool") or {}
    created = parse_iso(first_pool.get("createdAt")) or parse_iso(raw.get("createdAt"))

    row: dict[str, Any] = {
        "mint": raw.get("id"),
        "observed_at": ts,
        "source": "jupiter",
        "symbol": raw.get("symbol"),
        "name": raw.get("name"),
        "decimals": raw.get("decimals"),
        "dev": raw.get("dev"),
        "launchpad": raw.get("launchpad"),
        "token_program": raw.get("tokenProgram"),
        "first_pool": first_pool.get("id"),
        "created_at": created,
        "age_s": (ts - created) if created else None,
        "circ_supply": _f(raw.get("circSupply")),
        "total_supply": _f(raw.get("totalSupply")),
        "holder_count": raw.get("holderCount"),
        "price_usd": _f(raw.get("usdPrice")),
        "mcap": _f(raw.get("mcap")),
        "fdv": _f(raw.get("fdv")),
        "liquidity": _f(raw.get("liquidity")),
        "price_block": raw.get("priceBlockId"),
        "organic_score": _f(raw.get("organicScore")),
        "organic_label": raw.get("organicScoreLabel"),
        "tags": raw.get("tags") or [],
        # audit block
        "mint_auth_disabled": audit.get("mintAuthorityDisabled"),
        "freeze_auth_disabled": audit.get("freezeAuthorityDisabled"),
        "top_holders_pct": _f(audit.get("topHoldersPercentage")),
        "dev_mints": audit.get("devMints"),
        "dev_balance_pct": _f(audit.get("devBalancePercentage")),
        "dev_migrations": audit.get("devMigrations"),
        "block_ref": raw.get("updatedAt"),
        "has_socials": bool((raw.get("twitter") or raw.get("website") or raw.get("telegram"))),
        "twitter": raw.get("twitter"),
        "website": raw.get("website"),
        "telegram": raw.get("telegram"),
    }
    for w in _STAT_WINDOWS:
        blk = raw.get(f"stats{w}") or {}
        for f in _STAT_FIELDS:
            row[f"s{w}_{f}"] = _f(blk.get(f)) if f not in ("numBuys", "numSells", "numTraders", "numNetBuyers") else blk.get(f)
    return row


def _fetch(path: str, **params: Any) -> Any:
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{BASE}/tokens/v2/{path}" + (f"?{qs}" if qs else "")
    return get_json(url, headers=_HDRS or None)


def recent(normalized: bool = True) -> list[dict[str, Any]]:
    """The ~30 most recently created tokens Jupiter knows about."""
    data = _fetch("recent")
    if not isinstance(data, list):
        return []
    ts = now()
    return [normalize(t, ts) for t in data] if normalized else data


def search(mints: Iterable[str], normalized: bool = True) -> list[dict[str, Any]]:
    """Look up up to MAX_BATCH mints in one call."""
    batch = [m for m in mints if m][:MAX_BATCH]
    if not batch:
        return []
    data = _fetch("search", query=",".join(batch))
    if not isinstance(data, list):
        return []
    ts = now()
    return [normalize(t, ts) for t in data] if normalized else data


def search_many(mints: Iterable[str], normalized: bool = True) -> Iterator[dict[str, Any]]:
    """Chunked search over an arbitrary number of mints."""
    buf: list[str] = []
    for m in mints:
        buf.append(m)
        if len(buf) >= MAX_BATCH:
            yield from search(buf, normalized)
            buf = []
    if buf:
        yield from search(buf, normalized)


def toplist(kind: str, window: str = "5m", limit: int = 100) -> list[dict[str, Any]]:
    """kind in {toptrending, toptraded, toporganicscore}."""
    data = _fetch(f"{kind}/{window}", limit=limit)
    if not isinstance(data, list):
        return []
    ts = now()
    return [normalize(t, ts) for t in data]


def quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int = 100,
    only_direct: bool = False,
) -> dict[str, Any] | None:
    """Executable quote. `amount` is in the input mint's smallest unit.

    Doubles as a liquidity probe: quoting a sell of your whole position tells
    you the realizable exit price including route impact, which is the only
    honest way to value an illiquid bag.
    """
    url = (
        f"{BASE}/swap/v1/quote?inputMint={input_mint}&outputMint={output_mint}"
        f"&amount={int(amount)}&slippageBps={int(slippage_bps)}"
        f"&onlyDirectRoutes={'true' if only_direct else 'false'}"
    )
    return get_json(url, headers=_HDRS or None, tries=2)


def price(mints: Iterable[str]) -> dict[str, Any]:
    ids = ",".join(list(mints)[:50])
    if not ids:
        return {}
    data = get_json(f"{BASE}/price/v3?ids={ids}", headers=_HDRS or None)
    return data if isinstance(data, dict) else {}
