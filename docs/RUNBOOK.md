# Runbook

How to operate this without losing more than you intended.

## 0. Before anything

This system's own measurements say the median new Solana token never trades
above its first print, and that 1.3% ever 5x. The strategy shows an edge on a
four-hour sample with a 95% confidence interval whose lower bound is barely
above zero. Treat every number as provisional until you have re-measured it
yourself over weeks.

**Fund a dedicated wallet with an amount you would be untroubled to lose
entirely.** Not "would rather not lose". Untroubled.

## 1. Collect data (weeks, not hours)

```bash
degen collect
```

Run this on an always-on host. It uses roughly 45 requests/minute against free
endpoints and writes append-only JSONL. Anything that stops the process is
safe — the registry is checkpointed every minute and resumes.

The single biggest weakness of the current results is that the collector has
run for hours, not weeks. Everything downstream improves as this runs longer,
and nothing else you do matters as much.

Check progress:

```bash
degen status
degen baserates      # recompute the outcome distribution on your own data
```

A useful threshold: **10,000+ tracked mints across at least two weeks**, which
should give a few hundred positive examples — enough for `degen train` to stop
refusing.

## 2. Validate before trusting

One command matters:

```bash
degen validate
```

It splits the census chronologically, fits everything fittable on the earlier
part, and measures on the later part. Read the **out-of-sample** column and
ignore the in-sample one entirely — the in-sample column is what a strategy
looks like when it has been tuned against the data you are scoring it on, and
it will always look better.

What good looks like: out-of-sample ROI and in-sample ROI in the same
neighbourhood. What overfitting looks like: in-sample far higher, and the trade
count collapsing. When this repository last ran it, the four-condition gate gave
16.6% in-sample against 23.1% out-of-sample — agreement — while adding the
weighted scorer gave 26.9% against 10.5%, which is the shape to be suspicious
of.

`degen backtest` is in-sample by construction. Read its *robustness* block
rather than its ROI: if deleting the three best trades turns the result
negative, you have a lucky token, not a strategy.

`degen train` refuses when the data cannot support a model, and that refusal is
a feature. Even when it succeeds, the model is not the default — see the table
in the README for why.

## 3. Paper trade

```bash
degen trade --mode paper --bankroll 5          # four-condition gate (default)
degen trade --mode paper --rule scorer         # opt into the weighted scorer + model
```

Fills are simulated against live pool state using the same AMM maths as the
backtester, so paper results are directly comparable. Run for **at least a
month**. What you are looking for:

- Does the live hit rate match the backtest hit rate? A large gap means the
  backtest is leaking or the cadence assumption is wrong.
- What fraction of entries are rejected at each stage? `degen status` and the
  `events` dataset record every rejection and its reason.
- Are the exits firing where you expected, or is everything hitting the time
  stop?

Then `--mode dry`, which builds real Jupiter routes and gets real quotes
without sending. Compare its quoted fills to what paper assumed. A consistent
gap is your slippage model being wrong.

## 4. Going live

Only after paper trading has reproduced the backtest for a month.

```bash
cp .env.example .env      # set DEGEN_WALLET_KEY and a real DEGEN_RPC_URL
degen trade --mode live --bankroll 1 --i-understand-the-risk
```

Non-negotiables:

- **A dedicated hot wallet.** Never the wallet holding anything else. The key
  sits in an environment variable on a machine running networked code.
- **A real RPC endpoint.** The public one is rate-limited and slow; you will
  miss fills and mis-price the local fee market. Helius, Triton, QuickNode and
  Chainstack all work.
- **Start at a bankroll far below what you eventually intend.** The first live
  week exists to discover what the simulation got wrong, and it will have got
  something wrong.
- **Watch the first day.** Not "check in occasionally" — watch it.

## 5. Ongoing operation

**Daily.** Check `degen status`. Confirm the breakers have not fired. Skim the
`events` dataset for a rejection reason that has suddenly become dominant — that
usually means an upstream schema changed rather than the market.

**Weekly.** Re-run `degen backtest` on accumulated data. Re-run `degen train`.
Compare live fills against paper fills for the same tokens.

**When a breaker fires**, it stays fired. Read the trades that caused it before
calling `resume()`. A halt after eight consecutive losses is information, not an
inconvenience.

## 6. Failure modes, and what they look like

| Symptom | Likely cause | What to do |
|---|---|---|
| Sudden flood of `429`s | too many processes sharing free rate limits | run collector and trader on separate hosts, or get an API key |
| Every token rejected at one stage | upstream schema changed | check `events` for the reason; the field probably went null |
| Live fills much worse than paper | slippage model optimistic, or sandwiching | tighten `slippage_bps`, reduce size, route via Jito bundles |
| Buys never land | priority fee too low for the local market | `suggest_cu_price()` with the pool accounts, not globally |
| Backtest great, paper poor | leakage, or cadence assumption wrong | run the leakage tests; check `obs_lag` in the feature rows |
| Bot buys the same rug repeatedly | creator-level cap not engaging | confirm `dev` is populated on the feature row |
| Position unexitable | bought into a pool too thin for the size | lower `max_frac_of_pool`; it is 2% for a reason |

## 7. Things that will destroy the account

In rough order of likelihood:

1. **Sizing up after a good week.** The distribution is fat-tailed; a good week
   is weak evidence. The Kelly fraction is 0.20 because the estimate of the edge
   is itself uncertain.
2. **Turning off the safety filter because it rejected a token that went up.**
   It will do this constantly. It is priced in.
3. **Switching to `--rule scorer` because its backtest looks better.** It does
   look better, in-sample, which is exactly the problem. Check `degen validate`
   before believing any configuration change.
4. **Removing the time stop.** It closes trades that "might still work". Most of
   them do not, and the capital is worth more elsewhere.
5. **Running live with the public RPC.** Missed fills are invisible losses.
6. **Reusing a main wallet.** One compromised key ends everything, not just this.
7. **Trading a regime the model never saw.** Memecoin activity is strongly
   regime-dependent; a model trained in one is not valid in another. Retrain.
8. **Believing this document over your own measurements.** Re-measure. This
   repository's own headline result was wrong once already — a 136x trade that
   turned out to be a broken price field — and it was only caught by auditing
   individual trades rather than reading summary statistics.

## 8. What is legal, and what is not

Running an automated bot on your own funds is generally lawful. The following
are not, in most jurisdictions, regardless of what the code makes possible:
wash trading to manufacture volume, promoting a token you are simultaneously
selling, trading on non-public information about a launch you are involved in,
and front-running other people's orders. Keep records — high-frequency crypto
trading generates a tax reporting burden that is much easier to handle
contemporaneously than reconstructed.
