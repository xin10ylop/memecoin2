"""DexScreener — free, keyless, and the best cross-chain view of a pair.

Used for: (a) confirming a pool exists and its real reserve depth, (b) the
paid-promotion feeds (token-profiles / token-boosts), which are a genuine
behavioural signal — somebody spending money to promote a token is a fact
about that token, independent of whether the promotion works.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..util.http import get_json
from ..util.log import get
from ..util.timeutil import now

log = get("degen.src.dexscreener")
BASE = "https://api.dexscreener.com"


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_pair(p: dict[str, Any], observed_at: float | None = None) -> dict[str, Any]:
    ts = observed_at if observed_at is not None else now()
    base = p.get("baseToken") or {}
    txns = p.get("txns") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    liq = p.get("liquidity") or {}
    created_ms = p.get("pairCreatedAt")
    created = created_ms / 1000.0 if created_ms else None
    row: dict[str, Any] = {
        "mint": base.get("address"),
        "observed_at": ts,
        "source": "dexscreener",
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "chain": p.get("chainId"),
        "dex": p.get("dexId"),
        "pair": p.get("pairAddress"),
        "quote_symbol": (p.get("quoteToken") or {}).get("symbol"),
        "price_usd": _f(p.get("priceUsd")),
        "price_native": _f(p.get("priceNative")),
        "fdv": _f(p.get("fdv")),
        "mcap": _f(p.get("marketCap")),
        "liquidity_usd": _f(liq.get("usd")),
        "liquidity_base": _f(liq.get("base")),
        "liquidity_quote": _f(liq.get("quote")),
        "created_at": created,
        "age_s": (ts - created) if created else None,
        "boosts": (p.get("boosts") or {}).get("active"),
        "has_socials": bool((p.get("info") or {}).get("socials")),
        "websites": len(((p.get("info") or {}).get("websites") or [])),
    }
    for w in ("m5", "h1", "h6", "h24"):
        t = txns.get(w) or {}
        row[f"ds_{w}_buys"] = t.get("buys")
        row[f"ds_{w}_sells"] = t.get("sells")
        row[f"ds_{w}_vol"] = _f(vol.get(w))
        row[f"ds_{w}_chg"] = _f(chg.get(w))
    return row


def pairs_for_tokens(mints: Iterable[str], chain: str = "solana") -> list[dict[str, Any]]:
    """Up to 30 comma-separated token addresses per call."""
    batch = [m for m in mints if m][:30]
    if not batch:
        return []
    data = get_json(f"{BASE}/tokens/v1/{chain}/{','.join(batch)}")
    if not isinstance(data, list):
        return []
    ts = now()
    return [normalize_pair(p, ts) for p in data]


def pair(chain: str, pair_address: str) -> dict[str, Any] | None:
    data = get_json(f"{BASE}/latest/dex/pairs/{chain}/{pair_address}")
    pairs = (data or {}).get("pairs") or []
    return normalize_pair(pairs[0]) if pairs else None


def search(query: str) -> list[dict[str, Any]]:
    data = get_json(f"{BASE}/latest/dex/search?q={query}")
    pairs = (data or {}).get("pairs") or []
    ts = now()
    return [normalize_pair(p, ts) for p in pairs]


def token_profiles() -> list[dict[str, Any]]:
    """Tokens that paid for a DexScreener profile — a spend signal."""
    data = get_json(f"{BASE}/token-profiles/latest/v1")
    return data if isinstance(data, list) else []


def token_boosts(top: bool = False) -> list[dict[str, Any]]:
    """Paid visibility boosts. `amount` is how much was spent."""
    suffix = "top" if top else "latest"
    data = get_json(f"{BASE}/token-boosts/{suffix}/v1")
    return data if isinstance(data, list) else []
