# Seed census

The dataset every measurement in this repository was computed from, preserved so
the results are reproducible and so a fresh install does not start from zero.

| | |
|---|---|
| mints | 4,082 |
| snapshots | 187,682 |
| collected | 2026-08-23, 11:42–21:27 UTC |
| **actual uptime** | **2.9 h of a 9.7 h window (30%)** |
| cleaning | price/liquidity divergence guard applied |

## Read the uptime line before trusting anything derived from this

Two gaps totalling 6.9 hours split this into three bursts, because the collector
was running inside a session container that suspends when the session goes idle.
So this is not "ten hours of market data" — it is **under three hours, from a
single afternoon, in a single market regime**, and the published pump.fun
graduation rate varies by a factor of thirty across periods.

That is the entire reason `deploy/` exists. Every headline figure in this
repository is limited by this file, not by the modelling.

## Use

```python
from degen.store.quality import load_seed
df = load_seed()          # the frame every analysis script consumes
```

`load_clean()` reads the live lake and falls back to this seed when the lake is
empty, so analysis works on a fresh clone before any collection has happened.

## What it is good for, and not

**Good for:** reproducing the reported numbers; developing and testing new rules;
sanity-checking that a change did not break the pipeline.

**Not good for:** deciding whether to trade real money. Three hours in one regime
cannot answer that, and a rule tuned against this file and no other data is
fitted to one afternoon.
