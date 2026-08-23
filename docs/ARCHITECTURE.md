# Architecture

## The organising constraint

One implementation per concept, shared by research and production. The
backtester and the live trader call the *same* `features_at`, the same
`SafetyConfig`, the same `CompositeScorer`, the same `evaluate` exit function
and the same AMM fill maths. A system where paper mode and live mode take
different code paths produces paper results that mean nothing, so the paths are
not allowed to differ.

## Data flow

```
                 ┌──────────────────────────────────────────┐
   Jupiter ──┐   │ Collector                                │
 GeckoTerm ──┼──▶│  discovery loop   every 12s              │──▶ discoveries
 DexScreen ──┘   │  tracking loop    age-tiered 20s → 2h    │──▶ snapshots
                 │  due-time priority queue, budget-capped  │
                 └──────────────────────────────────────────┘
                                    │
                                    ▼
                        features_at(hist, age)          ← only age ≤ T
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
              build_panel()                   live consider()
              + label_forward()                     │
                    │                               │
                    ▼                               ▼
              model/train.py                  safety → score → size
              purged walk-forward                   │
                    │                               ▼
                    └──────▶ models/ ──────▶   broker.buy()
                                                    │
                                              exit.evaluate()
                                                    │
                                              broker.sell() ──▶ trades
```

## Why the collector is shaped the way it is

**Two loops, one process.** Discovery must run at a fixed fast cadence to catch
launches within seconds. Tracking must scale to thousands of tokens. Separating
them lets each have its own rhythm without one starving the other.

**Age-tiered cadence.** A token gets polled every 20s while under 5 minutes old,
every 60s to 30 minutes, then progressively less. All the decision-relevant
information is in the first few minutes; a day-old token needs sampling 30x less
often. This is what makes tracking a thousand tokens on a free API possible.

**A due-time priority queue rather than a schedule.** Each cycle serves the most
overdue mints up to the request budget. When discovery outruns the API
allowance, the system degrades by sampling old tokens less often instead of
falling over.

**Early retirement.** A token with under $400 of liquidity and fewer than six
trades at 45 minutes old is never coming back; retiring it frees budget for
tokens that might matter.

## Why JSONL and not a database

The collector must survive an ungraceful kill at any instant without corrupting
history, must be appendable from several processes, and must tolerate upstream
payload schemas drifting without notice. Line-delimited JSON gives all three for
free, DuckDB reads it directly with `union_by_name`, and closed days compact to
Parquet. A torn final line from a killed process is skipped rather than fatal.

## The leakage discipline

The obvious way to build this dataset — "use the first snapshot of each token" —
leaks, and it leaks in a way that is invisible in the metrics. How much has
happened by the first snapshot depends on how long the collector took to notice
the token, and tokens that get noticed late are disproportionately the ones that
were already busy. The model learns "was discovered late", which is not
available at decision time.

The fix is to fix the decision *age*: features at exactly 60s use only
observations at or before 60s, whatever the collector's timing was. The staleness
that remains is exposed to the model as `obs_lag`, because the live bot has the
same limitation and should be allowed to know about it.

`tests/test_features_no_leak.py` rewrites the entire future of a token to absurd
values and asserts that not one feature moves.

## Backtest fidelity

Modelled: AMM fills against reconstructed reserves; your own trade moving the
pool; execution latency; the full measured fee stack; failed-transaction retry
cost; exit liquidity caps that force large positions to unwind in slices at
progressively worse prices; terminal positions marked out at what they would
actually fetch rather than the last printed price.

Not modelled, and therefore where results should be discounted: intra-interval
price movement (snapshot cadence is 20–60s, so stops trigger later and at better
prices than reality), sandwich attacks on entry, and transactions that fail to
land at all.

## Component notes

**`sim/amm.py`** — constant product plus the pump.fun bonding curve, which is
constant product over virtual reserves. `from_liquidity_usd` reconstructs
reserves from the `(liquidity_usd, price_usd)` pair every data API reports, so
any snapshot can be turned into a tradable pool. `round_trip_cost` answers the
question that actually matters before entering: what fraction of capital
disappears just from buying and selling back.

**`sim/costs.py`** — tiers derived from on-chain measurement rather than
provider documentation. The important structural fact it encodes: fixed
per-transaction cost is small (7 bps of a 0.5 SOL position) while proportional
cost is large (200 bps of AMM fees plus impact), so size must be tied to pool
depth.

**`safety/filters.py`** — three stages, cheapest first, because thousands of
tokens are evaluated per hour and a network round-trip must never run on a token
free arithmetic already disqualifies. Two subtleties that are easy to get wrong
and are handled explicitly: the largest holder of nearly every token is its own
AMM reserve account, so concentration checks exclude addresses identified as
pool, LP vault or locker infrastructure; and Token-2022 is now the majority
program for new pump.fun launches, so the program id alone means nothing — what
matters is whether a lethal extension is set.

**`risk/exit.py`** — the trailing stop is deliberately *not* armed before the
first ladder rung. New tokens wick 40% routinely; an early trail converts
survivable noise into realised losses.

**`model/train.py`** — refuses to return a model below 300 rows or 40 positives.
Splits are chronological, purged by the label horizon, embargoed, and grouped by
mint so the same token cannot appear in both train and test. The reported metric
is precision in the top decile against the base rate, because that is the slice
the bot actually trades — AUC over the whole distribution is not the question
being asked.
