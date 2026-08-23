import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.store.lake import lake
from degen.sim.backtest import run, BacktestConfig
from degen.sim.costs import CostModel
from degen.signals.score import CompositeScorer
from degen.safety.filters import check_local, SafetyConfig
from degen.util.log import setup
setup("WARNING")
snaps=lake().df("snapshots"); snaps=snaps[snaps.price_usd.notna()&(snaps.price_usd>0)&snaps.age_s.notna()]
sc=CompositeScorer(rule_threshold=0.55); saf=SafetyConfig()
def sig(f):
    if not check_local(f,saf).ok: return None
    r=sc.score(f); return float(np.clip(r.score,0.3,1.0)) if r.passed else None
t,s=run(snaps,sig,BacktestConfig(base_size_sol=0.5,costs=CostModel(venue="pumpfun")))
pnl=np.sort(t.pnl_sol.values)[::-1]
print(f"trades={s['n_trades']} win={100*s['win_rate']:.1f}% ROI={100*s['roi']:.1f}% "
      f"exp={s['expectancy_x']:.3f}x med={s['median_x']:.3f}x PF={s['profit_factor']:.2f} best={s['best_x']:.1f}x")
print(f"P(exp>0)={s['prob_expectancy_positive']:.3f} CI95={[round(v,4) for v in s['expectancy_ci95']]}")
for k in range(4): print(f"  drop top {k}: total={pnl[k:].sum():+.3f} SOL mean={pnl[k:].mean():+.4f} win%={100*(pnl[k:]>0).mean():.1f}")
print("exits:",s['exit_reasons'])
