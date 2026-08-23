"""Deployer reputation, computed walk-forward.

The largest single lift found anywhere in the research: scoring creators by the
realized graduation rate of their prior launches separates an elite tier that
graduates at 71% from a 0.63% population base — a 35-110x difference. It is also
the signal most easily faked by a careless implementation, in two distinct ways.

**Selection on the reported statistic.** The published elite tier was chosen by
the same graduation rate then quoted for it, which guarantees the number. The
only honest version is walk-forward: a deployer's score at the moment token N
launches may use outcomes from tokens 1..N-1 and nothing else. `score_at()`
enforces that by timestamp, and `evaluate_walk_forward()` measures the signal
the way it would actually have been available.

**Confusing volume with skill.** A creator on their 20,000th mint is a factory
whose tokens fail at the base rate; a creator on their third whose first two
both ran is either skilled or lucky and there is no way to tell yet. The score
therefore shrinks toward the population rate with a Beta prior, so a small
sample cannot produce a large score.

Jupiter reports `devMints` directly, which gives the volume side for free. This
module adds the part that matters — what those prior mints actually *did*.
"""
from __future__ import annotations

import json
import math
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..util.log import get
from ..util.timeutil import now

log = get("degen.deployer")

# Beta prior. Weight equivalent to this many prior launches at the base rate,
# so a 2-for-2 record does not outrank a 30-for-100 one.
PRIOR_WEIGHT = 25.0
DEFAULT_BASE_RATE = 0.06
# Below this, report the prior and say so rather than pretending to know.
MIN_HISTORY = 3


@dataclass
class DeployerRecord:
    dev: str
    # (launch_time, was_success) sorted by time, so a walk-forward lookup is a
    # bisect rather than a scan.
    history: list[tuple[float, bool]] = field(default_factory=list)

    def add(self, at: float, success: bool) -> None:
        self.history.append((at, success))
        self.history.sort(key=lambda x: x[0])

    def before(self, t: float) -> tuple[int, int]:
        """(successes, total) strictly before time t."""
        idx = bisect_left([h[0] for h in self.history], t)
        prior = self.history[:idx]
        return sum(1 for _, ok in prior if ok), len(prior)


class DeployerBook:
    def __init__(self, path: str | Path = "data/deployers.json", base_rate: float = DEFAULT_BASE_RATE) -> None:
        self.path = Path(path)
        self.base_rate = base_rate
        self.devs: dict[str, DeployerRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self.base_rate = d.get("base_rate", self.base_rate)
        for dev, hist in (d.get("devs") or {}).items():
            self.devs[dev] = DeployerRecord(dev, [(float(t), bool(ok)) for t, ok in hist])
        log.info("deployers: loaded %d creators", len(self.devs))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "base_rate": self.base_rate,
            "devs": {k: [[t, ok] for t, ok in v.history] for k, v in self.devs.items()},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(self.path)

    def observe(self, dev: str | None, launched_at: float, success: bool) -> None:
        if not isinstance(dev, str) or not dev:
            return
        rec = self.devs.get(dev)
        if rec is None:
            rec = DeployerRecord(dev)
            self.devs[dev] = rec
        rec.add(launched_at, success)

    # ---------------- scoring ----------------

    def score_at(self, dev: str | None, t: float) -> dict[str, Any]:
        """Shrunk success rate using only launches strictly before `t`."""
        out = {
            "dev_prior_launches": 0,
            "dev_prior_successes": 0,
            "dev_success_rate": self.base_rate,
            "dev_score": 0.0,
            "dev_known": False,
        }
        if not isinstance(dev, str) or not dev:
            return out
        rec = self.devs.get(dev)
        if rec is None:
            return out
        succ, total = rec.before(t)
        out["dev_prior_launches"] = total
        out["dev_prior_successes"] = succ
        if total == 0:
            return out
        shrunk = (succ + PRIOR_WEIGHT * self.base_rate) / (total + PRIOR_WEIGHT)
        out["dev_success_rate"] = shrunk
        out["dev_known"] = total >= MIN_HISTORY
        if total >= MIN_HISTORY:
            # Log-ratio against the base rate, squashed to a bounded score.
            ratio = shrunk / max(self.base_rate, 1e-9)
            out["dev_score"] = float(max(-1.0, min(1.0, math.log(ratio) / math.log(6.0))))
        return out

    def build(self, rows: Iterable[dict[str, Any]], success_key: str = "success") -> int:
        n = 0
        for r in rows:
            dev = r.get("dev")
            at = r.get("created_at") or r.get("first_seen")
            if not isinstance(dev, str) or not dev or at is None:
                continue
            self.observe(dev, float(at), bool(r.get(success_key)))
            n += 1
        succ = sum(1 for rec in self.devs.values() for _, ok in rec.history if ok)
        tot = sum(len(rec.history) for rec in self.devs.values())
        if tot:
            self.base_rate = succ / tot
        self.save()
        log.info("deployers: %d creators from %d launches, base rate %.4f", len(self.devs), n, self.base_rate)
        return n


def evaluate_walk_forward(book: DeployerBook, rows: list[dict[str, Any]], success_key: str = "success") -> dict[str, Any]:
    """Measure the signal as it would actually have been available.

    For each launch, score its creator using only that creator's earlier
    launches, then compare success rates across score buckets. Any lift here is
    lift you could have traded on; lift measured the other way is not.
    """
    scored: list[tuple[float, bool, int]] = []
    for r in rows:
        at = r.get("created_at") or r.get("first_seen")
        if at is None:
            continue
        s = book.score_at(r.get("dev"), float(at))
        if not s["dev_known"]:
            continue
        scored.append((s["dev_score"], bool(r.get(success_key)), s["dev_prior_launches"]))
    if len(scored) < 30:
        return {"n": len(scored), "note": "insufficient creators with prior history"}
    scored.sort(key=lambda x: x[0])
    n = len(scored)
    base = sum(1 for _, ok, _ in scored if ok) / n
    third = max(1, n // 3)
    bottom = scored[:third]
    top = scored[-third:]
    br = sum(1 for _, ok, _ in bottom if ok) / len(bottom)
    tr = sum(1 for _, ok, _ in top if ok) / len(top)
    return {
        "n": n,
        "base_rate": round(base, 4),
        "bottom_tercile_rate": round(br, 4),
        "top_tercile_rate": round(tr, 4),
        "lift": round(tr / base, 2) if base > 0 else None,
        "spread": round(tr - br, 4),
    }
