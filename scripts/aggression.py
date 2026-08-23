"""What different aggression levels do - and a model that breaks, on purpose.

The default sizes at 2% of bankroll across 6 slots. That is a choice about
variance, not a law, and it is why the expected daily figure looks small. This
sweeps the aggression range against the observed out-of-sample distribution.

READ THE OUTPUT SCEPTICALLY. At higher fractions it reports absurd terminal
wealth - a median of $288 million from $1,000 - and that number is the model
announcing its own missing constraint. Two things are wrong with it:

  1. It compounds a per-trade mean estimated from 81 trades, in which a handful
     of winners produced all the profit, some 720 times. Resampling with
     replacement all but guarantees repeatedly drawing those same winners.
  2. It has no capacity limit. Real pools do. `capacity.py` computes the actual
     ceiling from the depth of the tokens the gate buys, and it is roughly
     $550-3,000 of deployable capital depending on tolerated slippage. Past
     that, extra bankroll cannot be put to work at all.

Use the low-fraction rows and the ruin columns, which are informative. Treat the
terminal-wealth column at 20%+ as a demonstration of why an unconstrained
compounding model is worthless for sizing.
"""
import sys; sys.path.insert(0,"src")
import numpy as np, pandas as pd
from degen.safety.filters import SafetyConfig, check_local
from degen.signals.gate import TractionGate
from degen.sim.backtest import BacktestConfig, run
from degen.sim.costs import CostModel
from degen.store.quality import load_clean
from degen.util.log import setup
setup("WARNING"); pd.set_option("display.width",210)

snaps = load_clean()
first = snaps.groupby("mint").observed_at.min().sort_values()
te = snaps[snaps.mint.isin(set(first.iloc[int(len(first)*0.60):].index))]
gate, saf = TractionGate(), SafetyConfig()
def sig(f):
    if not check_local(f, saf).ok: return None
    return gate.signal(f)
t, s = run(te, sig, BacktestConfig(base_size_sol=0.5, costs=CostModel(venue="pumpfun")))
R = t.realized_x.values                      # multiple returned per unit staked
print(f"held-out trades {len(R)}   win {100*(R>1).mean():.0f}%   "
      f"mean {R.mean():.3f}x   median {np.median(R):.3f}x   worst {R.min():.3f}x   best {R.max():.1f}x\n")

# --- empirical Kelly: the fraction maximising log growth on THIS distribution ---
fracs = np.linspace(0.005, 0.60, 120)
growth = [np.mean(np.log1p(f*(R-1.0))) for f in fracs]
kelly = fracs[int(np.argmax(growth))]
print(f"empirical Kelly fraction (max log-growth on this sample): {100*kelly:.1f}% per trade")
print(f"the shipped default is 2% x conviction ~= 1.2%, i.e. {kelly/0.012:.0f}x more conservative\n")

rng = np.random.default_rng(7)
# Effective INDEPENDENT bets per day, not raw trade count. A day's 284 trades
# are all long the same factor - whether the memecoin market is hot - so they
# compound like a handful of bets, not hundreds. Assuming 284 is the same error
# that produced a fictional +45%/day earlier.
EFFECTIVE_BETS_PER_DAY = 8
SIMS = 6_000
HORIZON = 90 * EFFECTIVE_BETS_PER_DAY         # 90 days

print(f"modelling {EFFECTIVE_BETS_PER_DAY} effective independent bets/day over 90 days "
      f"= {HORIZON} compounding events\n")
print("=== the actual menu: 90 days, $1,000 start ===")
print(f"{'sizing':<26}{'median end':>12}{'P(2x)':>8}{'P(5x)':>8}{'P(10x)':>8}"
      f"{'P(<$200)':>10}{'P(<$50)':>9}{'worst 5%':>10}")
for label, f in [("1.2% (shipped default)",0.012), ("3%",0.03), ("5%",0.05),
                 ("10%",0.10), ("20%",0.20), (f"{100*kelly:.0f}% (full Kelly)",kelly),
                 ("35%",0.35), ("50%",0.50)]:
    draws = rng.choice(R, size=(SIMS, HORIZON), replace=True)
    logret = np.log1p(f*(draws-1.0))
    # bankruptcy is absorbing: once below $50 you cannot size a trade at all
    paths = 1000.0*np.exp(np.cumsum(logret, axis=1))
    lowest = paths.min(axis=1)
    end = np.where(lowest < 50, lowest, paths[:,-1])
    print(f"{label:<26}{np.median(end):>12,.0f}{100*(end>=2000).mean():>7.1f}%"
          f"{100*(end>=5000).mean():>7.1f}%{100*(end>=10000).mean():>7.1f}%"
          f"{100*(lowest<200).mean():>9.1f}%{100*(lowest<50).mean():>8.1f}%"
          f"{np.percentile(end,5):>10,.0f}")

print("\n=== and if the true edge is HALF what we measured (the likely case) ===")
Rh = 1.0 + (R-1.0)*0.5
print(f"{'sizing':<26}{'median end':>12}{'P(10x)':>9}{'P(<$50)':>10}")
for label, f in [("1.2% (default)",0.012), ("10%",0.10), ("20%",0.20), ("35%",0.35)]:
    draws = rng.choice(Rh, size=(4000, HORIZON), replace=True)
    paths = 1000.0*np.exp(np.cumsum(np.log1p(f*(draws-1.0)), axis=1))
    lowest = paths.min(axis=1); end = np.where(lowest<50, lowest, paths[:,-1])
    print(f"{label:<26}{np.median(end):>12,.0f}{100*(end>=10000).mean():>8.1f}%{100*(lowest<50).mean():>9.1f}%")
