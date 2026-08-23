# Empirical findings

Everything here was measured by this system on live Solana data, or measured
on-chain directly. Where a number comes from somewhere else it is cited. Where
a claim is uncertain it says so. Nothing in this file is copied from a
promotional page.

---

## 1. The base rate: what actually happens to a new token

Method: the collector watches Jupiter's recent-tokens feed and records every
new mint within seconds of its first pool existing, then re-polls each one on
an age-tiered schedule. Because it sees *every* launch rather than the ones
that later became interesting, the resulting census has no survivorship bias.
The figures below cover **924 launches** caught under 180 seconds old and
observed at least five times.

| Outcome (peak multiple from first observed price) | Count | Rate | 95% CI |
|---|---|---|---|
| ever ≥ 1.3x | 162 | 17.53% | 15.22 – 20.12% |
| ever ≥ 1.5x | 123 | 13.31% | 11.27 – 15.66% |
| ever ≥ 2x   | 69  | 7.47%  | 5.94 – 9.34% |
| ever ≥ 3x   | 32  | 3.46%  | 2.46 – 4.85% |
| ever ≥ 5x   | 12  | 1.30%  | 0.74 – 2.26% |
| ever ≥ 10x  | 4   | 0.43%  | 0.17 – 1.11% |
| ever ≥ 20x  | 3   | 0.33%  | 0.11 – 0.95% |
| ever ≥ 50x  | 2   | 0.22%  | 0.06 – 0.79% |

Distribution shape:

- **Median peak multiple: 1.000x.** The typical new token never trades above
  its first print. Not "goes up a little" — never goes up at all.
- **Mean peak multiple: 6.21x**, but **1.21x excluding the top 1%**. Essentially
  the entire expected value of the asset class lives in a handful of tokens.
- Only **6.2%** ever reach $10,000 of liquidity; only **13.9%** ever reach 50
  holders.

The practical consequence: a strategy that buys launches indiscriminately is a
guaranteed loser, and this is measurable rather than a matter of opinion — the
backtest of `buy-everything` returns **-1.35% ROI with a profit factor of 0.89**
after realistic costs. The question is never "is this token going to moon", it
is "is this one of the 6% where anything at all is happening".

## 2. What predicts the difference

Measured at a **fixed decision age** (60s and 180s), using only data available
at or before that instant, against the outcome "peak ≥ 1.3x within the next
hour". Base rate 8.3%. Split into terciles:

| Signal at decision time | bottom | middle | top |
|---|---|---|---|
| liquidity trend (`liq_slope`) | 3.2% | 1.6% | **24.6%** |
| holder-count trend (`hold_slope`) | 1.3% | 3.3% | **24.0%** |
| price vs first print (`px_from_first`) | 2.5% | 3.2% | **22.7%** |
| holder count | 2.4% | 1.6% | **20.6%** |
| liquidity per holder (inverted — low is better) | **20.4%** | 3.2% | 1.3% |
| buy/sell ratio | 2.9% | 4.1% | **18.5%** |
| trade count | 3.7% | 4.5% | **16.9%** |
| creator's prior mint count (`dev_mints`) | **9.4%** | 7.3% | 6.5% |

Every one of these says the same thing in a different vocabulary: *other people
are arriving, and they are buying rather than selling*. Low liquidity-per-holder
is the same statement again — it distinguishes many small real buyers from one
wallet providing the whole float.

Combining them as a gate:

| Filter | passes | hit rate (1.3x) | median peak |
|---|---|---|---|
| none | 100% | 8.3% | 1.000x |
| holders ≥ 10 | 19.4% | 27.5% | 1.082x |
| holders ≥ 20 | 14.0% | 34.8% | 1.160x |
| holders ≥ 10 and buy/sell > 0.6 | 12.6% | 33.9% | 1.163x |
| holders ≥ 30, liq ≥ $5k, buy/sell ≥ 0.6 | 5.5% | — | 1.069x |

**This is the central finding and it determines the design of the whole system:
the predictive signal does not exist at t=0.** At the moment of the mint there
is nothing to measure except the creator's history. The information arrives
over the following one to five minutes, as participants either show up or do
not. A sniper that buys at block zero is buying a lottery ticket at the base
rate; waiting 60–180 seconds trades away the first part of the move in exchange
for a roughly 4x improvement in hit rate.

## 3. The cost floor

Measured on-chain across 272 successful pump.fun transactions in 14 consecutive
blocks (full detail in `01_latency_infra.md`):

- Landed compute-unit price on pump.fun: **p50 264,166 / p75 812,534 /
  p90 4,000,000 microlamports per CU**. The *global* prioritisation floor at the
  same moment was ~2,986. pump.fun runs a local fee market roughly 100x hotter
  than the chain average, so a generic fee estimator will underprice a snipe by
  more than an order of magnitude and the transaction simply will not land.
- Compute units consumed: pump.fun bonding curve median **153,860**; PumpSwap
  **85,938**; Raydium AMM v4 **142,828**.
- Jito tips: only **12%** of landed pump.fun transactions tip at all, and the
  distribution among tippers is bimodal — p50 10,000 lamports, p90 3,000,000.
  Jito's own `tip_floor` endpoint reported a 75th percentile of 9,523 lamports
  at the same moment, which is a network-wide figure and badly misleading as a
  target for a contested mint.
- **77.1% of transactions touching the pump.fun program fail** (n=4,000), and
  the dominant failure mode is not slippage — it is third-party guard programs
  aborting after ~950–2,750 CU. Professional snipers deliberately front their
  swap with a cheap precondition check so that losing a race costs the base fee
  rather than a bad fill.

Translated into what a trade actually costs:

| tier | cu price | Jito tip | SOL/tx | USD/tx |
|---|---|---|---|---|
| routine | 500,000 | 0 | 0.000091 | $0.009 |
| contested | 2,000,000 | 0.001 SOL | 0.001483 | $0.139 |
| aggressive | 4,000,000 | 0.003 SOL | 0.004097 | $0.385 |

For a 0.5 SOL position on pump.fun taking one buy and three ladder sells at the
routine tier: fixed fees **7.3 bps**, AMM fees **200 bps**, giving a **2.07%
floor before any price impact**. Price impact then dominates: on a $12k pool a
1 SOL buy costs another 1.8% in slippage. The fixed costs are almost
irrelevant; the proportional costs are the whole game, which is why position
size must be tied to pool depth rather than to conviction alone.

## 4. The arithmetic of whether this can work at all

Fractional Kelly on a binary approximation, with `b` the net odds on a win and
`p` the win probability, requires `b·p > 1−p` for any positive edge.

At a 15% hit rate with an average loss of 50% of the position (which is what the
−45% hard stop produces), breaking even needs:

```
b > (1 − 0.15) / 0.15 = 5.67   →   win_mult > 1 + 5.67 × 0.50 = 3.83x
```

**At a 15% hit rate you need your average winner to exceed roughly 3.8x.** This
is the constraint that justifies the exit ladder: a policy that sells the whole
position at 2x cannot clear the bar no matter how good the entries are. The
ladder exists specifically to keep a residual position alive into the tail,
because the tail is where the expectancy is (mean peak 6.21x vs 1.21x excluding
the top 1%).

## 5. Strategy results

Backtested on 28,529 collected snapshots covering 1,210 tokens, with AMM fills
against reconstructed pool reserves, 3-second execution latency, and the full
measured cost stack.

| strategy | trades | win% | ROI | expectancy | PF | P(exp>0) |
|---|---|---|---|---|---|---|
| buy everything | 390 | 8.7% | −1.35% | 0.985x | 0.89 | 0.365 |
| holders ≥ 20 | 58 | 41.4% | +28.99% | 1.278x | 2.69 | 0.660 |
| score ≥ 0.55, no safety gate | 70 | 62.9% | +77.4% | 1.831x | 7.52 | 0.995 |
| **score ≥ 0.55 + safety gate** | **44** | **63.6%** | **+87.0%** | **1.870x** | **5.43** | **0.977** |

Robustness of the headline strategy — profit is meaningless if one trade
carries it:

| | total PnL | mean/trade | win% |
|---|---|---|---|
| all trades | +11.08 SOL | +0.252 | 63.6% |
| dropping the best trade | +2.13 SOL | +0.050 | 62.8% |
| dropping the best 2 | +1.52 SOL | +0.036 | 61.9% |
| dropping the best 3 | +0.96 SOL | +0.023 | 61.0% |

It survives the deletion of its three best trades and the median trade is
**1.11x**, so this is not a single-outlier artefact — which an earlier, cruder
rule *was* (93.9% of its profit came from one token).

## 6. What these results are not

Stated plainly, because the numbers above are the kind that get over-read:

1. **The sample is one afternoon.** 44 trades drawn from roughly four hours of
   launches on a single day. Memecoin activity is strongly regime-dependent;
   nothing here says anything about how this behaves in a different regime.
2. **The confidence interval on expectancy is [0.002, 0.694] SOL per trade.**
   The lower bound is barely above zero. "Probably profitable" is the correct
   reading; "profitable" is not.
3. **Selection effect.** Several rule variants were evaluated against the same
   data and the best was reported. The honest correction for that is out-of-
   sample validation, which has not happened yet — that is what paper trading
   is for.
4. **Observation windows are truncated.** The container running the collector
   suspends when idle, so many tokens' forward windows are cut short. This
   biases measured peaks *downward* but also means slow rugs — which unfold
   over hours — are largely unobserved. The safety filter's benefit is
   therefore systematically **under**-counted here, which is why it stays on
   despite costing return in this sample.
5. **Snapshot cadence is 20–60 seconds.** Intra-interval wicks are invisible, so
   stops trigger later and at better prices than they would live. Treat
   reported drawdowns as optimistic.
6. **No live fills.** Paper and dry modes price against real pool state, but no
   real transaction has been landed, so nothing here accounts for failed sends,
   sandwich attacks on entry, or the difference between a quote and a fill.

The correct next step is not to increase size. It is to run the collector
continuously for several weeks on an always-on host, retrain, and re-measure.
