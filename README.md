# degen — a quantitative memecoin trading system for Solana

An end-to-end system that discovers every new Solana token within seconds of
launch, decides which of them are worth buying, sizes the position against pool
depth, and exits on a rule instead of on hope.

It is built around one finding, measured on its own data rather than assumed:

> **The predictive signal does not exist at t=0.** At the moment of a mint there
> is nothing to measure but the creator's history. The information arrives over
> the next one to five minutes, as real participants either show up or do not.
> Waiting for that evidence costs the first part of the move and improves the
> hit rate roughly fourfold.

That is why this is not a sniper. Sniping the mint buys at the base rate, and
the base rate — from an unbiased census of **3,298 launches** — is that the
median new token **never trades above its first print**, 7.9% ever double, and
2.2% ever 5x. Backtested on its own, entering at 30 seconds *loses money*.

Full numbers, methodology and caveats: **[docs/RESEARCH.md](docs/RESEARCH.md)**.

---

## What it does

```
discover ─▶ track ─▶ quality ─▶ features ─▶ safety ─▶ gate ─▶ size ─▶ execute
   │          │         │           │          │         │       │        │
Jupiter   age-tiered  price/liq  fixed-age  3-stage  4 conditions  Kelly  paper/
recent    re-polling  coherence  snapshot   reject   no weights   + pool  dry/
feed      20s → 2h    check      features   stack    no model      cap    live
                                                                            │
                        cost recovery · widening trail · dump · stops ◀─────┘
```

Every stage is shared between the backtester and the live trader. There is one
implementation of the features, one of the safety rules, one of the exit
policy, and one of the AMM fill maths — so paper results and live results are
directly comparable, and a divergence is a bug rather than a design choice.

## Quick start

```bash
pip install -e .

degen collect                      # start building the dataset (run it for days)
degen status                       # what the system knows
degen baserates                    # the honest outcome distribution
degen scan --top 15                # current candidates, with reasons
degen backtest --threshold 0.55    # evaluate on collected data
degen train                        # fit the model (refuses if underpowered)
degen validate                     # out-of-sample walk-forward - the number that counts
degen trade --mode paper           # trade with simulated fills against live prices
```

Nothing above needs an API key. Discovery, tracking, quotes, risk reports and
OHLCV history all run on free, keyless endpoints.

## Data sources

| Source | Used for | Auth | Cost |
|---|---|---|---|
| Jupiter Token API v2 | discovery, batch tracking (100 mints/call), quotes, swaps | none | free |
| RugCheck | holder graph, LP state, creator history, Token-2022 extensions | none | free |
| GeckoTerminal | historical minute OHLCV for exit-policy fitting | none | free |
| DexScreener | cross-chain pair data, paid-promotion signal | none | free |
| Solana RPC | account classification, local fee market | none | free tier |

Jupiter's recent-tokens feed is the backbone. It returns, for tokens seconds
old, the creator wallet, **how many tokens that creator has previously minted**,
holder count, top-holder concentration, mint/freeze authority state, and
buy/sell counts over four windows — data that would otherwise cost several RPC
round-trips per token, for free, in one call.

## The strategy

**Entry.** The default rule is the four-condition traction gate above — the only
entry rule that held up out of sample. The weighted scorer and the trained model
are available with `--rule scorer` and score better in-sample, which is exactly
why they are not the default.

A token is considered at fixed ages (60s, 120s, 180s, 240s, 300s).
Those ages are measured rather than chosen: backtesting each in isolation gives
a clean inverted-U peaking at 180 seconds, with sniping at 30s and entering at
30 minutes both *losing* money. See
[when to buy](docs/RESEARCH.md#6-when-to-buy).
At each checkpoint the system builds features from observations at or before
that age only, runs a three-stage safety filter, and scores it. Buying requires
the safety stack to pass and the conviction score to clear its threshold.

**Safety** runs cheapest-first, because thousands of tokens are evaluated per
hour: free arithmetic on data already held, then one RugCheck call, then RPC.
It rejects live mint or freeze authority, lethal Token-2022 extensions
(transfer fee, transfer hook, permanent delegate, non-transferable), token
factories, dev-held float, non-pool holder concentration, and pools too thin to
exit. It cannot tell you a token will rise; it removes the ways a position
becomes unexitable.

**Sizing** is fractional Kelly (a fifth of full), capped absolutely and capped
again at 2% of pool depth. Full Kelly on an edge estimated from a few hundred
trades is a mathematical guarantee of ruin, because the error in the estimate
exceeds the edge.

**Exit** is the part that decides whether the strategy works. On a distribution
where the mean peak is 6.2x but only 1.2x excluding the top 1%, the exit rule
determines whether the one trade in three hundred pays for the other 299:

1. **Cost recovery** — sell 40% at 1.6x, taking most of the stake off the table.
2. **Ladder** — 25% at 2.5x, 15% at 4x, 10% at 8x, 5% at 20x.
3. **Trailing stop** — armed only *after* the first rung, tightening from 45%
   to 22% drawdown as the trade matures. Not armed before, because new tokens
   wick 40% routinely and an early trail just donates the spread.
4. **Hard stop** at −45%, **time stop** at 15 minutes unless already up 12%,
   and an immediate exit on liquidity collapse.

Against a path that peaks at 10.2x and round-trips to 1.0x, this realises
**3.59x**.

## Risk controls

Fractional Kelly sizing · max 6 concurrent positions · one position per creator
· 30% total exposure cap · 2% of pool depth cap · cooldown after a loss ·
circuit breakers on daily loss (15%), session loss (25%), loss streak (8) and
consecutive fill failures (6). Breakers halt trading rather than reduce size: a
system that is quietly wrong should stop, not slow down.

## Modes

| Mode | Fills | Funds | Requires |
|---|---|---|---|
| `paper` | simulated against live pool state | none | nothing |
| `dry` | real Jupiter routes and quotes, no send | none | nothing |
| `live` | signed and sent | **real** | `DEGEN_WALLET_KEY`, `--i-understand-the-risk` |

Start in `paper`. See [docs/RUNBOOK.md](docs/RUNBOOK.md) before going live.

## The model

Once the census reached ~7,100 decision rows the trainer stopped refusing. Fitted
with purged, embargoed, mint-grouped walk-forward splits, it reaches **32.2%
precision in the top decile against a 5.95% base rate — a 6.65x lift** — with a
mean peak multiple of 2.83x in that decile.

Two feature groups were excluded deliberately even though both cost measured
performance: raw price levels (which correlate with a ratio label only because
small numbers multiply easily) and observation cadence (which encodes this
collector's polling schedule rather than the market). Details in
[docs/RESEARCH.md](docs/RESEARCH.md#7-the-model).

## Honest status

Two corrections happened on the way here, and both matter more than any headline
number.

**First, an audit killed the spectacular results.** An early version reported
+279% with a profit factor of 18, led by a 136x trade. That token's price rose
190-fold while its reported liquidity sat flat at $72,000 — in a
constant-product pool the two are mechanically linked, so those were broken
fields, not trades. A price/liquidity consistency guard now drops the 6% of
mints whose two series contradict each other, and every analysis path runs
behind it.

**Second, complexity was destroying the edge.** Measured on clean data with a
chronological split — fit on the earlier 60% of mints, test on the later 40%,
no token straddling the boundary:

| entry rule | IS trades | IS ROI | OOS trades | **OOS ROI** | OOS PF | P(exp>0) |
|---|---|---|---|---|---|---|
| holders ≥ 20 | 313 | 10.4% | 214 | +9.8% | 1.52 | 0.96 |
| + liquidity, buy/sell | 248 | 14.1% | 168 | +18.1% | 2.06 | 1.00 |
| **+ market cap** ← default | 219 | 16.6% | 142 | **+23.1%** | **2.41** | **1.00** |
| + full safety stack | 114 | 21.2% | 68 | +19.3% | 2.14 | 0.97 |
| + weighted rule scorer | 82 | 26.9% | 46 | +10.5% | 1.60 | 0.80 |
| + gradient-boosted model | 64 | 39.2% | 36 | +17.0% | 1.83 | 0.86 |

The first four rows have in-sample and out-of-sample figures that agree — that
is what generalisation looks like. The last two do not: the scorer's apparent
edge more than halves out of sample and both cut the trade count to a third.
They are fitting the training window, so **they are off by default.**

The shipped default is row three: four conditions, no fitted weights, no model.

```
holders ≥ 20   ·   liquidity ≥ $3,000   ·   buy/sell ≥ 0.55   ·   market cap ≥ $5,000
```

A modest, real edge — roughly a 2.4 profit factor on 142 held-out trades — not a
money printer. What it is *not*:

- The thresholds came from univariate analysis of an earlier census, so they are
  not fully out-of-sample themselves. A sweep of neighbouring values stays
  profitable, so it is a plateau rather than a spike, but the level above is
  optimistic by some unmeasured amount.
- The whole census is hours, not weeks, from a single market regime.
- No real transaction has been landed, so failed sends and sandwich attacks on
  entry are unmodelled.

Re-run `degen validate` as the census grows. It is the only command whose output
means anything. That is
encouraging and it is **not** evidence of a durable edge:

- The whole census is a few hours of one day, in one market regime. The
  out-of-sample window inside it is 33 trades.
- **Nobody has published a profitable memecoin selection strategy.** The best
  published result — a model-guided selection at 76% precision — still loses
  26.64% on average. This repository has not beaten that out of sample either.
- No real transaction has been landed, so failed sends and sandwich attacks on
  entry are unmodelled.
- The fix is not more tuning. It is more data: run the collector for weeks on an
  always-on host, then re-run `degen validate`.

What to do about it is in [docs/RUNBOOK.md](docs/RUNBOOK.md): run the collector
continuously for weeks, re-run `degen validate`, and only consider live trading
if the out-of-sample column stops being negative. Paper trade for a month after
that.

## Layout

```
src/degen/
  sources/     jupiter · rugcheck · dexscreener · geckoterminal · solana_rpc
  collect/     collector (discovery + forward tracking) · ohlcv_harvest
  store/       append-only JSONL lake with DuckDB views
  features/    fixed-age, leak-free feature construction
  labels/      forward-looking outcome labels
  model/       LightGBM with purged, embargoed, mint-grouped walk-forward CV
  signals/     transparent rule scorer + model blend
  safety/      three-stage reject stack
  sim/         AMM fill maths · measured cost model · backtester
  risk/        exit policy · portfolio risk manager
  execution/   paper · dry · live brokers behind one interface
  live/        the trading loop
tests/         56 tests, including a leakage suite
docs/          RESEARCH · ARCHITECTURE · RUNBOOK
```

## Legal and practical

Trading your own funds with your own bot is legal in most jurisdictions;
this is not legal or financial advice and you should confirm your own position.
Do not use this to wash trade, to promote tokens you hold, to trade on
non-public information about a launch, or to front-run other people's orders —
those are illegal in most places regardless of what the code can do.

Expect to lose money. The measured base rate is that the median new token never
goes up.
