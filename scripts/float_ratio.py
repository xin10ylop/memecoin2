"""Is market-cap-to-liquidity a usable exitability guard, and where is the line?"""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.features.build import features_at
from degen.store.quality import load_clean
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",210)
snaps=load_clean()
rows=[]
for mint,h in snaps.groupby("mint",sort=False):
    h=h.sort_values("age_s")
    p=pd.to_numeric(h["price_usd"],errors="coerce").to_numpy(float)
    if len(p)<4 or not np.isfinite(p[0]) or p[0]<=0: continue
    peak=np.nanmax(p)/p[0]
    if not np.isfinite(peak) or peak>200: continue
    f=features_at(h,180)
    if not f: continue
    m,l,hold=f.get("mcap"),f.get("liquidity"),f.get("holder_count")
    if not m or not l or l<=0: continue
    rows.append({"peak":peak,"ratio":float(m)/float(l),"mcap":float(m),
                 "liq":float(l),"holders":float(hold or 0)})
d=pd.DataFrame(rows)
print(f"n={len(d)}\n")
print("market cap / liquidity ratio, by outcome:")
for lbl,s in [("all",d),("peak>=2",d[d.peak>=2]),("peak>=5",d[d.peak>=5]),("went nowhere",d[d.peak<1.2])]:
    if len(s)<10: continue
    q=s.ratio.quantile([.25,.5,.75,.95])
    print(f"  {lbl:14} n={len(s):5d}  p25={q[.25]:7.1f}  median={q[.5]:7.1f}  p75={q[.75]:7.1f}  p95={q[.95]:8.1f}")
print("\noutcome by ratio band (a high ratio means a big paper valuation on a thin pool):")
d["band"]=pd.cut(d.ratio,[0,5,10,20,50,1e9],labels=["<5","5-10","10-20","20-50",">50"])
t=d.groupby("band",observed=True).agg(n=("peak","size"),pct2x=("peak",lambda x:(x>=2).mean()),
                                      pct5x=("peak",lambda x:(x>=5).mean()),med=("peak","median"),
                                      med_liq=("liq","median"))
print((t*[1,100,100,1,1]).round(2).to_string())
print("\nthe >50 band is where the thin-float 'winners' live. Their liquidity:")
hi=d[d.ratio>50]
print(f"  n={len(hi)}  median liquidity ${hi.liq.median():,.0f}  median mcap ${hi.mcap.median():,.0f}  median holders {hi.holders.median():.0f}")
print(f"  a 0.5 SOL (~$47) exit is {100*47/hi.liq.median():.1f}% of the median pool")
