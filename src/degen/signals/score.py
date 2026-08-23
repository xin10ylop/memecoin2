"""Conviction scoring.

Two scorers with the same interface. The rule scorer is a transparent additive
model over the signals that survived testing on the collected census; the model
scorer is a gradient-boosted classifier that takes over once enough data exists
to train one honestly. Until then `CompositeScorer` runs on rules alone - a
model fit on 26 positive examples is not a model, it is a memorised list.

Why an explicit rule model at all, rather than waiting for the ML: every weight
here is auditable and every term corresponds to a measurable claim. When the bot
buys something stupid you can read off exactly which term did it. That property
is worth more than a couple of points of AUC.

The evidence behind the default weights, from an unbiased census of launches
collected by this system (see docs/RESEARCH.md for the run):

    signal                     hit rate vs base rate (1.3x in 1h)
    initial mcap > $7.5k       35.1%  vs  8.7%   (2x, full window)
    holders > ~8               20.6%  vs  2.4%
    holder count rising        24.0%  vs  1.3%
    liquidity rising           24.6%  vs  3.2%
    buy/sell ratio > 0.56      18.5%  vs  2.9%
    price above first print    22.7%  vs  2.5%
    low liquidity-per-holder   20.4%  vs  1.3%

All of these say the same thing in different words: real participants are
arriving. That is the only thing that has ever separated a token that runs from
a token that does not, and it is not observable at t=0 - which is why this
system deliberately does not snipe the mint.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util.log import get

log = get("degen.signal")


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _ramp(v: float | None, lo: float, hi: float) -> float:
    """0 below lo, 1 above hi, linear between. Missing data scores 0, never a
    free pass - an unmeasured signal is not a positive signal."""
    if v is None:
        return 0.0
    if hi == lo:
        return 1.0 if v >= hi else 0.0
    return float(min(1.0, max(0.0, (v - lo) / (hi - lo))))


@dataclass
class RuleWeights:
    holders: float = 1.6
    holder_growth: float = 2.0
    initial_mcap: float = 1.4
    liquidity: float = 1.2
    liquidity_growth: float = 1.8
    buy_pressure: float = 1.5
    volume_imbalance: float = 0.9
    price_momentum: float = 1.3
    organic_retail: float = 1.1      # many holders per unit of liquidity
    trade_activity: float = 0.9
    fresh_dev: float = 0.7
    # Penalties, subtracted.
    dev_factory: float = 1.2
    dumping: float = 2.0
    concentration: float = 1.0
    stalling: float = 0.8


@dataclass
class ScoreResult:
    score: float                       # 0..1 conviction
    passed: bool
    terms: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    source: str = "rules"

    def explain(self) -> str:
        parts = sorted(self.terms.items(), key=lambda kv: -abs(kv[1]))
        body = "  ".join(f"{k}{v:+.2f}" for k, v in parts if abs(v) > 0.01)
        return f"score={self.score:.3f} [{self.source}] {body}"


class RuleScorer:
    def __init__(self, weights: RuleWeights | None = None, threshold: float = 0.55) -> None:
        self.w = weights or RuleWeights()
        self.threshold = threshold

    def score(self, f: dict[str, Any]) -> ScoreResult:
        w = self.w
        t: dict[str, float] = {}
        notes: list[str] = []

        holders = _num(f.get("holder_count"))
        liq = _num(f.get("liquidity"))
        bsr = _num(f.get("buy_sell_ratio"))
        imb = _num(f.get("vol_imbalance"))
        trades = _num(f.get("trades_total"))
        dev_mints = _num(f.get("dev_mints"))
        lph = _num(f.get("liq_per_holder"))
        hslope = _num(f.get("hold_slope"))
        lslope = _num(f.get("liq_slope"))
        pxf = _num(f.get("px_from_first"))
        pxp = _num(f.get("px_from_peak"))

        # --- positive evidence ---
        t["holders"] = w.holders * _ramp(holders, 8, 60)
        t["holder_growth"] = w.holder_growth * _ramp(hslope, 0.0, 0.02)
        t["liquidity"] = w.liquidity * _ramp(liq, 3_000, 25_000)
        # Independent of liquidity in our data (r = 0.06), and the single
        # strongest gate we found: 8.7% base 2x rate rises to 35% above $7.5k.
        t["initial_mcap"] = w.initial_mcap * _ramp(_num(f.get("mcap")), 4_000, 20_000)
        t["liquidity_growth"] = w.liquidity_growth * _ramp(lslope, 0.0, 0.005)
        t["buy_pressure"] = w.buy_pressure * _ramp(bsr, 0.52, 0.75)
        t["volume_imbalance"] = w.volume_imbalance * _ramp(imb, 0.0, 0.5)
        t["price_momentum"] = w.price_momentum * _ramp(pxf, 1.0, 1.6)
        t["trade_activity"] = w.trade_activity * _ramp(trades, 8, 60)
        # Low liquidity per holder means many small real buyers rather than one
        # whale providing the entire float. Inverted ramp.
        t["organic_retail"] = w.organic_retail * (1.0 - _ramp(lph, 300, 2_500)) if lph is not None else 0.0
        t["fresh_dev"] = w.fresh_dev * (1.0 if (dev_mints is not None and dev_mints <= 3) else 0.0)

        # --- penalties ---
        if dev_mints is not None and dev_mints > 100:
            t["dev_factory"] = -w.dev_factory * _ramp(math.log10(max(dev_mints, 1)), 2.0, 4.0)
            notes.append(f"creator has {dev_mints:.0f} prior mints")
        if bsr is not None and bsr < 0.45:
            t["dumping"] = -w.dumping * _ramp(0.45 - bsr, 0.0, 0.25)
            notes.append("net selling")
        top = _num(f.get("top_holders_pct"))
        if top is not None and top > 40:
            t["concentration"] = -w.concentration * _ramp(top, 40, 80)
        # Already well off its own high: the move may be over.
        if pxp is not None and pxp < 0.75:
            t["stalling"] = -w.stalling * _ramp(0.75 - pxp, 0.0, 0.35)
            notes.append("well below local peak")

        raw = sum(t.values())
        max_pos = (w.holders + w.holder_growth + w.liquidity + w.liquidity_growth +
                   w.buy_pressure + w.volume_imbalance + w.price_momentum +
                   w.trade_activity + w.organic_retail + w.fresh_dev + w.initial_mcap)
        score = max(0.0, min(1.0, raw / max_pos))
        return ScoreResult(score=score, passed=score >= self.threshold, terms=t, notes=notes, source="rules")


class ModelScorer:
    """LightGBM scorer. Loads the artifact written by model.train."""

    def __init__(self, model_path: str | Path = "models/signal_lgbm.txt",
                 features_path: str | Path = "models/features.json",
                 threshold: float = 0.5) -> None:
        self.threshold = threshold
        self.booster = None
        self.features: list[str] = []
        mp, fp = Path(model_path), Path(features_path)
        if mp.exists() and fp.exists():
            try:
                import lightgbm as lgb

                self.booster = lgb.Booster(model_file=str(mp))
                self.features = json.loads(fp.read_text())
                log.info("loaded model with %d features", len(self.features))
            except Exception as exc:
                log.warning("could not load model: %s", exc)
                self.booster = None

    @property
    def available(self) -> bool:
        return self.booster is not None

    def score(self, f: dict[str, Any]) -> ScoreResult | None:
        if self.booster is None:
            return None
        import numpy as np

        row = np.array([[_num(f.get(c)) if _num(f.get(c)) is not None else np.nan for c in self.features]])
        p = float(self.booster.predict(row)[0])
        return ScoreResult(score=p, passed=p >= self.threshold, terms={"model_p": p}, source="model")


class CompositeScorer:
    """Rules always run; the model blends in only when it exists and has
    demonstrated lift. Both must agree for a trade at full size.

    Optional evidence sources (smart-money confluence, social attention) are
    additive *bonuses* rather than blended terms. They can raise conviction on a
    token the core already likes; they cannot rescue one it does not, because
    neither has been validated on this system's own data yet and an unvalidated
    signal should not be able to originate a trade.
    """

    def __init__(
        self,
        rule_threshold: float = 0.55,
        model_weight: float = 0.5,
        model_dir: str | Path = "models",
        require_both: bool = True,
        smartmoney: Any = None,
        social: Any = None,
        smartmoney_weight: float = 0.15,
        social_weight: float = 0.10,
    ) -> None:
        self.rules = RuleScorer(threshold=rule_threshold)
        self.model = ModelScorer(Path(model_dir) / "signal_lgbm.txt", Path(model_dir) / "features.json")
        self.model_weight = model_weight if self.model.available else 0.0
        self.require_both = require_both
        self.rule_threshold = rule_threshold
        self.smartmoney = smartmoney
        self.social = social
        self.smartmoney_weight = smartmoney_weight
        self.social_weight = social_weight

    def _bonuses(self, f: dict[str, Any], terms: dict[str, float], notes: list[str]) -> float:
        bonus = 0.0
        mint = f.get("mint")
        if self.smartmoney is not None and mint:
            try:
                c = self.smartmoney.confluence(str(mint))
            except Exception:
                c = None
            # Independent actors, not addresses: a bundle of 19 wallets run by
            # one operator is one opinion.
            if c and c.get("available") and c.get("n_clusters", 0) >= 2:
                v = self.smartmoney_weight * min(1.0, c["n_clusters"] / 4.0)
                terms["smart_money"] = v
                notes.append(f"{c['n_clusters']} independent credited actors holding")
                bonus += v
        if self.social is not None and mint:
            try:
                sf = self.social.features(str(mint), f.get("created_at"))
            except Exception:
                sf = None
            if sf is not None and getattr(sf, "available", False) and sf.score > 0:
                v = self.social_weight * sf.score
                terms["social"] = v
                bonus += v
        return bonus

    def score(self, f: dict[str, Any]) -> ScoreResult:
        r = self.rules.score(f)
        m = self.model.score(f) if self.model.available else None
        if m is None:
            base, source = r.score, "rules"
            core_pass = r.passed
        else:
            base = (1 - self.model_weight) * r.score + self.model_weight * m.score
            source = "rules+model"
            core_pass = (r.passed and m.passed) if self.require_both else (base >= self.rule_threshold)
            r.terms["model_p"] = m.score

        notes = list(r.notes)
        bonus = self._bonuses(f, r.terms, notes)
        total = max(0.0, min(1.0, base + bonus))
        return ScoreResult(
            score=total,
            passed=core_pass and total >= self.rule_threshold,
            terms=r.terms,
            notes=notes,
            source=source + ("+extra" if bonus > 0 else ""),
        )
