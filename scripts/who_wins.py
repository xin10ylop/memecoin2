"""Who actually gets the 5x, 10x, 50x - and would the gate have been there?

Answers the recall question the ROI figures hide. The gate can look good on what
it buys while missing most of what mattered, and it does.

Last run, on 3,937 tokens:

  RECALL - of the big winners, how many the gate would have bought
      2x    132 of 318   41.5%
      5x     38 of  82   46.3%
     10x     14 of  36   38.9%
     20x      2 of  13   15.4%     <- worst exactly where it matters most

  PRECISION - of what the gate buys, what it reaches
      2x   47.1% (vs 8.1% base, 5.8x lift)
      5x   13.6% (vs 2.1% base, 6.5x lift)

  PROFILE at 3 minutes, median
      5x+ winners    101 holders   $6,335 liq   $30,762 mcap   buy/sell 0.60
      went nowhere     2 holders   $2,867 liq    $2,706 mcap   buy/sell 0.50

  CONCENTRATION
      top 5 tokens of 3,937 (0.13%) = 24% of all upside
      top 50           (1.27%)      = 55% of all upside

The gate is good at avoiding losers and mediocre at catching the very biggest
winners, which is the expected trade for a rule that waits for evidence: by the
time evidence exists, part of the move has happened.
"""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.features.build import features_at
from degen.signals.gate import TractionGate
from degen.store.quality import load_clean
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",240)

snaps = load_clean()
gate = TractionGate()
rows=[]
for mint, h in snaps.groupby("mint", sort=False):
    h = h.sort_values("age_s")
    p = pd.to_numeric(h["price_usd"], errors="coerce").to_numpy(float)
    if len(p) < 4 or not np.isfinite(p[0]) or p[0] <= 0: continue
    peak = np.nanmax(p) / p[0]
    if not np.isfinite(peak) or peak > 200: continue
    f = features_at(h, 180)
    passed = bool(f and gate.check(f).passed)
    rows.append({"mint": mint, "sym": h["symbol"].iloc[0], "peak": peak, "passed": passed,
                 "holders180": (f or {}).get("holder_count"), "liq180": (f or {}).get("liquidity"),
                 "mcap180": (f or {}).get("mcap"), "bsr180": (f or {}).get("buy_sell_ratio"),
                 "devmints": (f or {}).get("dev_mints"),
                 "age_first": h["age_s"].iloc[0], "n": len(h)})
d = pd.DataFrame(rows)
print(f"tokens analysed: {len(d)}\n")

print("=== RECALL: of the big winners, how many would the gate have bought? ===")
print(f"{'threshold':>10}{'winners':>9}{'gate caught':>13}{'recall':>9}")
for m in (2,3,5,10,20,50):
    w = d[d.peak>=m]
    if len(w)==0: continue
    print(f"{m:>9}x{len(w):>9}{int(w.passed.sum()):>13}{100*w.passed.mean():>8.1f}%")

print("\n=== PRECISION: of what the gate buys, how much reaches each level? ===")
g = d[d.passed]
print(f"gate buys {len(g)} of {len(d)} tokens ({100*len(g)/len(d):.1f}%)")
print(f"{'threshold':>10}{'of gate buys':>14}{'of all tokens':>15}{'lift':>7}")
for m in (2,3,5,10,20,50):
    a, b = (g.peak>=m).mean(), (d.peak>=m).mean()
    if b == 0: continue
    print(f"{m:>9}x{100*a:>13.2f}%{100*b:>14.2f}%{a/b:>7.1f}x")

print("\n=== WHAT THE 5x+ WINNERS LOOKED LIKE AT 3 MINUTES ===")
w = d[(d.peak>=5) & d.holders180.notna()]
l = d[(d.peak<1.2) & d.holders180.notna()]
for label, s in (("5x+ winners", w), ("went nowhere", l)):
    if len(s)<5: continue
    print(f"{label:16} n={len(s):4d}  holders={s.holders180.median():6.0f}  "
          f"liq=${s.liq180.median():>9,.0f}  mcap=${s.mcap180.median():>9,.0f}  "
          f"buy/sell={s.bsr180.median():.2f}  devmints={s.devmints.median():.0f}")

print("\n=== the biggest movers, and whether we'd have been in ===")
top = d.nlargest(12,"peak")[["sym","peak","passed","holders180","liq180","mcap180","bsr180"]]
print(top.round(2).to_string(index=False))

print("\n=== WHERE THE MONEY IS: how concentrated are the returns? ===")
d2 = d.sort_values("peak", ascending=False)
tot = (d2.peak - 1).clip(lower=0).sum()
for k in (1, 5, 10, 25, 50):
    share = (d2.peak.head(k) - 1).clip(lower=0).sum() / tot
    print(f"  top {k:>3} tokens ({100*k/len(d2):.2f}% of launches) = {100*share:5.1f}% of all upside")
