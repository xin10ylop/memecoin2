"""What does the exitability guard remove, and were those profits ever real?"""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.sim.backtest import BacktestConfig, run
from degen.sim.costs import CostModel
from degen.safety.filters import SafetyConfig, check_local
from degen.store.quality import load_clean
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",240)
snaps=load_clean()
first=snaps.groupby("mint").observed_at.min().sort_values()
cut=int(len(first)*0.60)
te=snaps[snaps.mint.isin(set(first.iloc[cut:].index))]

def num(f,k):
    v=f.get(k)
    try: v=float(v)
    except (TypeError,ValueError): return None
    return v if v==v else None
def gate(f):
    h,l,b,m=num(f,"holder_count"),num(f,"liquidity"),num(f,"buy_sell_ratio"),num(f,"mcap")
    if None in (h,l,b,m): return False
    return h>=20 and l>=3000 and b>=0.55 and m>=5000

LOOSE=SafetyConfig(max_mcap_liquidity_ratio=1e9)   # guard off
TIGHT=SafetyConfig()                                # guard on
def mk(cfg):
    def g(f):
        if not check_local(f,cfg).ok: return None
        return 1.0 if gate(f) else None
    return g

cfg=BacktestConfig(base_size_sol=0.5,costs=CostModel(venue="pumpfun"))
res={}
for name,c in (("guard OFF",LOOSE),("guard ON",TIGHT)):
    t,s=run(te,mk(c),cfg); res[name]=(t,s)
    print(f"{name:11} trades={s['n_trades']:4d} win={100*s['win_rate']:5.1f}% ROI={100*s['roi']:7.1f}% "
          f"PF={s['profit_factor']:7.2f} total={t.pnl_sol.sum():+.3f} SOL deployed={t.sol_in.sum():.2f} SOL")

off,on = res["guard OFF"][0], res["guard ON"][0]
removed = off[~off.mint.isin(set(on.mint))]
print(f"\nthe guard removed {len(removed)} trades. What were they?")
if len(removed):
    print(f"  their reported PnL      : {removed.pnl_sol.sum():+.3f} SOL")
    print(f"  median position size    : {removed.sol_in.median():.4f} SOL  (vs {on.sol_in.median():.4f} for kept trades)")
    print(f"  median entry liquidity  : ${removed.entry_liq.median():,.0f}  (vs ${on.entry_liq.median():,.0f} kept)")
    tiny = removed[removed.sol_in < 0.05]
    print(f"  positions under 0.05 SOL: {len(tiny)} of {len(removed)}  ({100*len(tiny)/len(removed):.0f}%)")
    print(f"  research puts the minimum viable trade at ~0.5 SOL; below that fixed")
    print(f"  fees exceed 0.25% of notional and the trade cannot clear its own costs.")
    print("\n  largest 'winners' the guard removed:")
    print(removed.nlargest(6,"pnl_sol")[["symbol","sol_in","entry_liq","realized_x","pnl_sol"]].round(4).to_string(index=False))
