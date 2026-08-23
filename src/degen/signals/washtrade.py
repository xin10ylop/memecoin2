"""Wash-trade detection and volume correction.

Roughly 21.4% of pre-migration pump.fun transactions are wash trades, and
Solidus Labs flags 98.6% of pump.fun tokens as rug or pump-and-dump. Volume is
therefore the least trustworthy field in the entire dataset, and any feature
built on raw volume is partly measuring manipulation rather than demand.

Chainalysis's canonical heuristic needs per-address trade histories - trades by
one address inside a 25-block window with under 1% notional delta, occurring at
least three times. We do not have per-address data from the aggregate feeds the
bot runs on, so this module implements what *is* computable from aggregate
counts, which turns out to catch the same behaviour from a different angle:

Wash trading inflates volume and trade count while adding no new participants.
So the tells are ratios, not levels - volume per distinct trader, trades per
distinct trader, and volume relative to holder growth. A token doing $40k of
volume across four traders is not being discovered; it is being painted.

Two outputs: a 0-1 suspicion score usable as a feature or a filter, and a
discount factor to apply to volume before it feeds anything else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Baseline discount applied to all Solana pre-migration volume, from the
# measured 21.4% wash-trade share. Applied even when nothing looks unusual,
# because the base rate is not zero.
BASE_WASH_SHARE = 0.214

# A real participant does not usually round-trip more than a few times in five
# minutes; above this, trade count is being manufactured.
SUSPICIOUS_TRADES_PER_TRADER = 4.0
EXTREME_TRADES_PER_TRADER = 10.0

# Volume per distinct trader, in USD. Wash trades are typically large relative
# to the number of people involved.
SUSPICIOUS_VOL_PER_TRADER = 3_000.0
EXTREME_VOL_PER_TRADER = 15_000.0


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


@dataclass
class WashAssessment:
    score: float = 0.0            # 0 = looks organic, 1 = almost certainly painted
    discount: float = 1.0 - BASE_WASH_SHARE
    trades_per_trader: float | None = None
    vol_per_trader: float | None = None
    vol_per_holder: float | None = None
    reasons: list[str] | None = None

    def as_features(self) -> dict[str, Any]:
        return {
            "wash_score": round(self.score, 4),
            "wash_discount": round(self.discount, 4),
            "trades_per_trader": self.trades_per_trader,
            "vol_per_trader": self.vol_per_trader,
        }


def _ramp(v: float | None, lo: float, hi: float) -> float:
    if v is None or hi == lo:
        return 0.0
    return float(min(1.0, max(0.0, (v - lo) / (hi - lo))))


def assess(feat: dict[str, Any]) -> WashAssessment:
    """Judge how much of a token's apparent activity is real."""
    buys = _num(feat.get("s5m_numBuys")) or 0.0
    sells = _num(feat.get("s5m_numSells")) or 0.0
    traders = _num(feat.get("s5m_numTraders"))
    bvol = _num(feat.get("s5m_buyVolume")) or 0.0
    svol = _num(feat.get("s5m_sellVolume")) or 0.0
    holders = _num(feat.get("holder_count"))
    trades = buys + sells
    vol = bvol + svol
    reasons: list[str] = []

    tpt = (trades / traders) if traders and traders > 0 else None
    vpt = (vol / traders) if traders and traders > 0 else None
    vph = (vol / holders) if holders and holders > 0 else None

    score = 0.0
    if tpt is not None:
        s = _ramp(tpt, SUSPICIOUS_TRADES_PER_TRADER, EXTREME_TRADES_PER_TRADER)
        if s > 0:
            reasons.append(f"{tpt:.1f} trades per distinct trader")
        score = max(score, s)
    if vpt is not None:
        s = _ramp(vpt, SUSPICIOUS_VOL_PER_TRADER, EXTREME_VOL_PER_TRADER)
        if s > 0:
            reasons.append(f"${vpt:,.0f} volume per distinct trader")
        score = max(score, s)
    # Heavy volume with essentially no holder base is the clearest tell.
    if holders is not None and holders <= 3 and vol > 2_000:
        score = max(score, 0.8)
        reasons.append(f"${vol:,.0f} volume across only {holders:.0f} holders")

    # Discount scales from the population base rate up to near-total when the
    # activity looks manufactured.
    discount = (1.0 - BASE_WASH_SHARE) * (1.0 - 0.75 * score)
    return WashAssessment(
        score=round(score, 4),
        discount=round(discount, 4),
        trades_per_trader=round(tpt, 3) if tpt is not None else None,
        vol_per_trader=round(vpt, 2) if vpt is not None else None,
        vol_per_holder=round(vph, 2) if vph is not None else None,
        reasons=reasons or None,
    )


def corrected_volume(feat: dict[str, Any]) -> float:
    """Total 5-minute volume with the wash estimate removed."""
    bvol = _num(feat.get("s5m_buyVolume")) or 0.0
    svol = _num(feat.get("s5m_sellVolume")) or 0.0
    return (bvol + svol) * assess(feat).discount
