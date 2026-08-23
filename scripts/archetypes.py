"""Are there winner archetypes the single gate structurally cannot see?

Yes - at least one, and it is worth chasing.

Why the gate misses 5x+ winners (37 of 75 last run):
    blocked by buy/sell < 0.55    70% of misses   <- the most expensive filter
    blocked by holders  < 20      57%
    blocked by liquidity < $3k    57%
    blocked by mcap     < $5k      8%

And a second archetype the gate is structurally blind to, because it requires
holders >= 20 while this one is defined by having almost none:

    segment                          n     5x rate   lift   med peak
    all tokens                    3816       1.97%    1.0x      1.00
    gate passes (current rule)     280      13.57%    6.9x      1.89
    mcap >= $100k AND holders < 20  30      53.33%   27.1x      8.91

A large valuation with nobody in it. Two of the three biggest movers in the
whole census looked like this - roughly 12 holders, $50k liquidity, $1.1M
market cap - and the gate could never have bought either.

TREAT AS A LEAD, NOT A FINDING. n=30, in-sample, and it is not monotone:
tightening to mcap >= $250k drops the rate to 25% on n=8, which is what noise
looks like. It needs the same held-out test as everything else, and 30 examples
cannot support one. Re-run this as the census grows.
"""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.features.build import features_at
from degen.signals.gate import TractionGate
from degen.store.quality import load_clean
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",240)

snaps = load_clean(); gate = TractionGate()
rows=[]
for mint, h in snaps.groupby("mint", sort=False):
    h = h.sort_values("age_s")
    p = pd.to_numeric(h["price_usd"], errors="coerce").to_numpy(float)
    if len(p) < 4 or not np.isfinite(p[0]) or p[0] <= 0: continue
    peak = np.nanmax(p)/p[0]
    if not np.isfinite(peak) or peak > 200: continue
    f = features_at(h, 180)
    if not f or f.get("holder_count") is None or f.get("mcap") is None: continue
    rows.append({"peak":peak,"passed":gate.check(f).passed,
                 "holders":float(f["holder_count"]),"liq":float(f.get("liquidity") or 0),
                 "mcap":float(f["mcap"]),"bsr":float(f.get("buy_sell_ratio") or 0),
                 "trades":float(f.get("trades_total") or 0)})
d=pd.DataFrame(rows); d["w5"]=(d.peak>=5).astype(int)
print(f"n={len(d)}  base 5x rate={100*d.w5.mean():.2f}%\n")

print("=== the misses: why did the gate reject each 5x+ winner? ===")
miss = d[(d.peak>=5) & ~d.passed]
reasons={"holders<20":0,"liq<3000":0,"bsr<0.55":0,"mcap<5000":0}
for _,r in miss.iterrows():
    if r.holders<20: reasons["holders<20"]+=1
    if r.liq<3000: reasons["liq<3000"]+=1
    if r.bsr<0.55: reasons["bsr<0.55"]+=1
    if r.mcap<5000: reasons["mcap<5000"]+=1
print(f"missed {len(miss)} winners of {int((d.peak>=5).sum())}")
for k,v in sorted(reasons.items(), key=lambda kv:-kv[1]):
    print(f"  blocked by {k:14} {v:3d}  ({100*v/max(1,len(miss)):.0f}% of misses)")

print("\n=== ARCHETYPE 2: high market cap but few holders ===")
print("(the 119x and 117x tokens looked like this: ~12 holders, $50k liquidity, $1.1M mcap)")
alt = (d.mcap>=100_000) & (d.holders<20)
print(f"{'segment':<44}{'n':>6}{'5x rate':>9}{'lift':>7}{'med peak':>10}")
base=d.w5.mean()
for label,m in [
    ("all tokens", pd.Series(True,index=d.index)),
    ("gate passes (current rule)", d.passed),
    ("mcap>=100k AND holders<20", alt),
    ("mcap>=250k AND holders<20", (d.mcap>=250_000)&(d.holders<20)),
    ("mcap>=100k (any holder count)", d.mcap>=100_000),
    ("holders>=100 (any liquidity)", d.holders>=100),
    ("holders>=100 AND bsr>=0.45", (d.holders>=100)&(d.bsr>=0.45)),
    ("gate OR (mcap>=100k & holders>=5)", d.passed | ((d.mcap>=100_000)&(d.holders>=5))),
    ("gate OR holders>=100", d.passed | (d.holders>=100)),
]:
    s=d[m.fillna(False)]
    if len(s)<8: print(f"{label:<44}{len(s):>6}   (too few)"); continue
    print(f"{label:<44}{len(s):>6}{100*s.w5.mean():>8.2f}%{s.w5.mean()/base:>7.1f}x{s.peak.median():>10.2f}")
