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

| strategy | trades | win% | ROI | expectancy | PF |
|---|---|---|---|---|---|
| buy everything | 390 | 8.7% | −1.35% | 0.985x | 0.89 |
| holders ≥ 20 | 58 | 41.4% | +28.99% | 1.278x | 2.69 |
| score ≥ 0.55 + safety gate | 44 | 63.6% | +87.0% | 1.870x | 5.43 |
| + market-cap gate, corrected fees, dump detector | 69 | 60.9% | +89.9% | 2.009x | 7.31 |
| **+ trained model, deployer reputation** | **99** | **64.6%** | **+94.1%** | **1.885x** | **9.90** |

The final row is the current configuration. It carries the corrected 1.25%/side
pump.fun fee, which by itself cost about 13 percentage points of ROI relative to
the same strategy priced at the commonly quoted 1%.

Robustness of the headline strategy — profit is meaningless if one trade
carries it:

| | total PnL | mean/trade | win% |
|---|---|---|---|
| all trades | +33.13 SOL | +0.335 | 64.6% |
| dropping the best trade | +20.49 SOL | +0.209 | 64.3% |
| dropping the best 2 | +10.85 SOL | +0.112 | 63.9% |
| dropping the best 3 | +9.92 SOL | +0.103 | 63.5% |

It survives the deletion of its three best trades and the median trade is
**1.15x**, so this is not a single-outlier artefact — which an earlier, cruder
rule *was* (93.9% of its profit came from one token).

## 6. When to buy

The research raised a sharp objection to the design: waiting 60-300 seconds may
filter out bot-only launches, but the median graduating token graduates at 4.4
minutes, so the window might also be selecting *out* the winners. That is
testable, so it was tested. Each checkpoint backtested in isolation:

| entry age | trades | win rate | ROI | profit factor | mean/trade ex-top-3 |
|---|---|---|---|---|---|
| 30s | 19 | 31.6% | **−4.4%** | 0.81 | −0.087 |
| 60s | 49 | 65.3% | +26.2% | 3.11 | +0.061 |
| **180s** | **31** | **67.7%** | **+157.9%** | **26.55** | **+0.115** |
| 300s | 25 | 48.0% | +24.2% | 2.23 | +0.014 |
| 600s | 22 | 45.5% | +14.2% | 2.00 | −0.009 |
| 1800s | 4 | 25.0% | **−9.9%** | 0.23 | −0.127 |

A clean inverted-U in every column, which is far more reassuring than a spike
would be. **Sniping the mint loses money outright** — at 30 seconds there is
nothing to measure and the bot is buying at the base rate, where the median
token never moves. By half an hour the move has already happened. The
information arrives in between and peaks near three minutes.

Both tails were losing configurations rather than merely weaker ones, so the
decision ages were narrowed to 60-300s bracketing the peak, and the live
trader's consideration window with them. The effect on the headline is a
*reduction* in ROI (94% to 72%) and a substantial improvement in what matters:
the 95% confidence interval on expectancy per trade moves from [0.002, 0.694]
to **[0.052, 0.640]**, and the probability that expectancy is positive reaches
1.000 in bootstrap. Fewer marginal trades, a more reliable edge.

## 7. Comparison against the published literature

A separate research sweep collected what has actually been published. Where it
agrees with our measurements, confidence goes up; where it disagrees, the
disagreement is recorded rather than resolved by preference.

**Agrees, and we adopted it:**

- *Initial market cap is the strongest single predictor.* A survival analysis of
  832,941 launches reports it as the dominant covariate (Cox HR 4.51). Tested
  on our census it replicates cleanly and — the useful part — is nearly
  uncorrelated with liquidity (r = 0.06), so it is independent evidence. Our
  sweep: 8.7% base 2x rate becomes 14.7% above $3k, 24.3% above $5k, 35.1%
  above $7.5k. Adopted as a $5k floor plus a graded score term.
- *Venue fees are higher than commonly quoted.* pump.fun's bonding curve takes
  1.25% per side (0.95% protocol + 0.30% creator), not the 1% usually cited,
  and PumpSwap scales 1.25% down to a 0.30% floor with market cap. Our cost
  model was wrong and has been corrected; the correction cost the headline
  backtest about 13 percentage points of ROI, which is the point of doing it.
- *Break-even hit rates.* At ~5% round-trip friction with total loss on losers,
  published figures are 52.5% for a 2x target, 21.0% for 5x, 10.5% for 10x. Our
  independent cost model reproduces 52.0%, 20.9% and 10.5% — a genuine
  cross-check of the fee stack. It also quantifies the stop: cutting the loss
  to −45% instead of a total loss roughly halves each figure (5x target needs
  12.0%, not 20.9%).
- *A 4-sigma dump detector is worth having.* Marino et al. find at least one
  4-sigma dump event in 92.22% of tokens with ≥30 swaps. Implemented as a
  Shewhart control chart on log returns; in the backtest it now accounts for 5
  of 69 exits, firing on the coordinated sell rather than waiting for the
  trailing stop.
- *The graduation rate is a regime variable, not a constant.* Measured values
  range from 0.198% to 6.7% across periods — a factor of thirty. This is why
  `signals/regime.py` measures conditions continuously and scales size,
  concurrency and selectivity, rather than shipping one fixed configuration.

**Disagrees with our data — recorded, not adopted:**

- *Deployer reputation gives a 35-110x lift* (an "elite tier" graduating at 71%
  against a 0.63% base). Implemented and measured **walk-forward** on our own
  census — a creator scored only from launches that happened strictly before the
  one being judged — the lift is **2.09x**: a 10.65% success rate in the top
  tercile against a 5.11% base, with the bottom tercile at 1.18%. Real and worth
  having, especially for *avoiding* bad creators (a 9x spread between terciles),
  but an order of magnitude below the published claim. The gap is almost
  certainly the selection effect the source itself flagged: the elite tier was
  chosen by the same statistic then quoted for it. Adopted at its measured
  strength, not its advertised one.
- *Liquidity accumulation speed is the strongest predictor, and trade count
  "mostly measures bots and wash trades".* Our data inverts this. Trade count is
  the **strongest** single predictor we have — bottom quartile 0.85%, top
  quartile 21.72%, a 3.0x lift — while `sol_per_trade` is non-monotonic (3.7%,
  11.5%, 10.3%, 3.6% across quartiles) and liquidity-per-trade runs the *wrong*
  way entirely (14.9% down to 1.7%). Among tokens with above-median trade count,
  *lower* SOL per trade does better (15.9% vs 8.0%). Many small trades beats few
  large ones, which is the same "real people are arriving" finding as everything
  else here. The likely reconciliation is that the published claim concerns
  lifetime graduation measured on vSOL accumulation, while ours concerns a
  30-minute forward return from a checkpoint 60-300 seconds in — different
  question, different answer. Kept as tree features, deliberately *not* added as
  a linear scorer term given the non-monotonicity.
- *A Telegram link lifts graduation 8.94x.* **This was retested as the census
  grew, and the earlier apparent contradiction was noise.** At n=24 our data
  showed a 0.50x lift; at n=49 it shows 1.20x, with a 95% interval of
  [4.4%, 21.8%] that comfortably contains the published effect. Our sample
  cannot resolve this against a Kaplan-Meier study of 832,941 launches
  (1.485% vs 0.166% graduation, Cox HR 5.40, log-rank p < 1e-100), and the
  honest conclusion is that we have no evidence either way. Adopted as a small
  scorer weight reflecting a published prior held with low confidence.

  Two parts of the same study *do* replicate directionally in our data: all
  three socials together shows 19.05% versus an 8.54% base (2.23x, n=21, against
  a published 17.4x), and Twitter presence is **not** discriminative — 0.89x in
  our data, Cox HR 1.30 in theirs, because 63% of launches carry one. Twitter
  presence is therefore not rewarded by the scorer at all.

**Where the research changed the design rather than the parameters:**

The measured literature on social feeds is uniformly hostile to using them as an
entry trigger, and the social module was rewritten around that. Across 10,687
pump events the price peaks at roughly two minutes; ranked members of tiered
channels get the signal 1-10 seconds ahead of ordinary members and abnormal
*sell* volume appears at second 19, so the dump begins while followers are still
buying. The correlation between a channel's audience size and the resulting pump
is **−0.162** — a bigger channel is not a better signal. One documented channel
announced a coin at its peak, making follower profit arithmetically impossible.
Both best-performing published Solana rug models use zero social features.

So the module now answers "is this promotion fake" and "is this account
borrowed" rather than "should I buy": a bot-amplification proxy standing in for
the retired Botometer, and a repurposed-handle check (an old account whose
crypto posting began two weeks ago after a long silence). Attention breadth
remains available as a weak confirming input; it can never originate a trade.

**Context that should temper any enthusiasm:**

- Solidus Labs flags **98.6%** of ~7M pump.fun tokens as rug or pump-and-dump;
  only ~97k ever held more than $1,000 of liquidity.
- CoinGecko's 18.67M-token study finds **68.67%** record their last trade on
  launch day and only **4.55%** are still trading after 90 days.
- **21.4%** of pre-migration pump.fun transactions are wash trades, so raw
  volume features should be discounted by roughly a fifth.
- Of migrated tokens, only **5.20%** never trade below their migration price;
  60.26% are below 0.2x of it within twenty minutes.
- Median Solana memecoin hold time has collapsed to **100 seconds**.
- Most importantly: **nobody has published a profitable memecoin selection
  strategy.** The best published result, a model-guided top-100 selection at 76%
  precision, still loses 26.64% on average — better than the 60.71% loss from
  random selection, but still a loss. Any claim to have beaten that, including
  the one in this repository, deserves proportionate scepticism.

## 8. The model

With ~7,100 decision rows and 438 positive examples the trainer stopped
refusing. Fitted with purged, embargoed, mint-grouped walk-forward splits and
evaluated out-of-fold:

| metric | value |
|---|---|
| rows / positives | 7,898 / 470 |
| base rate (peak ≥ 1.5x within 30 min) | 5.95% |
| top-decile precision | **32.2%** |
| lift over base rate | **6.65x** |
| mean peak multiple in the top decile | 2.83x |

Top features, in importance order: `s5m_liquidityChange`, `top_holders_pct`,
`dev_mints`, `liq_accel`, `s1h_priceChange`, `px_vol`, `liquidity`,
`s5m_priceChange`, `hold_accel`, `px_accel`, `buy_sell_ratio`, `vol_per_trader`.

Two deliberate exclusions, both of which cost measured performance and were made
anyway:

- **Raw price level** (`price_usd`, `fdv`, `mcap` as levels). These correlate
  with the label only because the label is a ratio — a token priced at 1e-9
  reaches 2x on an absolute move a token priced at 1e-4 could never make.
  Keeping them teaches the model to buy small numbers. `log_mcap` and `log_liq`
  carry the scale information that is actually meaningful.
- **Observation cadence** (`obs_age`, `obs_lag`, `n_obs`). These are legitimately
  known at decision time and the model leans on them heavily — they were the top
  two features before removal — but they encode *this* collector's age-tiered
  polling schedule rather than anything about the market, so a deployment
  polling differently would see a different distribution. Removing them cost
  lift 5.90 → 5.78 while the top-decile multiple *rose* 3.00 → 3.12. The edge
  was never in the sampling artefact.

Adding the model to the rule scorer, in backtest:

| configuration | trades | win% | ROI | expectancy | PF | mean/trade ex-top-3 |
|---|---|---|---|---|---|---|
| rules only | 93 | 61.3% | +79.9% | 1.873x | 7.73 | +0.076 |
| rules + model | 70 | 62.9% | +120.5% | 2.159x | 12.71 | +0.123 |

The model makes the system *more* selective (70 trades rather than 93) and the
robustness metric improves alongside the headline, which is the result that
matters — it is not simply chasing the tail harder.

**The important caveat on that table:** the backtest period overlaps the model's
training data. The purged walk-forward lift of 6.00x is the honest out-of-sample
number; the +120.5% ROI is not, and should be read as "the model's ranking is
useful", not as an expected return. Treating it otherwise is exactly the mistake
this document keeps warning about.

## 9. What these results are not

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
6. **The model's backtest is partly in-sample.** See section 7. The out-of-fold
   lift is real; the ROI figure that includes the model is not an out-of-sample
   estimate.
7. **No live fills.** Paper and dry modes price against real pool state, but no
   real transaction has been landed, so nothing here accounts for failed sends,
   sandwich attacks on entry, or the difference between a quote and a fill.

The correct next step is not to increase size. It is to run the collector
continuously for several weeks on an always-on host, retrain, and re-measure.
