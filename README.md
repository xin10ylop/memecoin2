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
the base rate — from an unbiased census of **924 launches** — is that the median
new token **never trades above its first print**, 7.5% ever double, and 1.3%
ever 5x.

Full numbers, methodology and caveats: **[docs/RESEARCH.md](docs/RESEARCH.md)**.

---

## What it does

```
discover  ──▶  track  ──▶  features  ──▶  safety  ──▶  score  ──▶  size  ──▶  execute
   │             │            │             │            │           │           │
Jupiter      age-tiered    fixed-age    3-stage      rules +     fractional   paper /
recent       re-polling    snapshot     reject       LightGBM     Kelly +     dry /
feed         (20s → 2h)    features     stack        blend        pool cap    live
                                                                                │
                                              ladder · trail · time · hard ◀────┘
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

**Entry.** A token is considered at fixed ages (60s, 120s, 180s, 240s, 300s).
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

The strategy backtests at **+72% ROI over 78 trades, 61.5% win rate, profit
factor 6.6**, and after deleting its three best trades still returns +8.0% per
trade. The 95% confidence interval on expectancy is **[0.052, 0.640] SOL per
trade** — positive across the whole interval. That is
encouraging and it is **not** evidence of a durable edge:

- 78 trades from a few hours of one day, in one market regime.
- **Nobody has published a profitable memecoin selection strategy.** The best
  published result — a model-guided selection at 76% precision — still loses
  26.64% on average. A repository claiming better deserves proportionate
  scepticism, including from its author.
- The 95% CI on expectancy per trade is **[0.002, 0.694] SOL** — the lower bound
  is barely above zero.
- Several rule variants were tried against the same data and the best reported;
  the correction for that is out-of-sample validation, which has not happened.
- No real transaction has been landed, so failed sends and sandwich attacks on
  entry are unmodelled.

What to do about it is in [docs/RUNBOOK.md](docs/RUNBOOK.md): run the collector
continuously for weeks, retrain, then paper trade for a month before risking
anything you would miss.

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
