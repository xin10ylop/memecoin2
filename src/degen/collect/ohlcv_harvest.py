"""Historical price-path harvester.

Purpose and its limits, stated up front because getting this wrong invalidates
everything downstream:

The live collector produces an unbiased census of launches - it sees every
token, including the overwhelming majority that die in minutes. That census is
the only valid basis for *entry* statistics (what fraction of launches ever 2x).

This harvester does something different. It takes tokens that already have
meaningful activity and pulls their complete minute-by-minute price path from
GeckoTerminal. That universe is survivorship-biased by construction, so it must
never be used to estimate how often a launch succeeds. What it is valid for is
the *conditional* question the exit policy actually asks: given that a token
has reached traction, what does its price path look like from there - how far
does it run, how fast, how deep are the pullbacks along the way, and how much
of the peak is still there an hour later. Exit rules fit on this are fit on the
population they will actually be applied to.

Both datasets are needed and they answer different questions.
"""
from __future__ import annotations

import argparse
from typing import Any, Iterable

from ..sources import dexscreener, geckoterminal as gt, jupiter
from ..store.lake import OHLCV, lake
from ..util.log import get, setup
from ..util.timeutil import now

log = get("degen.harvest")


def build_universe(max_pages: int = 8) -> dict[str, dict[str, Any]]:
    """Collect candidate (pool, mint) pairs from every free listing we have."""
    uni: dict[str, dict[str, Any]] = {}
    log.info("building universe: scanning new_pools (%d pages) + trending + toplists", max_pages)

    def add(rows: Iterable[dict], src: str) -> None:
        for r in rows:
            pool = r.get("pool") or r.get("pair")
            mint = r.get("mint")
            if not pool:
                continue
            if pool not in uni:
                uni[pool] = {
                    "pool": pool, "mint": mint, "src": src,
                    "created_at": r.get("created_at"),
                    "reserve_usd": r.get("reserve_usd") or r.get("liquidity_usd"),
                    "symbol": r.get("symbol") or r.get("pool_name"),
                }

    for page in range(1, max_pages + 1):
        add(gt.new_pools(page=page), "gt_new")
    log.info("  new_pools done: %d", len(uni))
    for dur in ("5m", "1h", "6h", "24h"):
        for page in range(1, 4):
            add(gt.trending_pools(duration=dur, page=page), f"gt_trend_{dur}")
    log.info("  trending done: %d", len(uni))

    # Jupiter toplists give mints; resolve each to its deepest pool.
    mints: set[str] = set()
    for kind in ("toptraded", "toptrending", "toporganicscore"):
        for w in ("5m", "1h", "6h", "24h"):
            for t in jupiter.toplist(kind, w, limit=100):
                if t.get("mint"):
                    mints.add(t["mint"])
    for b in dexscreener.token_boosts() + dexscreener.token_profiles():
        if b.get("chainId") == "solana" and b.get("tokenAddress"):
            mints.add(b["tokenAddress"])

    known = {v["mint"] for v in uni.values() if v.get("mint")}
    todo = [m for m in mints if m not in known]
    log.info("universe: %d pools from listings, resolving %d extra mints", len(uni), len(todo))
    for i, m in enumerate(todo):
        for p in gt.pools_for_token(m)[:1]:
            add([p], "jup_toplist")
        if i % 50 == 0 and i:
            log.info("  resolved %d/%d", i, len(todo))
    log.info("universe: %d pools total", len(uni))
    return uni


def harvest(
    uni: dict[str, dict[str, Any]],
    timeframe: str = "minute",
    aggregate: int = 1,
    max_pages: int = 6,
    limit: int | None = None,
) -> int:
    lk = lake()
    n = 0
    items = list(uni.values())[: limit or len(uni)]
    for i, ent in enumerate(items):
        candles = gt.ohlcv_full(ent["pool"], timeframe=timeframe, aggregate=aggregate, max_pages=max_pages)
        if not candles:
            continue
        rows = [
            {
                "pool": ent["pool"], "mint": ent.get("mint"), "symbol": ent.get("symbol"),
                "src": ent.get("src"), "harvested_at": now(), "timeframe": f"{aggregate}{timeframe[0]}",
                "ts": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5],
            }
            for c in candles
            if c and len(c) >= 6
        ]
        n += lk.append_many(OHLCV, rows)
        if i % 25 == 0:
            log.info("harvested %d/%d pools, %d candles", i + 1, len(items), n)
    log.info("harvest done: %d candles from %d pools", n, len(items))
    return n


def main() -> None:
    setup()
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--timeframe", default="minute")
    ap.add_argument("--aggregate", type=int, default=1)
    ap.add_argument("--max-pages", type=int, default=6)
    a = ap.parse_args()
    uni = build_universe(a.pages)
    harvest(uni, a.timeframe, a.aggregate, a.max_pages, a.limit)


if __name__ == "__main__":
    main()
