"""Does the market-cap-to-liquidity band add out-of-sample edge to the gate?"""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.sim.backtest import BacktestConfig, run
from degen.sim.costs import CostModel
from degen.safety.filters import SafetyConfig, check_local
from degen.store.quality import load_clean
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",240)

snaps = load_clean()
first = snaps.groupby("mint").observed_at.min().sort_values()
cut = int(len(first)*0.60)
tr = snaps[snaps.mint.isin(set(first.iloc[:cut].index))]
te = snaps[snaps.mint.isin(set(first.iloc[cut:].index))]
print(f"train {cut} mints / test {len(first)-cut} mints\n")

def num(f,k):
    v=f.get(k)
    try: v=float(v)
    except (TypeError,ValueError): return None
    return v if v==v else None

saf = SafetyConfig()

def base_gate(f):
    h,l,b,m = num(f,"holder_count"),num(f,"liquidity"),num(f,"buy_sell_ratio"),num(f,"mcap")
    if None in (h,l,b,m): return False
    return h>=20 and l>=3000 and b>=0.55 and m>=5000

def mk(ratio_lo=None, ratio_hi=None, use_gate=True, safety=True):
    def g(f):
        if safety and not check_local(f,saf).ok: return None
        if use_gate and not base_gate(f): return None
        if ratio_lo is not None or ratio_hi is not None:
            r = num(f,"mcap_liq_ratio")
            if r is None: return None
            if ratio_lo is not None and r < ratio_lo: return None
            if ratio_hi is not None and r > ratio_hi: return None
        return 1.0
    return g

cfg = BacktestConfig(base_size_sol=0.5, costs=CostModel(venue="pumpfun"))
rows=[]
for name, sig in [
    ("gate (current default)",            mk()),
    ("gate + ratio >= 2",                 mk(ratio_lo=2)),
    ("gate + ratio >= 3",                 mk(ratio_lo=3)),
    ("gate + ratio 2-35",                 mk(ratio_lo=2, ratio_hi=35)),
    ("gate + ratio 3-20",                 mk(ratio_lo=3, ratio_hi=20)),
    ("ratio 3-20 only (no gate)",         mk(ratio_lo=3, ratio_hi=20, use_gate=False)),
    ("ratio >50 (the untradeable band)",  mk(ratio_lo=50, use_gate=False, safety=False)),
]:
    out={"rule":name}
    for tag,df in (("IS",tr),("OOS",te)):
        t,s = run(df, sig, cfg)
        if s["n_trades"]<5:
            out[f"{tag}_n"]=s["n_trades"]; continue
        out[f"{tag}_n"]=s["n_trades"]
        out[f"{tag}_win%"]=round(100*s["win_rate"],1)
        out[f"{tag}_ROI%"]=round(100*s["roi"],1)
        out[f"{tag}_PF"]=round(s["profit_factor"],2)
        out[f"{tag}_P>0"]=round(s["prob_expectancy_positive"],2)
    rows.append(out); print("  done",name,flush=True)
print()
print(pd.DataFrame(rows).to_string(index=False))
