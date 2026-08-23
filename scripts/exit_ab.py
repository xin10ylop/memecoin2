"""A/B the exit policy.

The exit research argues the ladder destroys the tail: on a distribution whose
local Pareto exponent falls below 1 above ~5x, selling into strength is provably
value-destroying, and the marginal sell/hold rule says sell below 2x and hold
above it. It also argues the trailing stop is the highest-leverage parameter,
that ours is roughly twice too wide, and that it is scaled backwards - tightening
as the multiple rises when realised volatility is rising.

All of that is a hypothesis about our data, so it gets tested on our data.
"""
import sys; sys.path.insert(0, "src")
import numpy as np, pandas as pd
from degen.risk.exit import ExitConfig
from degen.safety.filters import SafetyConfig, check_local
from degen.signals.score import CompositeScorer
from degen.sim.backtest import BacktestConfig, run
from degen.sim.costs import CostModel
from degen.store.lake import lake
from degen.util.log import setup

setup("WARNING")
pd.set_option("display.width", 230)
snaps = lake().df("snapshots")
snaps = snaps[snaps.price_usd.notna() & (snaps.price_usd > 0) & snaps.age_s.notna()]
sc = CompositeScorer(rule_threshold=0.55); saf = SafetyConfig()
def sig(f):
    if not check_local(f, saf).ok: return None
    r = sc.score(f)
    return float(np.clip(r.score, 0.3, 1.0)) if r.passed else None

CURRENT = ExitConfig()
HOLD_TAIL = ExitConfig(
    ladder=((1.6, 0.40), (2.2, 0.30)),                      # de-risk to 2x, then stop selling
    trail_schedule=((1.5, 0.25), (3.0, 0.30), (6.0, 0.35), (15.0, 0.42)),  # widens with the run
)
TIGHT_TRAIL_ONLY = ExitConfig(
    ladder=((1.6, 0.40),),
    trail_schedule=((1.2, 0.22), (3.0, 0.28), (8.0, 0.34), (20.0, 0.40)),
)
PURE_TRAIL = ExitConfig(
    ladder=(),
    trail_schedule=((1.15, 0.25), (3.0, 0.30), (8.0, 0.36), (20.0, 0.42)),
)
NARROW_CURRENT = ExitConfig(
    ladder=ExitConfig().ladder,
    trail_schedule=((1.5, 0.25), (3.0, 0.22), (6.0, 0.20), (15.0, 0.18)),   # current shape, tighter
)

rows = []
for name, ex in [("current (ladder, wide trail)", CURRENT),
                 ("narrower current trail", NARROW_CURRENT),
                 ("hold-the-tail (sell to 2x)", HOLD_TAIL),
                 ("one rung + tight widening trail", TIGHT_TRAIL_ONLY),
                 ("pure trail, no ladder", PURE_TRAIL)]:
    t, s = run(snaps, sig, BacktestConfig(base_size_sol=0.5, exit=ex, costs=CostModel(venue="pumpfun")))
    if s["n_trades"] < 5:
        rows.append({"exit policy": name, "trades": s["n_trades"]}); continue
    pnl = np.sort(t.pnl_sol.values)[::-1]
    x = t.realized_x.values
    rows.append({
        "exit policy": name, "trades": s["n_trades"], "win%": round(100*s["win_rate"],1),
        "ROI%": round(100*s["roi"],1), "exp_x": round(s["expectancy_x"],3),
        "med_x": round(s["median_x"],3), "PF": round(s["profit_factor"],2),
        "best_x": round(s["best_x"],1),
        "skew": round(float(pd.Series(x).skew()),2),
        "ex_top3": round(pnl[3:].mean(),4) if len(pnl)>3 else None,
        "P(exp>0)": round(s["prob_expectancy_positive"],3),
    })
    print(f"  done {name}", flush=True)
print()
print(pd.DataFrame(rows).to_string(index=False))
