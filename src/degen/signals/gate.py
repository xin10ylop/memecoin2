"""The traction gate: the default entry rule, and the only one validated
out of sample.

Why this exists in preference to the scorer and the model. Measured on a clean
census with a chronological split - fit on the earlier 60% of mints, tested on
the later 40%, no token straddling the boundary:

    strategy                       IS n   IS ROI   OOS n  OOS ROI  OOS PF  P(>0)
    holders >= 20                   313    10.4%     214     9.8%    1.52   0.96
    + liquidity and buy/sell        248    14.1%     168    18.1%    2.06   1.00
    + market cap                    219    16.6%     142    23.1%    2.41   1.00
    + full safety stack             114    21.2%      68    19.3%    2.14   0.97
    + rule scorer                    82    26.9%      46    10.5%    1.60   0.80
    + gradient-boosted model         64    39.2%      36    17.0%    1.83   0.86

The first four rows have in-sample and out-of-sample figures that agree, which
is what generalisation looks like. The last two do not: the scorer's apparent
edge more than halves out of sample and the model's falls by more than half,
while both cut the trade count to a third. They are fitting the training window.

So the default is row three - four conditions, no fitted weights, no model:

    holders >= 20, liquidity >= $3,000, buy/sell ratio >= 0.55, market cap >= $5,000

All four say the same thing in different words: real participants have arrived
and are net buying. The market-cap condition is the one that adds independent
information, being nearly uncorrelated with liquidity (r = 0.06).

An honest caveat on those thresholds: they were chosen from univariate analysis
of an earlier, smaller census, so they are not fully out-of-sample themselves. A
sweep of neighbouring values (holders 10-40, liquidity $2k-$5k, ratio 0.50-0.60)
stays profitable throughout, so this is a plateau rather than a spike - but the
level of the returns above is optimistic by some unmeasured amount, and the
right correction is to re-run `degen validate` as the census grows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


@dataclass
class GateConfig:
    min_holders: float = 20.0
    min_liquidity_usd: float = 3_000.0
    min_buy_sell_ratio: float = 0.55
    min_mcap_usd: float = 5_000.0
    # Conviction is graded within the gate so sizing can still vary, but the
    # grading only ever scales a position the gate has already approved - it
    # cannot admit one the gate rejected.
    conviction_floor: float = 0.35


@dataclass
class GateResult:
    passed: bool
    conviction: float
    failed: list[str]
    values: dict[str, float | None]

    def explain(self) -> str:
        if self.passed:
            v = self.values
            return (f"pass conv={self.conviction:.2f} "
                    f"holders={v['holders']:.0f} liq=${v['liquidity']:,.0f} "
                    f"bsr={v['buy_sell_ratio']:.2f} mcap=${v['mcap']:,.0f}")
        return "fail: " + ", ".join(self.failed)


class TractionGate:
    """Four conditions, no weights. The default entry rule."""

    def __init__(self, cfg: GateConfig | None = None) -> None:
        self.cfg = cfg or GateConfig()

    def check(self, f: dict[str, Any]) -> GateResult:
        c = self.cfg
        holders = _num(f.get("holder_count"))
        liq = _num(f.get("liquidity"))
        bsr = _num(f.get("buy_sell_ratio"))
        mcap = _num(f.get("mcap"))
        values = {"holders": holders, "liquidity": liq, "buy_sell_ratio": bsr, "mcap": mcap}

        failed: list[str] = []
        # A missing value is a failure, never a pass. An unmeasured condition is
        # not a satisfied one.
        if holders is None or holders < c.min_holders:
            failed.append(f"holders {holders} < {c.min_holders:.0f}")
        if liq is None or liq < c.min_liquidity_usd:
            failed.append(f"liquidity {liq} < {c.min_liquidity_usd:,.0f}")
        if bsr is None or bsr < c.min_buy_sell_ratio:
            failed.append(f"buy/sell {bsr} < {c.min_buy_sell_ratio}")
        if mcap is None or mcap < c.min_mcap_usd:
            failed.append(f"mcap {mcap} < {c.min_mcap_usd:,.0f}")
        if failed:
            return GateResult(False, 0.0, failed, values)

        # Grade how far past each threshold the token sits, capped so one
        # extreme reading cannot carry the others.
        def over(v: float, thresh: float, span: float) -> float:
            return min(1.0, max(0.0, (v - thresh) / span))

        parts = [
            over(holders, c.min_holders, 80.0),
            over(liq, c.min_liquidity_usd, 20_000.0),
            over(bsr, c.min_buy_sell_ratio, 0.30),
            over(mcap, c.min_mcap_usd, 30_000.0),
        ]
        conv = c.conviction_floor + (1.0 - c.conviction_floor) * (sum(parts) / len(parts))
        return GateResult(True, round(conv, 4), [], values)

    def signal(self, f: dict[str, Any]) -> float | None:
        """Backtest/live signal interface: conviction or None."""
        r = self.check(f)
        return r.conviction if r.passed else None
