"""Would the safety filter even let us buy a thin-float token?

The archetype is defined by extreme concentration - a large valuation held by
almost nobody - which is exactly the shape the safety stack exists to reject.
If the filter blocks them all, the archetype is unreachable and there is nothing
to build.
"""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.features.build import features_at
from degen.safety.filters import SafetyConfig, check_local, check_rugcheck
from degen.store.quality import load_clean
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",220)

snaps = load_clean(); cfg = SafetyConfig()
rows=[]
for mint,h in snaps.groupby("mint", sort=False):
    h=h.sort_values("age_s")
    p=pd.to_numeric(h["price_usd"],errors="coerce").to_numpy(float)
    if len(p)<4 or not np.isfinite(p[0]) or p[0]<=0: continue
    peak=np.nanmax(p)/p[0]
    if not np.isfinite(peak) or peak>200: continue
    f=features_at(h,180)
    if not f or f.get("mcap") is None or f.get("holder_count") is None: continue
    thin = float(f["mcap"])>=100_000 and float(f["holder_count"])<20
    if not thin: continue
    r=check_local(f,cfg)
    rows.append({"mint":mint,"sym":f.get("symbol"),"peak":peak,"local_ok":r.ok,
                 "why":"; ".join(r.rejects[:2]),"holders":f["holder_count"],
                 "liq":f.get("liquidity"),"mcap":f["mcap"]})
d=pd.DataFrame(rows)
print(f"thin-float candidates: {len(d)}   5x rate: {100*(d.peak>=5).mean():.1f}%")
print(f"pass the local safety filter: {int(d.local_ok.sum())} / {len(d)}\n")
if len(d):
    print(d.sort_values("peak",ascending=False).head(12)[
        ["sym","peak","local_ok","holders","liq","mcap","why"]].round(1).to_string(index=False))
    print("\nreject reasons among thin-float candidates:")
    from collections import Counter
    c=Counter()
    for w in d[~d.local_ok].why:
        for part in w.split("; "):
            if part: c[part.split()[0]+" "+part.split()[1] if len(part.split())>1 else part]+=1
    for k,v in c.most_common(6): print(f"  {k:26} {v}")
    print(f"\n5x rate among those that DO pass safety: ", end="")
    ps=d[d.local_ok]
    print(f"{100*(ps.peak>=5).mean():.1f}% on n={len(ps)}" if len(ps) else "n/a")
