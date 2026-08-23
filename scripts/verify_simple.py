"""Stress the simple gate. If it survives this it becomes the default."""
import sys; sys.path.insert(0, "src")
import numpy as np, pandas as pd
from degen.safety.filters import SafetyConfig, check_local
from degen.sim.backtest import BacktestConfig, run
from degen.sim.costs import CostModel
from degen.store.lake import lake
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width", 240)

from degen.store.quality import load_clean
snaps = load_clean(report=True)
first = snaps.groupby("mint").observed_at.min().sort_values()

def num(f,k):
    v=f.get(k)
    try: v=float(v)
    except (TypeError,ValueError): return None
    return v if v==v else None

def gate(f):
    h,l,b = num(f,"holder_count"), num(f,"liquidity"), num(f,"buy_sell_ratio")
    if None in (h,l,b): return None
    return 1.0 if (h>=20 and l>=3000 and b>=0.55) else None

cfg = BacktestConfig(base_size_sol=0.5, costs=CostModel(venue="pumpfun"))

print("=== 1. Rolling out-of-sample: 5 sequential folds, each tested on the next slice ===")
print(f"{'fold':<6}{'train mints':>12}{'test n':>8}{'win%':>7}{'ROI%':>9}{'PF':>8}{'ex_top3':>10}{'P>0':>7}")
edges = np.linspace(0.35, 1.0, 6)
for i in range(len(edges)-1):
    a,b = int(len(first)*edges[i]), int(len(first)*edges[i+1])
    te = snaps[snaps.mint.isin(set(first.iloc[a:b].index))]
    t,s = run(te, gate, cfg)
    if s["n_trades"] < 5: print(f"{i+1:<6}{a:>12}{s['n_trades']:>8}   (too few)"); continue
    pnl=np.sort(t.pnl_sol.values)[::-1]
    print(f"{i+1:<6}{a:>12}{s['n_trades']:>8}{100*s['win_rate']:>7.1f}{100*s['roi']:>9.1f}"
          f"{s['profit_factor']:>8.2f}{pnl[3:].mean():>10.4f}{s['prob_expectancy_positive']:>7.2f}")

print("\n=== 2. Full-sample robustness: how much rides on the best trades ===")
t,s = run(snaps, gate, cfg)
pnl=np.sort(t.pnl_sol.values)[::-1]
print(f"trades={s['n_trades']} win={100*s['win_rate']:.1f}% ROI={100*s['roi']:.1f}% PF={s['profit_factor']:.2f} "
      f"med_x={s['median_x']:.3f} best={s['best_x']:.1f}x")
for k in (0,1,3,5,10):
    if len(pnl)>k:
        print(f"  drop top {k:>2}: total={pnl[k:].sum():+8.3f} SOL  mean={pnl[k:].mean():+.4f}  win%={100*(pnl[k:]>0).mean():.1f}")
print(f"  top trade = {100*pnl[0]/pnl.sum():.1f}% of total profit")
print(f"  top 5     = {100*pnl[:5].sum()/pnl.sum():.1f}% of total profit")

print("\n=== 3. Parameter sensitivity (is 20/3000/0.55 a knife edge?) ===")
print(f"{'holders':>8}{'liq':>7}{'bsr':>6}{'trades':>8}{'win%':>7}{'ROI%':>9}{'PF':>8}")
for h in (10,15,20,30,40):
    for l in (2000,3000,5000):
        for bs in (0.50,0.55,0.60):
            def g(f,h=h,l=l,bs=bs):
                a,b2,c = num(f,"holder_count"), num(f,"liquidity"), num(f,"buy_sell_ratio")
                if None in (a,b2,c): return None
                return 1.0 if (a>=h and b2>=l and c>=bs) else None
            t2,s2 = run(snaps, g, cfg)
            if s2["n_trades"]<10: continue
            print(f"{h:>8}{l:>7}{bs:>6}{s2['n_trades']:>8}{100*s2['win_rate']:>7.1f}{100*s2['roi']:>9.1f}{s2['profit_factor']:>8.2f}")
