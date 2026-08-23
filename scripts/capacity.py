"""How much money can this strategy physically absorb?

The compounding simulation produced $288M from $1,000, which is the model
telling you it has no capacity constraint in it. Real pools do. This computes
the ceiling from the actual depth of the tokens the gate buys.
"""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.features.build import features_at
from degen.safety.filters import SafetyConfig, check_local
from degen.signals.gate import TractionGate
from degen.sim.amm import Pool, max_size_for_impact
from degen.store.quality import load_clean
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",210)
SOL=94.0
snaps=load_clean(); gate=TractionGate(); saf=SafetyConfig()
liqs=[]
for mint,h in snaps.groupby("mint",sort=False):
    f=features_at(h.sort_values("age_s"),180)
    if not f: continue
    if not check_local(f,saf).ok: continue
    if not gate.check(f).passed: continue
    l=f.get("liquidity")
    if l: liqs.append(float(l))
liqs=np.array(liqs)
print(f"tokens the gate actually buys: {len(liqs)}")
print(f"their liquidity: p25 ${np.percentile(liqs,25):,.0f}  median ${np.median(liqs):,.0f}  "
      f"p75 ${np.percentile(liqs,75):,.0f}  p95 ${np.percentile(liqs,95):,.0f}\n")

print("=== max position per trade, by how much slippage you will tolerate ===")
print(f"{'slippage':>10}{'on median pool':>17}{'on p25 pool':>14}{'on p75 pool':>14}")
for s in (0.01,0.02,0.05,0.10,0.20):
    row=[]
    for pct in (50,25,75):
        L=np.percentile(liqs,pct)
        side_sol=(L/2)/SOL
        row.append(side_sol*s/(1-s)*SOL)
    print(f"{100*s:>9.0f}%{row[0]:>16,.0f}{row[1]:>14,.0f}{row[2]:>14,.0f}")

print("\n=== therefore the whole strategy's working-capital ceiling ===")
SLOTS=6
for s,label in ((0.02,"disciplined"),(0.05,"loose"),(0.10,"reckless")):
    per=np.median(liqs)/2/SOL*s/(1-s)*SOL
    print(f"  {label:12} {100*s:>3.0f}% slippage -> ${per:>6,.0f}/trade x {SLOTS} slots = "
          f"${per*SLOTS:>7,.0f} deployable at any moment")
print("\n  Beyond that, extra bankroll simply sits idle. You cannot put $100,000")
print("  into a pool holding $5,000 - you would be buying your own price up and")
print("  then selling into nothing.")

print("\n=== what that caps daily profit at, if the edge is real ===")
TURNS=24*60/30.4   # ~47 position turnovers per slot per day
print(f"{'slippage':>10}{'deployed':>11}{'turns/day':>11}{'daily turnover':>16}"
      f"{'@2% edge':>10}{'@5% edge':>10}{'@12% edge':>11}")
for s in (0.02,0.05,0.10):
    per=np.median(liqs)/2/SOL*s/(1-s)*SOL
    dep=per*SLOTS; turn=dep*TURNS
    print(f"{100*s:>9.0f}%{dep:>11,.0f}{TURNS:>11.0f}{turn:>16,.0f}"
          f"{0.02*turn:>10,.0f}{0.05*turn:>10,.0f}{0.12*turn:>11,.0f}")
print("\n  Note the edge shrinks as slippage rises - taking 10% slippage to deploy")
print("  more capital destroys the very edge you are trying to scale.")
