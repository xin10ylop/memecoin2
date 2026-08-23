"""Smart-money tracking: find wallets that are early in winners, then follow them.

This is the mechanic that "alpha groups" sell access to, and it is entirely
reproducible from free data. The method:

1. From our own census, identify tokens that achieved a meaningful multiple.
2. Pull each winner's holder list from RugCheck and record which wallets held
   it. Holders present while the token was still small were early.
3. Credit those wallets. A wallet that shows up early in many winners is either
   skilled, connected, or the insider - and for copy-trading purposes the
   distinction matters less than the persistence.
4. Score new tokens by how many high-credit wallets are already holding.

The three failure modes this design has to survive, because they are what make
naive copy-trading lose money:

**Luck.** With thousands of wallets buying thousands of tokens, some will be
early in several winners by chance. The scorer therefore requires a minimum
number of distinct winners and uses a shrunk hit rate rather than a raw count,
so one lucky wallet with two hits cannot outrank a consistent one with twenty.

**Insiders and bundlers.** The dev and the wallets funded by the dev are early
in *every* one of their own tokens and cannot be copied - by the time their
buy is visible the price already reflects it. Wallets that appear only in
tokens from a single creator are excluded.

**Decay.** Wallet alpha is not permanent. Credits are exponentially decayed by
age, so a wallet that stopped performing three weeks ago stops scoring.

**Bundles masquerading as consensus.** This one was found by running the code
on real data: the first build surfaced a dozen wallets with *identical*
statistics - 7 winners from 7 tokens, same best multiple, same 7 creators.
Wallets that always appear together are one operator running many addresses,
and counting them as seven independent confirmations overstates the evidence by
a factor of seven. `cluster_wallets()` groups addresses by the overlap of the
token sets they appear in, and confluence counts *clusters*, not addresses.

The confluence signal - N distinct credited clusters in the same token within a
window - is much stronger than any single wallet, and is the form actually
worth trading.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..sources.rugcheck import report as rugcheck_report
from ..util.log import get
from ..util.timeutil import now

log = get("degen.smartmoney")

# Wallets seen in this many distinct winners before they count at all. Below
# this the estimate is indistinguishable from luck.
MIN_WINNERS = 3
# Credit halves after this long. Alpha decays; a stale edge is not an edge.
HALFLIFE_S = 14 * 86400.0
# A wallet whose winners all came from one creator is that creator's own.
MIN_DISTINCT_CREATORS = 2


@dataclass
class WalletStat:
    wallet: str
    winners: int = 0
    total_seen: int = 0
    weighted_credit: float = 0.0
    best_multiple: float = 0.0
    creators: set[str] = field(default_factory=set)
    last_seen: float = 0.0
    tokens: list[str] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        """Shrunk toward the base rate so small samples cannot dominate.

        A wallet with 2 wins from 2 tokens scores below one with 15 from 40.
        """
        prior_n, prior_p = 20.0, 0.08
        return (self.winners + prior_n * prior_p) / (self.total_seen + prior_n)

    @property
    def is_insider(self) -> bool:
        return len(self.creators) < MIN_DISTINCT_CREATORS

    def score(self, t: float | None = None) -> float:
        if self.winners < MIN_WINNERS or self.is_insider:
            return 0.0
        t = t if t is not None else now()
        decay = 0.5 ** ((t - self.last_seen) / HALFLIFE_S) if self.last_seen else 0.0
        # Credit for being right, scaled by how right and how recently.
        return float(self.hit_rate * math.log1p(self.weighted_credit) * decay)


class SmartMoneyBook:
    """Persistent ledger of wallet performance."""

    def __init__(self, path: str | Path = "data/smartmoney.json") -> None:
        self.path = Path(path)
        self.wallets: dict[str, WalletStat] = {}
        self.scanned: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for w, v in (d.get("wallets") or {}).items():
            self.wallets[w] = WalletStat(
                wallet=w, winners=v.get("winners", 0), total_seen=v.get("total_seen", 0),
                weighted_credit=v.get("weighted_credit", 0.0), best_multiple=v.get("best_multiple", 0.0),
                creators=set(v.get("creators") or []), last_seen=v.get("last_seen", 0.0),
                tokens=v.get("tokens") or [],
            )
        self.scanned = set(d.get("scanned") or [])
        log.info("smartmoney: loaded %d wallets, %d tokens scanned", len(self.wallets), len(self.scanned))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "wallets": {
                w: {
                    "winners": s.winners, "total_seen": s.total_seen,
                    "weighted_credit": round(s.weighted_credit, 5),
                    "best_multiple": round(s.best_multiple, 3),
                    "creators": sorted(s.creators)[:50], "last_seen": s.last_seen,
                    "tokens": s.tokens[-25:],
                }
                for w, s in self.wallets.items()
            },
            "scanned": sorted(self.scanned)[-20000:],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(self.path)

    # ---------------- credit assignment ----------------

    def _holders(self, mint: str) -> tuple[list[str], str | None]:
        """Return (retail holder owners, creator) for a mint, pool accounts removed."""
        rep = rugcheck_report(mint)
        if not rep:
            return [], None
        infra: set[str] = set()
        creator = rep.get("creator")
        for addr, meta in (rep.get("knownAccounts") or {}).items():
            if (meta or {}).get("type") in ("AMM", "LOCKER"):
                infra.add(addr)
        for mk in rep.get("markets") or []:
            for k in ("pubkey", "liquidityA", "liquidityB", "mintLP"):
                if mk.get(k):
                    infra.add(mk[k])
        owners = []
        for h in rep.get("topHolders") or []:
            o = h.get("owner")
            if not o or o in infra or h.get("address") in infra:
                continue
            if creator and o == creator:
                continue
            owners.append(o)
        return owners, creator

    def observe(self, mint: str, multiple: float, creator: str | None = None, at: float | None = None) -> int:
        """Record the outcome of one token and credit its holders."""
        if mint in self.scanned:
            return 0
        # Census rows come from pandas, where a missing creator is NaN rather
        # than None; letting that into the set makes it unsortable later.
        if not isinstance(creator, str) or not creator:
            creator = None
        at = at if at is not None else now()
        owners, rep_creator = self._holders(mint)
        if creator is None and isinstance(rep_creator, str) and rep_creator:
            creator = rep_creator
        self.scanned.add(mint)
        if not owners:
            return 0
        # Credit scales with log of the multiple: a 50x is worth more than a 2x
        # but not 25x more, because a single jackpot should not mint an oracle.
        won = multiple >= 2.0
        credit = math.log1p(max(0.0, multiple - 1.0)) if won else 0.0
        for o in owners:
            s = self.wallets.get(o)
            if s is None:
                s = WalletStat(wallet=o)
                self.wallets[o] = s
            s.total_seen += 1
            s.last_seen = max(s.last_seen, at)
            if creator:
                s.creators.add(creator)
            if won:
                s.winners += 1
                s.weighted_credit += credit
                s.best_multiple = max(s.best_multiple, multiple)
                s.tokens.append(mint)
        return len(owners)

    # ---------------- scoring ----------------

    # ---------------- cluster detection ----------------

    def cluster_wallets(self, min_overlap: float = 0.8) -> dict[str, int]:
        """Map wallet -> cluster id, grouping addresses that move together.

        Two wallets belong to the same operator when the sets of tokens they
        appear in are near-identical. Exact-signature grouping catches the
        common case cheaply; a Jaccard pass then merges the near-misses.
        Union-find keeps it transitive, so a chain of overlapping addresses
        collapses to one actor rather than several.
        """
        wallets = [w for w, s in self.wallets.items() if s.tokens]
        sets = {w: frozenset(self.wallets[w].tokens) for w in wallets}

        parent: dict[str, str] = {w: w for w in wallets}

        def find(a: str) -> str:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # Pass 1: identical token sets are certainly one operator.
        by_sig: dict[frozenset, list[str]] = defaultdict(list)
        for w in wallets:
            by_sig[sets[w]].append(w)
        for group in by_sig.values():
            for other in group[1:]:
                union(group[0], other)

        # Pass 2: near-identical sets, compared only within shared tokens so the
        # comparison stays linear in practice rather than quadratic in wallets.
        by_token: dict[str, list[str]] = defaultdict(list)
        for w in wallets:
            for tok in sets[w]:
                by_token[tok].append(w)
        checked: set[tuple[str, str]] = set()
        for group in by_token.values():
            if len(group) > 60:      # a popular token tells us nothing about linkage
                continue
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    key = (a, b) if a < b else (b, a)
                    if key in checked:
                        continue
                    checked.add(key)
                    sa, sb = sets[a], sets[b]
                    inter = len(sa & sb)
                    if not inter:
                        continue
                    if inter / len(sa | sb) >= min_overlap:
                        union(a, b)

        roots = {}
        out: dict[str, int] = {}
        for w in wallets:
            r = find(w)
            if r not in roots:
                roots[r] = len(roots)
            out[w] = roots[r]
        return out

    def top(self, n: int = 25) -> list[WalletStat]:
        t = now()
        return sorted(
            (s for s in self.wallets.values() if s.score(t) > 0),
            key=lambda s: -s.score(t),
        )[:n]

    def credited(self) -> dict[str, float]:
        t = now()
        return {w: s.score(t) for w, s in self.wallets.items() if s.score(t) > 0}

    def confluence(self, mint: str, min_clusters: int = 2) -> dict[str, Any]:
        """How many *independent* credited actors are already in this token.

        This is the tradable form of the signal. One good wallet buying is
        noise; several independent actors arriving in the same token inside a
        short window is the thing worth acting on - provided they really are
        independent, which is what the clustering establishes.
        """
        creds = self.credited()
        if not creds:
            return {"n_wallets": 0, "n_clusters": 0, "score": 0.0, "wallets": [], "available": False}
        clusters = self.cluster_wallets()
        owners, _ = self._holders(mint)
        hits = [(o, creds[o]) for o in dict.fromkeys(owners) if o in creds]
        hits.sort(key=lambda kv: -kv[1])

        # Credit each independent actor once, at the score of its best wallet.
        best_per_cluster: dict[int, float] = {}
        for w, sc in hits:
            cid = clusters.get(w, -1)
            best_per_cluster[cid] = max(best_per_cluster.get(cid, 0.0), sc)
        n_clusters = len(best_per_cluster)
        total = sum(best_per_cluster.values())
        return {
            "n_wallets": len(hits),
            "n_clusters": n_clusters,
            "score": total if n_clusters >= min_clusters else 0.0,
            "wallets": hits[:10],
            "available": True,
        }


def build_from_census(book: SmartMoneyBook, rows: Iterable[dict[str, Any]], limit: int = 200) -> int:
    """Populate the book from collected outcomes.

    `rows` should carry `mint`, `maxx` and optionally `dev`. Winners are scanned
    first, since they carry the information; a sample of losers is scanned too so
    that `total_seen` reflects a real denominator rather than only successes.
    """
    rows = list(rows)
    winners = sorted((r for r in rows if (r.get("maxx") or 0) >= 2.0), key=lambda r: -(r.get("maxx") or 0))
    losers = [r for r in rows if (r.get("maxx") or 0) < 2.0][: max(0, limit - len(winners))]
    n = 0
    for r in winners[:limit] + losers:
        got = book.observe(str(r["mint"]), float(r.get("maxx") or 0.0), r.get("dev"))
        n += 1 if got else 0
        if n % 25 == 0 and n:
            book.save()
            log.info("smartmoney: scanned %d tokens, %d wallets known", n, len(book.wallets))
    book.save()
    return n
