"""Try to break the simple gate's result before believing it."""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.sim.backtest import BacktestConfig, run
from degen.sim.costs import CostModel
from degen.store.lake import lake
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",240)
snaps=lake().df("snapshots"); snaps=snaps[snaps.price_usd.notna()&(snaps.price_usd>0)&snaps.age_s.notna()]
def num(f,k):
    v=f.get(k)
    try: v=float(v)
    except (TypeError,ValueError): return None
    return v if v==v else None
def gate(f):
    h,l,b=num(f,"holder_count"),num(f,"liquidity"),num(f,"buy_sell_ratio")
    if None in (h,l,b): return None
    return 1.0 if (h>=20 and l>=3000 and b>=0.55) else None

t,s=run(snaps,gate,BacktestConfig(base_size_sol=0.5,costs=CostModel(venue="pumpfun")))
print("=== top 8 trades: are they real, and could we have exited? ===")
cols=["symbol","entry_age","entry_liq","sol_in","sol_out","realized_x","peak_x","reason","hold_s","n_exits"]
print(t.sort_values("pnl_sol",ascending=False).head(8)[cols].round(3).to_string(index=False))
print(f"\nunique mints traded: {t.mint.nunique()} / trades {len(t)}  (duplicates would inflate)")
print(f"total deployed {t.sol_in.sum():.1f} SOL, returned {t.sol_out.sum():.1f} SOL")

print("\n=== does the result hold with far more pessimistic assumptions? ===")
print(f"{'scenario':<46}{'trades':>7}{'win%':>7}{'ROI%':>9}{'PF':>8}{'med_x':>8}")
scen=[
 ("baseline", dict()),
 ("latency 15s instead of 3s", dict(latency_s=15.0)),
 ("latency 30s", dict(latency_s=30.0)),
 ("exit only 5% of pool per slice", dict(max_pool_frac_sell=0.05)),
 ("exit only 2% of pool per slice", dict(max_pool_frac_sell=0.02)),
 ("size 2 SOL (5x bigger)", dict(base_size_sol=2.0)),
 ("aggressive fee tier", dict(costs=CostModel(venue="pumpfun",tier="aggressive"))),
 ("+1% platform fee each way", dict(costs=CostModel(venue="pumpfun",platform_fee=0.01))),
 ("+3% MEV loss each way", dict(costs=CostModel(venue="pumpfun",mev_loss=0.03))),
 ("30% of buys never land", dict(costs=CostModel(venue="pumpfun",fail_rate=0.30))),
 ("everything hostile at once", dict(latency_s=15.0, max_pool_frac_sell=0.05,
        costs=CostModel(venue="pumpfun",tier="aggressive",platform_fee=0.01,mev_loss=0.03,fail_rate=0.30))),
]
for name,kw in scen:
    base=dict(base_size_sol=0.5,costs=CostModel(venue="pumpfun"))
    base.update(kw)
    t2,s2=run(snaps,gate,BacktestConfig(**base))
    if s2["n_trades"]<5: print(f"{name:<46}{s2['n_trades']:>7}  (too few)"); continue
    print(f"{name:<46}{s2['n_trades']:>7}{100*s2['win_rate']:>7.1f}{100*s2['roi']:>9.1f}{s2['profit_factor']:>8.2f}{s2['median_x']:>8.3f}")
