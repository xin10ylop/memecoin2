"""The data collector: discover every new token, then follow each one forward.

Two cooperating loops share one process.

DISCOVERY sweeps the new-token feeds and records the first time we ever see a
mint. Jupiter's recent feed is the fast path (tokens show up within seconds of
their first pool); GeckoTerminal and DexScreener are cross-checks that catch
launches Jupiter indexes late or not at all.

TRACKING re-polls known mints on an age-tiered schedule and appends a snapshot
row each time. This is what turns a stream of launches into a panel dataset:
for every token we get its whole observed trajectory, including the ~97% that
go straight to zero. Those failures are the entire point — a dataset of only
the tokens that survived long enough to be interesting is a dataset that will
teach a model to lose money.

The scheduler is a simple due-time priority queue. Each cycle it serves the
most-overdue mints up to the request budget, so when discovery outruns the
API allowance the system degrades by sampling old tokens less often rather
than by falling over.
"""
from __future__ import annotations

import heapq
import json
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..sources import dexscreener, geckoterminal, jupiter
from ..store.lake import DISCOVERIES, SNAPSHOTS, Lake, lake
from ..util.http import STATS
from ..util.log import get
from ..util.timeutil import human_age, now

log = get("degen.collect")

# (max_age_seconds, poll_interval_seconds). A token's cadence is the first row
# whose age bound it falls under. Young tokens are where all the decision-
# relevant information is, so they get sampled ~30x more often than day-olds.
CADENCE: list[tuple[float, float]] = [
    (5 * 60, 20),
    (30 * 60, 60),
    (3 * 3600, 180),
    (12 * 3600, 600),
    (48 * 3600, 1800),
    (96 * 3600, 7200),
]
RETIRE_AGE = 96 * 3600

# A token with no liquidity and no trades after this long is never coming back.
# Retiring it early frees budget for tokens that might actually matter.
DEAD_AGE = 45 * 60
DEAD_LIQ_USD = 400.0
DEAD_MIN_TRADES = 6


@dataclass(order=True)
class Tracked:
    due: float
    mint: str = field(compare=False)
    first_seen: float = field(compare=False, default=0.0)
    created_at: float | None = field(compare=False, default=None)
    polls: int = field(compare=False, default=0)
    misses: int = field(compare=False, default=0)
    peak_liq: float = field(compare=False, default=0.0)
    peak_price: float = field(compare=False, default=0.0)
    first_price: float | None = field(compare=False, default=None)
    retired: bool = field(compare=False, default=False)

    def age(self, t: float | None = None) -> float:
        t = t if t is not None else now()
        base = self.created_at or self.first_seen
        return max(0.0, t - base)

    def interval(self, t: float | None = None) -> float:
        a = self.age(t)
        for bound, iv in CADENCE:
            if a <= bound:
                return iv
        return float("inf")


class Registry:
    """Durable set of tracked mints plus the due-time heap."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.by_mint: dict[str, Tracked] = {}
        self.heap: list[Tracked] = []
        self.lock = threading.Lock()
        self._dirty = 0
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        n = 0
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = Tracked(
                    due=d.get("due", 0.0),
                    mint=d["mint"],
                    first_seen=d.get("first_seen", 0.0),
                    created_at=d.get("created_at"),
                    polls=d.get("polls", 0),
                    peak_liq=d.get("peak_liq", 0.0),
                    peak_price=d.get("peak_price", 0.0),
                    first_price=d.get("first_price"),
                    retired=d.get("retired", False),
                )
                self.by_mint[t.mint] = t
                if not t.retired:
                    heapq.heappush(self.heap, t)
                n += 1
        log.info("registry: loaded %d mints (%d active)", n, len(self.heap))

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with self.lock:
            rows = list(self.by_mint.values())
        with open(tmp, "w", encoding="utf-8") as fh:
            for t in rows:
                fh.write(
                    json.dumps(
                        {
                            "mint": t.mint,
                            "due": t.due,
                            "first_seen": t.first_seen,
                            "created_at": t.created_at,
                            "polls": t.polls,
                            "peak_liq": t.peak_liq,
                            "peak_price": t.peak_price,
                            "first_price": t.first_price,
                            "retired": t.retired,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        tmp.replace(self.path)

    def add(self, mint: str, created_at: float | None) -> Tracked | None:
        """Returns the Tracked entry if this mint is new, else None."""
        with self.lock:
            if mint in self.by_mint:
                return None
            t = Tracked(due=now(), mint=mint, first_seen=now(), created_at=created_at)
            self.by_mint[mint] = t
            heapq.heappush(self.heap, t)
            self._dirty += 1
            return t

    def due_batch(self, limit: int, horizon: float = 0.0) -> list[Tracked]:
        out: list[Tracked] = []
        t = now()
        with self.lock:
            while self.heap and len(out) < limit:
                head = self.heap[0]
                if head.retired:
                    heapq.heappop(self.heap)
                    continue
                if head.due > t + horizon:
                    break
                out.append(heapq.heappop(self.heap))
        return out

    def reschedule(self, t: Tracked) -> None:
        iv = t.interval()
        if iv == float("inf") or t.age() > RETIRE_AGE:
            t.retired = True
            return
        t.due = now() + iv
        with self.lock:
            heapq.heappush(self.heap, t)

    def active(self) -> int:
        with self.lock:
            return sum(1 for t in self.by_mint.values() if not t.retired)


class Collector:
    def __init__(
        self,
        lk: Lake | None = None,
        registry_path: str | Path = "data/registry.jsonl",
        track_budget_per_min: int = 40,
        enable_gecko: bool = True,
        enable_dexscreener: bool = True,
    ) -> None:
        self.lake = lk or lake()
        self.reg = Registry(Path(registry_path))
        self.track_budget = track_budget_per_min
        self.enable_gecko = enable_gecko
        self.enable_dexscreener = enable_dexscreener
        self.stop = threading.Event()
        self.n_discovered = 0
        self.n_snapshots = 0
        self.started = now()

    # ------------- discovery -------------

    def _ingest(self, rows: list[dict[str, Any]], source: str) -> int:
        fresh = 0
        disc_rows: list[dict[str, Any]] = []
        for r in rows:
            mint = r.get("mint")
            if not mint:
                continue
            t = self.reg.add(mint, r.get("created_at"))
            if t is None:
                continue
            fresh += 1
            d = dict(r)
            d["discovered_by"] = source
            d["discovered_at"] = t.first_seen
            disc_rows.append(d)
            if r.get("price_usd"):
                t.first_price = r["price_usd"]
        if disc_rows:
            self.lake.append_many(DISCOVERIES, disc_rows)
            # A discovery is also the first snapshot - don't throw the data away.
            self.lake.append_many(SNAPSHOTS, disc_rows)
            self.n_discovered += fresh
            self.n_snapshots += len(disc_rows)
        return fresh

    def discovery_loop(self) -> None:
        next_gecko = 0.0
        next_boost = 0.0
        while not self.stop.is_set():
            t0 = now()
            try:
                self._ingest(jupiter.recent(), "jupiter_recent")
            except Exception as exc:  # a source outage must not kill the loop
                log.warning("jupiter recent failed: %s", exc)

            if self.enable_gecko and t0 >= next_gecko:
                next_gecko = t0 + 45
                try:
                    self._ingest(geckoterminal.new_pools(), "gecko_new_pools")
                except Exception as exc:
                    log.warning("gecko new_pools failed: %s", exc)

            if self.enable_dexscreener and t0 >= next_boost:
                next_boost = t0 + 300
                try:
                    boosted = dexscreener.token_boosts()
                    mints = [b.get("tokenAddress") for b in boosted if b.get("chainId") == "solana"]
                    if mints:
                        self._ingest(dexscreener.pairs_for_tokens(mints[:30]), "ds_boosts")
                except Exception as exc:
                    log.warning("dexscreener boosts failed: %s", exc)

            self.stop.wait(max(0.0, 12.0 - (now() - t0)))

    # ------------- tracking -------------

    def _should_retire(self, t: Tracked, row: dict[str, Any]) -> bool:
        age = t.age()
        if age < DEAD_AGE:
            return False
        liq = row.get("liquidity") or 0.0
        trades = (row.get("s1h_numBuys") or 0) + (row.get("s1h_numSells") or 0)
        return liq < DEAD_LIQ_USD and trades < DEAD_MIN_TRADES

    def track_loop(self) -> None:
        # One Jupiter search call covers 100 mints, so the per-minute request
        # budget translates directly into a per-minute mint budget.
        while not self.stop.is_set():
            cycle_start = now()
            batch = self.reg.due_batch(jupiter.MAX_BATCH)
            if not batch:
                self.stop.wait(2.0)
                continue
            mints = [t.mint for t in batch]
            try:
                rows = jupiter.search(mints)
            except Exception as exc:
                log.warning("track batch failed: %s", exc)
                rows = []

            by_mint = {r["mint"]: r for r in rows if r.get("mint")}
            out: list[dict[str, Any]] = []
            for t in batch:
                r = by_mint.get(t.mint)
                t.polls += 1
                if r is None:
                    t.misses += 1
                    # Jupiter drops tokens it has decided are noise. Three
                    # consecutive misses means we will never see it again.
                    if t.misses >= 3:
                        t.retired = True
                        continue
                    self.reg.reschedule(t)
                    continue
                t.misses = 0
                r["track_poll"] = t.polls
                r["first_seen"] = t.first_seen
                liq = r.get("liquidity") or 0.0
                px = r.get("price_usd") or 0.0
                t.peak_liq = max(t.peak_liq, liq)
                t.peak_price = max(t.peak_price, px)
                if t.first_price is None and px:
                    t.first_price = px
                out.append(r)
                if self._should_retire(t, r):
                    t.retired = True
                else:
                    self.reg.reschedule(t)

            if out:
                self.n_snapshots += self.lake.append_many(SNAPSHOTS, out)

            # Pace to the request budget.
            min_cycle = 60.0 / max(1, self.track_budget)
            self.stop.wait(max(0.0, min_cycle - (now() - cycle_start)))

    # ------------- housekeeping -------------

    def report_loop(self) -> None:
        while not self.stop.is_set():
            self.stop.wait(60)
            if self.stop.is_set():
                break
            self.reg.save()
            up = now() - self.started
            rate = self.n_discovered / max(up / 3600.0, 1e-9)
            log.info(
                "up=%s discovered=%d (%.0f/h) snapshots=%d active=%d http_ok=%d err=%d codes=%s",
                human_age(up), self.n_discovered, rate, self.n_snapshots,
                self.reg.active(), STATS.ok, STATS.err,
                dict(sorted(STATS.by_code.items(), key=lambda kv: -kv[1])[:4]),
            )

    def run(self) -> None:
        threads = [
            threading.Thread(target=self.discovery_loop, name="discover", daemon=True),
            threading.Thread(target=self.track_loop, name="track", daemon=True),
            threading.Thread(target=self.report_loop, name="report", daemon=True),
        ]
        for th in threads:
            th.start()
        log.info("collector running: %d threads, %d mints already known", len(threads), len(self.reg.by_mint))
        try:
            while not self.stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            self.reg.save()
            log.info("collector stopped: %d discovered, %d snapshots", self.n_discovered, self.n_snapshots)


def main() -> None:
    from ..util.log import setup

    setup()
    c = Collector()
    signal.signal(signal.SIGTERM, lambda *_: c.stop.set())
    signal.signal(signal.SIGINT, lambda *_: c.stop.set())
    c.run()


if __name__ == "__main__":
    main()
