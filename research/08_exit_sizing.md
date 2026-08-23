# Optimal exits and position sizing for power-law payoffs in a Solana memecoin bot: critique of the current ladder/trail/Kelly policy and replacement parameters

## Executive summary

Three structural facts, derived from your own 924-launch census, dominate everything else.

(1) **Your peak-multiple distribution has a local Pareto exponent below 1 in the tail.** Fitting piecewise Pareto to your knots (median 1.000x so P(M>1)<=0.50; P(M>=2)=0.075; P(M>=5)=0.013; P(M>=50)=0.0022) gives alpha=2.737 on [1x,2x], alpha=1.913 on [2x,5x], **alpha=0.772 on [5x,50x]**. alpha<1 means E[peak] does not converge: E[M] = 0.945 truncated at 100x, 1.020 at 200x, 1.247 at 1000x. An oracle selling at the exact top of every token in this population earns roughly 1.0x gross. Your entire edge must therefore come from selection, and the exit policy's only job is to not destroy the tail.

(2) **Your ladder destroys the tail.** With a position sized at 2% of quote reserve, your 40/25/15/10/5 schedule converts a charted 20x peak into a realised 4.14x. Across the fitted distribution only **7.3% of your policy's expected value comes from tokens that reach 5x**, versus 30% for a pure trail. This is empirically corroborated: the only published Solana memecoin bot deployment I found (arXiv:2606.08232, 190 trades) reports skewness of **-1.21** — a *left*-skewed P&L on a right-skewed asset, which is the signature of laddering out of the tail — while simultaneously **1.6% of its trades generated 100% of cumulative profit**.

(3) **The single highest-leverage parameter is not the ladder, it is the trailing stop width, and yours is roughly 2x too wide and scaled the wrong way.** Holding everything else fixed, tightening the base trail from 45% to 20-25% moves the current policy's log-growth from -1bp to +496bp per trade. With premature-stop risk made fully endogenous (stop probability *and* stop point both functions of width), the optimum is a clean closed form: **w* ~= sigma_bar * sqrt(T_rem)**, the width at which the trail just survives its own run's noise. Your fixed 45%->22% schedule is only correct for sigma_bar ~= 4%/min over a 30-minute run, and it *tightens* as the multiple rises when realised vol (and hence the correct width) rises.

The marginal sell/hold rule that unifies all of this: conditional on peak M>=m with local exponent alpha(m), E[M|M>=m] = m*alpha/(alpha-1), so **hold the marginal unit iff alpha(m) < 1/(1 - theta_eff)** where theta_eff = (1-g)*theta + g*rho is the effective fraction of the peak your trail retains. At theta_eff ~= 0.57 the threshold is 2.33: your census crosses it exactly at **m = 2x**. Sell below 2x, hold above. Your rungs at 4x, 8x and 20x sit squarely in the alpha<1 region where selling is provably value-destroying under any risk-neutral criterion.

On sizing: 0.20 fractional Kelly is defensible in direction but for the wrong reason, and the cap is mis-specified. f* swings from 0.001 to 0.96 across a plausible range of selection quality — your P(>=50x) is **2 events in 924 launches**, Poisson 95% CI [0.24, 7.22], a 30x range in the rate that dominates EV. Sampling error alone justifies little shrinkage; *model* error justifies a lot. Recommend c=0.10-0.15 plus a hard Busseti-Ryu-Boyd drawdown constraint rather than c=0.20 alone. And the "2% of pool depth" cap is 2-4x too loose depending on what "depth" means: the exact CPMM cap for a target one-way slippage s is P_max = y_quote * s/(1-s), so 1% slippage = 1.01% of the quote reserve = 0.51% of TVL.

One counter-intuitive execution result you should hardcode: **a constant-product invariant is path-independent, so slicing a sell into N pieces yields identical proceeds** (verified numerically: 1.960784 SOL for N=1 and N=100, zero fee; slightly *worse* with fees). TWAP-ing an exit in an AMM buys you nothing. The only thing that reduces impact is incoming buy flow, so the correct unwind rule is percentage-of-volume, not a clock.

## Numeric parameters

- **LADDER_RUNGS (replaces 40/25/15/10/5 @ 1.6/2.5/4/8/20)** = `[(1.45x, 0.20), (2.4x, 0.15)] — 35% total laddered, 65% rides the trail` — Weight sweep at 1.5x peaks log-growth at w1=10-20% (576.6bp at 10%, 542.5bp at 20%, 498.3bp at 30%, 451.8bp at 40%). The alpha rule forbids rungs above ~2.5x since alpha=1.913 on [2,5] and 0.772 on [5,50], both below the 2.33 hold threshold at theta_eff=0.57. Level is set slightly below 1.5x because P(reach 1.45x) ~= 17% versus 13.8% at 1.6x.
- **DELETE_RUNGS** = `remove the 4x, 8x and 20x rungs entirely` — alpha(m) < 1/(1-theta_eff) for all m > 2x on your census, so selling there is value-destroying under any risk-neutral criterion. These three rungs are why only 7.3% of your EV comes from M>=5x versus 30% for a pure trail.
- **TRAIL_WIDTH (replaces fixed 45% -> 22%)** = `w = clamp(1.00 * sigma_bar * sqrt(T_rem), 0.18, 0.60)` — Endogenous-stop simulation puts the interior optimum exactly at w = sigma_bar*sqrt(T_rem), the width at which the trail survives its own run's noise (b = (w/sigma)^2/T = 1). Verified at three (sigma, T) points: (4%,30) -> optimum 20-25% vs formula 21.9%; (8%,15) -> 25-30% vs 31%; (8%,30) -> 40-50% vs 43.8%.
- **sigma_bar** = `EWMA sd of 1-minute log returns of executable price, halflife 5 bars, floor 0.02, initialised at 0.06` — Do NOT derive from bar ranges: E[TrueRange]/sigma is 1.54 with dense ticks but falls to 1.06 at 4 ticks/bar and 0.82 at 2 ticks/bar, so ATR under-measures vol on exactly the illiquid tokens where the trail matters most.
- **T_rem** = `20 bars (minutes) default; 30 once the position has exceeded 3x` — Winners' median hold is 3.60 min but the runners that matter run much longer; the killed-GBM inversion gives a median residual life of ~12 min for the above-5x cohort versus 1-2.5 min below 2x.
- **TRAIL_ATR_EQUIVALENT (if you must express it in ATR)** = `w = 0.65 * sqrt(T_rem) * ATR; T=15 -> 2.5x ATR, T=30 -> 3.6x ATR, T=60 -> 5.0x ATR` — From w = sigma*sqrt(T) and ATR = 1.54*sigma*price. Use only with a tick-density correction.
- **TRAIL_ARMING** = `arm at fill, anchored to the running high of executable (post-impact) price` — 86.2% of positions never reach 1.6x on the fitted distribution, so arming after the first rung leaves the overwhelming majority of positions with no trail at all.
- **TRAIL_SCALING_DIRECTION** = `widen with realised vol (automatic in the formula); do NOT tighten as the multiple rises` — w* scales with sigma_bar*sqrt(T_rem) and both rise with the multiple. The current 45%->22% tightening is backwards in vol terms. Verify by regressing sigma_bar on log(multiple) in your own census before overriding.
- **HARD_STOP (replaces -45%)** = `-25%` — Hard-stop sweep is flat from -50% to -15% (g* between 498.2bp and 510.1bp) because a 25% trail from the entry-anchored high always fires first. Tightening is therefore free and reduces single-block gap exposure.
- **TIME_STOP_PRIMARY (replaces 15 min unless +12%)** = `exit if net buy volume(60s) < 0.25 * median net buy volume(trailing 5 min) OR unique buying wallets(60s) <= 1; evaluate every 10s from t+60s` — The hold criterion is mu > lambda*(1-rho); both are flow-driven, not clock-driven. Losers' median hold is 0.60 min empirically (arXiv:2606.08232).
- **TIME_STOP_BACKSTOP** = `6 minutes absolute` — 15 min is ~25x the empirical median losing hold and ~2.5x the implied median residual life at low multiples (1-2.5 min from the killed-GBM inversion).
- **TIME_STOP_WAIVER (replaces '+12%')** = `waive the flow test while the trail level is more than 1*sigma_bar above the entry price` — A fixed +12% threshold means something completely different at sigma_bar=2%/min (6 sigma) than at 15%/min (0.8 sigma).
- **KELLY_FRACTION c (replaces 0.20)** = `0.125, applied to f* computed at the 20th percentile of your posterior over the tail rates` — c=0.20 is exactly the right budget for 'P(ever below 65%) < 5%' IF f* were known: c = 2/(1 + ln(beta)/ln(x)) = 0.19. It is not known — f* ranges 0.001 to 0.96 over plausible selection quality, and P(>=50x) rests on 2 events (Poisson 95% CI [0.24, 7.22], a 30x rate range). Halving c costs only 0.36->0.234 of g_max, i.e. 35% of growth, against a 1000x parameter range.
- **RCK_LAMBDA (new hard sizing gate)** = `lambda = 6.456; enforce mean over the simulated payoff sample of (1 + f*(R-1))^(-lambda) <= 1` — Busseti-Ryu-Boyd exact bound: this guarantees P(W_min < 0.7) < 0.10 and simultaneously P(W_min<0.5) < 1.14% and P(W_min<0.8) < 3.07e-3%. In their lognormal experiment RCK achieved growth 0.047 versus 0.035 for fractional Kelly at the same 0.10 drawdown risk (+34%). The bound was conservative by 20-35% in Monte Carlo (0.073-0.080 realised against a 0.100 bound).
- **POSITION_SIZE_FORMULA** = `size = min( 0.125 * f*_p20 * bankroll , 0.010 * quote_reserve , 0.25 * B_buyflow_per_min * 1.5min , RCK_gate )` — Four independent binding constraints: Kelly with shrinkage, static pool depth, dynamic exit-liquidity, drawdown. Take the min.
- **POOL_DEPTH_CAP (replaces '2% of pool depth')** = `1.0% of the QUOTE-side reserve (= 0.5% of TVL)` — Exact CPMM: P_max = y_quote * s/(1-s). At 1% of quote reserve, one-way slippage is 0.99% and the round-trip wedge versus the charted multiple is 2.69% at 1.6x rising to 5.64% at 20x. At your current 2% those become 4.80% and 10.27%. Define 'depth' explicitly in code — if it currently means TVL, you are at 4% of quote reserve.
- **PARTICIPATION_RATE kappa (new)** = `0.25 of observed buy volume per child order` — CPMM slicing is path-independent (verified: identical proceeds for N=1 and N=100), so the only impact reduction comes from intervening buy flow. Standard POV territory; 0.20-0.35 is the defensible band.
- **MAX_CONCURRENT** = `keep 6, but cap TOTAL exposure at min(6 * f_single, f_single / rho_hat)` — n_eff = n/(1+(n-1)rho) = 2.40 at n=6, rho=0.30, rising only to 2.79 at n=12 — the marginal benefit of position 7+ is negligible. Total exposure saturates at 1/rho = 3.3x a single bet at rho=0.30.
- **TOTAL_EXPOSURE_CAP** = `4% of bankroll at rho_hat=0.30 (i.e. ~0.67% per position at 6 concurrent)` — Mis-specification test: at 2%/position (12% total) the light policy has P(dd>50%)=29.7% and P(dd>80%)=5.8% if the true selection lift is only 1.5x; at 0.5%/position (3% total) that falls to P(dd>50%)=0.3%. Size to survive K=1.5, not K=3.
- **CORRELATION_KILL_SWITCH (new)** = `if realised cross-position correlation of 1-min returns over the last 30 min > 0.60, halve total exposure; if > 0.80, stop opening new positions` — P(dd>50%) over 1000 trades at n=6, f=2%: 21.7% at rho=0, 30.8% at rho=0.30, 58.5% at rho=0.60, 84.7% at rho=0.90. Correlation, not count, is the risk driver above n=3.
- **LOSING_STREAK_TOLERANCE** = `do not trip a permanent circuit breaker below 15 consecutive losers` — A published 190-trade memecoin deployment recorded a longest streak of 12 consecutive losing trades while ending +117.7% cumulative (arXiv:2606.08232). With a 13.8% probability of reaching 1.45x, runs of 12+ losers are the base rate.
- **BACKTEST_METRIC_1 (new, mandatory)** = `share of E[R] contributed by trades with peak >= 5x; target >= 20%, reject below 10%` — Current policy 7.3%, light 2-rung 19.0-23.8%, pure trail 30.7%. This single number diagnoses tail destruction better than Sharpe.
- **BACKTEST_METRIC_2 (new, mandatory)** = `top-3-trade removal test; report cumulative P&L with top 1, 3, 5, 10 removed` — Direct replication of the fragility table in arXiv:2606.08232 (+117.7% -> +61.1% -> -50.6% -> -160.5% -> -414.2%). If removing 1.6% of trades flips the sign, the sample has not identified an edge.
- **BACKTEST_METRIC_3 (new, mandatory)** = `P&L skewness; a negative value on a right-skewed asset is a red flag` — The published deployment reported skewness -1.21 with excess kurtosis 6.61 — a left-skewed P&L produced by laddering out of a right-skewed asset.
- **M_CAP for all EV computations** = `200x` — alpha=0.772 in [5x,50x] means E[peak] does not converge: 0.945 at a 100x cap, 1.020 at 200x, 1.247 at 1000x. Fix the cap explicitly and report sensitivity.
- **HAIRCUT_MODEL for backtests** = `realised = charted * (1 - s*sqrt(m)/(1 + s*sqrt(m))) * (1-fee)^2, s = position/quote_reserve` — First-order fit to the exact CPMM simulation (quote reserve scales as sqrt(price), token reserve as 1/sqrt(price)). Reproduces the measured 2.69%->5.64% haircut at s=1% and 4.80%->10.27% at s=2% across m=1.6x to 20x.
- **ALPHA_RECALIBRATION (new)** = `re-estimate alpha(m) = -d ln P(peak>=m) / d ln m monthly; set the top rung where alpha crosses 1/(1-theta_eff)` — Rung placement depends only on the SHAPE of the peak distribution, which is scale-invariant to your selection edge and estimable from ~900 launches. Position size depends on the LEVEL, which is not reliably estimable.
- **SELECTION_LIFT MEASUREMENT (new)** = `K_hat = P_traded(peak>=2x) / 0.075, measured monthly on your traded cohort versus the census` — Break-even is K~=2.2 for the current policy and K~=1.35 for the light policy. This is a cheap, direct measurement of whether the filter is earning its keep, and it is the input that determines position size.

## APIs / endpoints

- **arXiv API** `http://export.arxiv.org/api/query?search_query=all:%22memecoin%22&start=0&max_results=40&sortBy=submittedDate&sortOrder=descending` · auth: none · cost: free — Full-text search over arXiv with structured Atom results (title, id, date, abstract). Verified working; returned the complete memecoin literature set used here. Supports field prefixes (all:, abs:, ti:) and quoted phrases; boolean AND across quoted phrases works but is brittle — prefer single quoted phrases with relevance sort.
- **OpenAlex works API** `https://api.openalex.org/works?search=<query>&per_page=15&select=id,doi,title,publication_year,open_access,primary_location` · auth: none (add mailto= parameter for the polite pool) · cost: free — Cross-publisher academic search returning DOIs and open-access PDF URLs. Verified working; this is how I located Kaminski & Lo (DOI 10.1016/j.finmar.2013.07.001) after a guessed NBER URL resolved to the wrong paper. Use it to resolve a citation to a real DOI before ever constructing a URL by hand.
- **Semantic Scholar Graph API** `https://api.semanticscholar.org/graph/v1/paper/search?query=<q>&fields=title,abstract,year,url,externalIds&limit=20` · auth: API key recommended (unauthenticated is heavily throttled) · cost: free with key — Paper search with abstracts. Endpoint exists and is correctly formed but returned HTTP 429 on every unauthenticated attempt in this session — treat as requiring an API key in practice.
- **Jupiter Swap quote API** `https://developers.jup.ag/docs/swap-api/get-quote (dev.jup.ag 301-redirects here; developers.jup.ag then 303-redirects docs paths to a /llms.mdx/ variant)` · auth: none for the public tier · cost: not verified — Quote endpoint exposing priceImpactPct and slippageBps. I confirmed the redirect chain via HTTP headers but was NOT able to read the field semantics from the docs page, so I am not asserting exact field definitions or limits. Verify against the live endpoint before coding against it.

## Traps and failure modes

- Your peak-multiple mean does not exist. E[M] is 0.945 at a 100x truncation and 1.247 at 1000x — a 32% swing from an arbitrary modelling choice. Any backtest that reports an expected return without stating its truncation cap is reporting an artifact. Always report EV at caps of 100x, 200x and 1000x.
- Charted multiple is not realised multiple. Your ladder turns a 20x peak into 4.14x realised at 2% of quote reserve. If your backtest measures peak multiples rather than proceeds/cost, it overstates tail trades by 3-5x — precisely the trades that determine whether the strategy works.
- Slicing an AMM exit is a no-op. Verified: identical proceeds for N=1 and N=100 (1.960784 SOL both), and slightly worse with fees. If you have TWAP unwind code you are paying extra transaction fees and extra hazard exposure for exactly zero impact reduction.
- Round-trip cost is NOT twice the one-way slippage. In an isolated CPMM a buy-then-sell round trip costs only the fees (0.49% at 0.25%/side), because impact is recovered. Budgeting 2x slippage will make you reject viable trades. The real cost is the multiple-dependent wedge, which is smaller than 2x slippage at low multiples and larger at high ones.
- Sizing off a point estimate of f* is the biggest single risk. f* ranges 0.001 to 0.96 across plausible selection quality. Your P(>=50x) is 2 events out of 924 with an exact Poisson 95% CI of [0.24, 7.22] events — a 30x rate range — and that segment dominates EV. A backtest that fits exit parameters and then reports f* from the same sample is circular.
- Overbetting and underbetting are NOT symmetric even though the growth curve is. c and 2-c give identical growth, but at c=0.2 P(ever -50%) is 0.195% while at c=1.8 it is 92.6%. Errors in the mean cost roughly 20x what errors in variance cost (Chopra & Ziemba).
- Full Kelly is far riskier than intuition suggests: measured P(W_min < 0.7) is 39.7% in the finite-outcome case and 56.9% in the lognormal case (Busseti/Ryu/Boyd Monte Carlo). If anyone proposes raising c, quote these.
- The trailing stop optimum sits at a 30-50% premature-stop probability. Operators reliably tighten trails after being stopped out of a runner, which is exactly the wrong reflex — being stopped early on a third of runners is the optimum, not a bug.
- A fixed percentage trail is simultaneously far too tight for high-vol tokens and far too loose for low-vol ones. Your 22% trail is 11 sigma for a token at 2%/min (never fires, gives back 22% of the peak) and 1.8 sigma at 12%/min (noise-stopped ~90% of the time within 30 bars).
- ATR under-measures volatility on illiquid tokens. E[TrueRange]/sigma is 1.54 with dense ticks, 1.29 at 12 ticks/bar, 1.06 at 4, and 0.82 at 2 — a factor-of-2 error in the direction that makes you set the trail too tight on exactly the tokens where you should be widest.
- Correlation between memecoins is regime-switching, not stationary. At rho=0 concurrency is nearly free (P(dd>50%) goes 17%->22% from n=1 to n=16); at rho=0.9 it is fatal (17%->99.7%). A backtest that assumes independent positions will drastically understate drawdown. Estimate rho live.
- Capping position COUNT instead of TOTAL exposure is the wrong control. Total growth-optimal exposure saturates at 1/rho times a single bet regardless of n; six positions at 2% each is roughly 1.8x over budget at rho=0.30.
- Selling above 5x is provably value-destroying on your own data (alpha=0.772 < 1), and it will feel correct every single time you do it, because 99.5% of the time the token is about to fall. The 0.5% of the time it doesn't is where the entire EV lives.
- A 12-loss streak is the base rate, not a regime break. A published 190-trade deployment hit exactly that while ending +117.7%. A circuit breaker that trips permanently at 8-10 losses will shut you off during normal operation.
- Left-skewed P&L on a right-skewed asset is a diagnosis, not a curiosity. The one published memecoin bot with disclosed statistics reports skewness -1.21 — the fingerprint of an exit policy that caps winners and lets losers run to a distant stop. Check your own P&L skew; if it is negative, your ladder is too heavy or your stop is too far.
- 82.8% of tokens that return >100% show engineered growth, and the manipulation sequence is wash-trading/LPI first, dump second. Your 2x runners are mostly operator-driven, so the exit is a discrete coordinated event rather than a diffusion — this raises the trail-gap probability g above what a diffusion model implies and is the main reason to keep SOME ladder rather than going pure-trail.
- Coordinated accounts hold 36.5% of supply on average (MELT dataset). A holder-concentration filter that only excludes pool addresses will pass tokens whose float is more than a third controlled by one coordinated cluster.
- Do not use secretary-problem or 1/e-law stopping rules here. They maximise the probability of stopping at the maximum over a finite exchangeable sequence — the wrong objective (it forces early selling) and the wrong assumptions (no hazard, no momentum, bounded horizon).
- Do not import the square-root law of market impact into an AMM. CPMM impact is exact, deterministic and path-independent; fitting a power law to it gives a strictly worse answer than the closed form.
- There is no execution-side remedy for a position too large for its pool. Because impact is path-independent, a 25%-of-reserve position costs ~20% slippage whether you dump it in one block or work it for an hour — and working it adds hazard exposure at ~18-36/hr. The cap must be enforced at entry.
- I could not verify the Kaminski & Lo stop-loss numbers (ScienceDirect 403; a guessed NBER working-paper URL resolved to an unrelated paper on Chinese exports, which I discarded). Do not let anyone cite specific stop-loss thresholds to you from that paper without the actual text. Likewise I did not extract the square-root-law prefactor from Toth et al.; treat any specific value as unverified.
- The 'median memecoin hold time is ~100s' figure in your brief is not something I could verify from any source. The closest verified numbers are winners' median hold 3.60 min and losers' median hold 0.60 min from a 190-trade deployment (arXiv:2606.08232) — which is a bot's holding policy, not the market's. Treat 100s as unsourced.
- My selection-lift parameter K is a modelling device, not a measurement. All absolute EV and ruin levels in this analysis scale with it. The RANKINGS of exit policies and the alpha-based rung placement are robust to K (the alpha rule is provably scale-invariant); the absolute f* and drawdown numbers are not. Measure K_hat before trusting any absolute figure here.
- My trail-width optimum rests on a stylised premature-stop model (stop point = M^b with b = min(1,(w/sigma)^2/T)). It is internally consistent — the b=1 boundary coincides exactly with the independently simulated median max-drawdown of sigma*sqrt(T) — but it is a model, not a measurement. Backtest the width sweep on your own tick data before hardcoding.

## Findings

### Your census implies a local Pareto exponent below 1 above 5x, so E[peak multiple] does not converge and is ~1.0 truncated at realistic caps

Piecewise log-log fit to your knots (1x,0.50),(2x,0.075),(5x,0.013),(50x,0.0022) gives alpha = ln(p_a/p_b)/ln(b/a) = 2.737 on [1,2], 1.913 on [2,5], 0.772 on [5,50]. Segment contributions to E[M]: 0.5515 + 0.1781 + 0.1520, plus tail beyond 50x of 0.0637 (cap 100x), 0.1384 (cap 200x), 0.3650 (cap 1000x). Totals: E[M]=0.945 / 1.020 / 1.247 at caps 100x/200x/1000x. Implied survival at intermediate levels: P(>=1.6x)=13.8%, P(>=2.5x)=4.89%, P(>=4x)=1.99%, P(>=8x)=0.905%, P(>=20x)=0.446%.

**Actionable:** Never compute an expected value over the peak distribution without an explicit truncation cap, and log which cap you used. Set M_CAP=200x as the default for backtests. Treat any backtest EV whose value changes by >20% when you move the cap from 100x to 1000x as unidentified.

`confidence=high` · source: https://arxiv.org/abs/2507.01963

### The exact marginal sell-vs-hold rule: hold iff alpha(m) < 1/(1 - theta_eff)

Conditional on the peak M>=m with local Pareto exponent alpha, E[M|M>=m] = m*alpha/(alpha-1) for alpha>1 and infinite for alpha<=1. If the residual exits at theta_eff * M where theta_eff = (1-g)*theta + g*rho (theta = trail retention, g = probability the trail gaps through, rho = recovery on a gap), then holding beats selling iff theta_eff * alpha/(alpha-1) > 1, i.e. alpha < 1/(1-theta_eff). Computed thresholds: theta_eff=0.780 -> 4.545 (hold everywhere); 0.681 -> 3.135 (hold everywhere); 0.571 -> 2.328 (sell <2x, hold above); 0.491 -> 1.965 (sell <2x); 0.486 -> 1.944. With your census alpha values (2.737 / 1.913 / 0.772) the crossover lands exactly at m=2x for any theta_eff in [0.55,0.62].

**Actionable:** Hardcode rungs only below 2.5x. Delete the 4x, 8x and 20x rungs entirely — they sell into the alpha<1 region where E[future peak] is unbounded. Re-estimate alpha(m) monthly from your own census and move the last rung to wherever alpha crosses 1/(1-theta_eff).

`confidence=high` · source: n/a

### The rule is scale-invariant: your selection edge determines whether to trade and how big, but NOT where to put the rungs

Multiplying the survival function P(M>=m) by a constant lift K leaves d ln S / d ln m unchanged. So alpha(m), and therefore the optimal rung placement, is invariant to how good your filter is. Verified in simulation: rung ordering is stable across selection lifts K=2,3,4.

**Actionable:** Tune rung LEVELS from the shape of the peak distribution (which is stable and estimable from 924 launches). Tune position SIZE from the level (which is unstable). Do not re-tune rungs when the filter changes.

`confidence=high` · source: n/a

### Your current ladder converts a 20x charted peak into a 4.14x realised return

Full CPMM simulation with 0.25% fee, position = 2% of quote reserve, price moved along the invariant by external flow between rungs. Realised multiples under your 40/25/15/10/5 @ 1.6/2.5/4/8/20 schedule with residual exited at 0.6*peak: peak 1.6x -> 1.174x realised; 2.5x -> 1.731x; 4x -> 2.271x; 8x -> 3.048x; 20x -> 4.136x. At 1% of quote reserve: 1.192 / 1.756 / 2.302 / 3.088 / 4.189.

**Actionable:** Log realised-vs-charted multiple on every trade. If your backtest reports peak multiples rather than realised proceeds/cost, it is overstating by 3-5x on the tail trades that carry the strategy.

`confidence=high` · source: n/a

### Only 7.3% of your policy's EV comes from tokens that reach 5x, versus 30% for a pure trail

400k-path simulation on the fitted distribution with a 15% trail-gap probability. Share of E[R] contributed by paths with M>=5x: CURRENT 5-rung 7.3%; first-rung-2.0x variant 8.4%; first-rung-1.35x 7.1%; 2-rung 30@1.5/20@3 + trail 19.0%; 15@1.4/15@2.5 + trail 23.8%; pure trail 30.7%. E[R] on the raw census (no selection lift): pure trail 0.955, 15@1.4/15@2.5 0.891, CURRENT 0.735.

**Actionable:** Add 'share of EV from trades with peak>=5x' as a first-class backtest metric alongside Sharpe. Target >=20%. If it is below 10% your exit policy is structurally mis-matched to the asset.

`confidence=high` · source: n/a

### A published Solana memecoin bot shows the exact pathology: left-skewed P&L on a right-skewed asset, with 1.6% of trades carrying all profit

arXiv:2606.08232 (Kamat), 190 paper trades over 15 days, Mar 29 - Apr 12 2026: win rate 40.5%, mean per-trade +0.62%, median -3.58%, skewness -1.21, excess kurtosis 6.61, cumulative +117.7%. Fragility table verbatim: remove top 1 -> +61.1%; top 3 -> -50.6%; top 5 -> -160.5%; top 10 -> -414.2%. 'Approximately 1.6 percent of trades generate 100 percent of cumulative profit.' Equity curve dipped to approximately -300% cumulative before recovering. Longest losing streak 12 consecutive trades.

**Actionable:** Expect and provision for 12+ consecutive losers. Do not let a circuit breaker trip permanently on a streak of that length — it is the base rate, not evidence of regime break. Compute the top-3-removal test on every backtest; if removing 1.6% of trades flips the sign, you have not measured an edge.

`confidence=high` · source: https://arxiv.org/abs/2606.08232

### Empirical hold times from that deployment: winners' median 3.60 minutes, losers' median 0.60 minutes

Verbatim from arXiv:2606.08232: 'In the deposited cohort, winners' median hold is 3.60 minutes and losers' median hold is 0.60 minutes; hold duration is systematically longer for winners.' Also: of 48 rejection events observed for at least six hours, 27 (56.25%) reached a 50% drawdown from reference; 26.0% of 4,874 forward samples were at or below half of reference price.

**Actionable:** Your 15-minute time stop is roughly 25x the empirical median losing hold. Cut the absolute backstop to 5-6 minutes and make the primary time stop flow-conditional (see numeric parameters).

`confidence=high` · source: https://arxiv.org/abs/2606.08232

### 82.8% of memecoins with >100% returns show evidence of artificial growth engineering

arXiv:2507.01963 examined 34,988 tokens across Ethereum, BNB Smart Chain, Solana and Base over three months. Verbatim: among tokens with returns exceeding 100%, 'an alarming 82.8% show evidence of artificial growth strategies designed to create a misleading appearance of market interest.' Manipulation types: wash trading, liquidity-pool-based price inflation, pump and dump, rug pull. Over 17,000 victimised addresses, realised losses >$9.3M. Temporal ordering: profit extraction (pump-and-dump, rug) typically FOLLOWS initial manipulation (wash trading / LPI).

**Actionable:** Treat reaching 2x as evidence of a coordinated operator, not organic demand — which strengthens the case for taking SOME size off below 2x, but also means the operator's exit is a discrete event, so your residual needs a fast trail rather than more rungs. The ordering result (wash trade -> LPI -> dump) is a usable live signal: a spike in wash-trade-like round-trip flow is a leading indicator of the dump.

`confidence=high` · source: https://arxiv.org/abs/2507.01963

### Constant-product AMMs are path-independent: slicing a sell into N pieces yields exactly identical proceeds

Verified numerically. Pool x=1e9 tokens, y=100 SOL, selling 2% of the token reserve. Zero fee: N=1 -> 1.960784 SOL; N=5 -> 1.960784; N=20 -> 1.960784; N=100 -> 1.960784 (diff 0.0000%). With 0.25% fee, slicing is marginally WORSE: N=1 -> 1.955978, N=100 -> 1.955931 (-0.0024%). This follows directly from the invariant xy=k being a state function.

**Actionable:** Delete any TWAP/time-sliced unwind logic that assumes slicing reduces impact in an AMM. It does not. Replace with percentage-of-volume: child_size = kappa * (observed buy volume in the previous interval), kappa in [0.20, 0.35]. Time to unwind = Q / (kappa * B) where B is the buy-flow rate.

`confidence=high` · source: n/a

### A round trip in an isolated CPMM costs only the fees, not twice the slippage — so 'slippage' is the wrong cost model for a hold

Buy q then immediately sell back, Raydium 0.25% each way: q = 0.5% of quote reserve -> round-trip loss 0.497%; 1.0% -> 0.494%; 2.0% -> 0.490%; 4.0% -> 0.480%. The price impact you pay on entry is recovered on exit because the invariant is conservative. The real cost is the wedge that opens when the price MOVES between entry and exit.

**Actionable:** Do not budget 2x one-way slippage as a round-trip cost — you will over-reject viable trades. Budget 2x fee + priority fee + tip + the multiple-dependent wedge below.

`confidence=high` · source: n/a

### The realised-vs-charted wedge grows with the exit multiple, because your tokens become a larger share of a shrinking token reserve

Exact CPMM, 0.25% fee, position sized as a fraction of the quote reserve, exit at multiple m. Haircut (1 - realised/charted): at 1% of quote reserve — 2.69% at 1.6x, 2.99% at 2.5x, 3.39% at 4x, 4.15% at 8x, 5.64% at 20x. At 2%: 4.80% / 5.36% / 6.11% / 7.55% / 10.27%. At 5%: 10.59% / 11.83% / 13.43% / 16.43% / 21.80%. At 10%: 18.81% / 20.85% / 23.39% / 27.96% / 35.58%. Mechanism: for xy=k, quote reserve scales as sqrt(price) while token reserve scales as 1/sqrt(price).

**Actionable:** Apply a multiple-dependent haircut in the backtest: haircut(m, s) ~= s*sqrt(m)/(1+s*sqrt(m)) to first order, where s = position/quote reserve. Do not use a flat slippage constant.

`confidence=high` · source: n/a

### Exact position cap for a target one-way slippage: P_max = y_quote * s/(1-s)

For a CPMM sell of T tokens into reserve x, average execution slippage versus mid = r/(1+r) with r = T/x; post-trade spot impact = 1 - 1/(1+r)^2 ~= 2r. In quote terms with position value P at mid and quote reserve y, slippage = P/(y+P). Table: target 0.5% -> P_max = 0.50% of quote reserve = 0.25% of TVL; 1% -> 1.01% / 0.51%; 2% -> 2.04% / 1.02%; 3% -> 3.09% / 1.55%; 5% -> 5.26% / 2.63%. Measured average slippage at r = 0.5/1/2/3/5/10/20%: 0.50/0.99/1.96/2.91/4.76/9.09/16.67%, with spot impact 0.99/1.97/3.88/5.74/9.30/17.36/30.56%.

**Actionable:** Your '2% of pool depth' is ambiguous and probably too loose. If depth means quote reserve you are accepting ~2% one-way slippage and a 4.8-10.3% round-trip wedge. Recommend 1.0% of quote reserve (0.5% of TVL) as the default cap, with the cap tightened for higher-target trades since the wedge grows with m.

`confidence=high` · source: n/a

### Trailing stop width has an interior optimum at w* ~= sigma_bar * sqrt(T_rem), derived by making both stop probability and stop point endogenous

Simulated maximum drawdown from a running max for a driftless random walk, in units of per-bar sd, for target premature-stop probabilities [50%,35%,20%,10%]: T=5 -> [1.28,1.73,2.34,3.02]; T=10 -> [2.37,3.00,3.90,4.89]; T=15 -> [3.20,4.00,5.10,6.28]; T=30 -> [5.06,6.18,7.76,9.47]; T=60 -> [7.71,9.30,11.52,13.93]; T=120 -> [11.39,13.60,16.76,20.26]. Median maxDD ~= 1.0*sigma*sqrt(T); the 20% quantile fit is c*sqrt(T) with c rising 1.05->1.53 as T goes 5->120. Modelling a noise stop as capturing M^b with b = min(1, (w/sigma)^2/T) (the fraction of the log-run completed before the drawdown occurs) produces an interior optimum exactly where b hits 1, i.e. w = sigma*sqrt(T). Verified: sigma=4%/T=30 (sigma*sqrt(T)=21.9%) optimum at w=20-25%; sigma=8%/T=15 (31%) optimum at 25-30%; sigma=8%/T=30 (43.8%) optimum at 40-50%.

**Actionable:** Replace the fixed 45%->22% schedule with w = clamp(1.0 * sigma_bar * sqrt(T_rem), 0.18, 0.60), sigma_bar = EWMA of per-minute log-return sd (halflife 5 bars), T_rem = 20 bars default. The optimum sits at a premature-stop probability of 30-50%, which is much higher than intuition suggests — being stopped out early on a third of your runners is correct.

`confidence=medium` · source: n/a

### ATR conversion constant: E[True Range] = 1.54 * sigma_bar * price for continuously observed bars, but collapses to 0.82 with sparse ticks

Simulated: E[TrueRange]/sigma_bar = 1.5382 with 400 ticks/bar (theory for a Brownian range is sqrt(8/pi) = 1.5958); E[|close-open|]/sigma = 0.7975. With sparse sampling: 60 ticks/bar -> 1.4522; 12 ticks/bar -> 1.2856; 4 ticks/bar -> 1.0635; 2 ticks/bar -> 0.8168. So w* = sigma*sqrt(T) translates to w* = 0.65*sqrt(T) ATR units: T=15 -> 2.5x ATR, T=30 -> 3.6x ATR, T=60 -> 5.0x ATR.

**Actionable:** If you use ATR, correct it for tick sparsity or you will systematically under-estimate volatility on exactly the illiquid tokens where you most need a wide trail. Safer: compute sigma_bar directly from log returns of trade prints rather than from bar ranges.

`confidence=high` · source: n/a

### Your trail scaling direction is likely backwards: it tightens as the peak rises, but the correct width scales with realised volatility, which rises with the multiple

w* = sigma_bar*sqrt(T_rem). A token that has run 20x has higher per-minute realised vol and a longer remaining run than one at 1.5x, so w* should widen. Your schedule tightens 45%->22%. The counter-argument (protect a larger absolute gain) is a risk-preference argument, not a growth-optimality one, and it is already handled by the fact that the residual is a smaller fraction of the position after the rungs.

**Actionable:** Testable on your own census: regress per-minute realised log-return sd on log(current multiple). If the slope is positive (expected), invert the tightening schedule. Until measured, use the vol-scaled formula, which handles it automatically.

`confidence=medium` · source: n/a

### Tightening the base trail is worth more than any ladder change: 45% -> 20% moves the current policy from -1bp to +496bp log-growth per trade

400k paths, selection lift K=2.5, 15% gap probability, exogenous gap. CURRENT 5-rung ladder by base trail width: 20% -> E[R]=1.206, f*=0.54, g*=495.9bp; 25% -> 1.159, 0.42, 286.2bp; 30% -> 1.111, 0.27, 127.8bp; 35% -> 1.064, 0.13, 37.2bp; 45% -> 0.979, 0.01, -1.1bp. Light 2-rung policy: 15% -> 1.616/0.68/1028.6bp; 20% -> 1.551/0.59/742.8bp; 25% -> 1.490/0.47/494.3bp; 35% -> 1.372/0.21/175.7bp; 45% -> 1.268/0.08/57.3bp. Note: this sweep holds gap probability fixed, which overstates the benefit of tightening; the endogenous version (previous finding) puts the optimum at sigma*sqrt(T), typically 20-40%.

**Actionable:** If you change one thing, change the trail width. But use the endogenous optimum (sigma*sqrt(T)), not the fixed-gap sweep, which is biased toward tight trails.

`confidence=high` · source: n/a

### The trail should arm at entry, not after the first rung

Your policy leaves the position protected only by a -45% hard stop between entry and 1.6x. Given the fitted distribution, 86.2% of positions never reach 1.6x, so the overwhelming majority of your positions spend their entire life with no trail at all and a 45% hole beneath them. Median peak is 1.000x, meaning at least half of tokens never tick above entry.

**Actionable:** Arm the trail from the fill, anchored to the running high of executable (post-impact) price, not mid. The hard stop then becomes a gap backstop only.

`confidence=high` · source: n/a

### Under a tight trail the -45% hard stop is dead code, and tightening it to -25% is free

Sweep of the hard stop with the recommended policy at a 25% trail, K=2.5, 400k paths: -50% -> E[R]=1.494, f*=0.47, g*=499.0bp; -45% -> 1.492/0.47/499.5bp; -35% -> 1.492/0.47/502.0bp; -30% -> 1.488/0.47/498.7bp; -25% -> 1.492/0.47/502.5bp; -20% -> 1.494/0.47/498.2bp; -15% -> 1.498/0.48/510.1bp. Flat to within noise, because a 25% trail from the entry-anchored high always fires first.

**Actionable:** Tighten the hard stop to -25%. It costs nothing in the model and materially reduces single-block gap exposure. Keep it as a backstop that only fires when the trail is jumped.

`confidence=high` · source: n/a

### First rung at 1.6x is roughly the right LEVEL but the 40% weight is 2-4x too heavy

Weight sweep at 1.5x with a 15% second rung at 2.5x, 25% trail, K=2.5, 400k paths: w1=0% -> E[R]=1.695, f*=0.35, g*=434.5bp; 10% -> 1.648/0.48/576.6bp; 20% -> 1.578/0.48/542.5bp; 30% -> 1.494/0.47/498.3bp; 40% -> 1.411/0.46/451.8bp; 50% -> 1.334/0.46/406.2bp; 60% -> 1.252/0.45/343.8bp; 70% -> 1.172/0.42/265.1bp. Log-growth peaks at w1=10-20%. Note the non-monotonicity: some early de-risk beats none (f* jumps 0.35 -> 0.48) because it raises the Kelly-feasible size.

**Actionable:** Cut the first rung to 15-25% at 1.5x. The purpose of the first rung is not cost recovery, it is to raise the Kelly-feasible position size by truncating the left tail. A 40% sale does far more than that job requires.

`confidence=high` · source: n/a

### Moving the first rung LATER (to 2.0x) is worse than leaving it at 1.6x, and moving it earlier (1.35x) is better

400k paths, gap 15%, L=hard-stop-consistent: first rung at 2.0x gives E[R]=0.6638 (g=15%), at 1.6x gives 0.6740, at 1.35x gives 0.6866, holding weights constant. Reason: P(reach 1.6x)=13.8% vs P(reach 2.0x)=7.5%, so raising the rung forfeits de-risking on the 6.3% of tokens peaking between 1.6x and 2.0x. This is orthogonal to the alpha rule, which governs the UPPER rungs.

**Actionable:** Answer to your question 'is 1.6x too early or too late': the level is slightly too LATE (1.4-1.5x is better) and the size is much too LARGE. Do not move it up.

`confidence=high` · source: n/a

### Kelly f* for this payoff swings from 0.001 to 0.96 across a plausible range of selection quality — a 1000x range

Full-Kelly optimiser over the simulated payoff distribution, light 2-rung policy, 15% gap. Selection lift K (multiplier on the whole upside survival curve): K=1 -> f*=0.001, g*=-1.1bp, E[R]=0.893; K=2 -> f*=0.069, g*=47.4bp, E[R]=1.242; K=3 -> f*=0.502, g*=564.4bp, E[R]=1.570; K=4 -> f*=0.960, g*=1817.3bp, E[R]=1.874. For the CURRENT policy: K=1 -> f*=0.001; K=2 -> f*=0.001 (E[R]=0.961, still a loser); K=3 -> f*=0.389; K=4 -> f*=0.785.

**Actionable:** Never size off a point estimate of f*. Size off the LOWER end of a posterior over K. Concretely: compute f* at your 20th-percentile estimate of the tail rates and then apply the fractional multiplier on top of that.

`confidence=high` · source: n/a

### The tail rate that dominates your EV is estimated from 2 events

n=924. P(>=2x)=0.075 -> ~69 events, SE 0.00866, relative SE 11.6%, 95% CI [0.0580, 0.0920], hi/lo = 1.6x. P(>=5x)=0.013 -> ~12 events, SE 0.00373, relSE 28.7%, CI [0.0057, 0.0203], hi/lo = 3.6x. P(>=50x)=0.0022 -> ~2 events, SE 0.00154, relSE 70.1%, normal CI includes zero. Exact Poisson 95% CI for k=2 is [0.24, 7.22] events, i.e. a rate range of [0.00026, 0.00781] — a 30x span. Since E[M] is dominated by the alpha=0.772 segment, the EV estimate inherits that 30x uncertainty.

**Actionable:** This, not sampling noise in the win rate, is the reason to use fractional Kelly. Note the classical result that sampling error alone implies shrinkage of only 1/(1+1/n) ~= 1; the justification for a deep haircut is model/nonstationarity error, which must be encoded as a prior sd, not estimated from the sample.

`confidence=high` · source: n/a

### Chopra-Ziemba: errors in estimated MEANS are roughly 20x more costly than errors in variances and 2x more than covariance errors

From MacLean, Thorp & Ziemba, 'Good and bad properties of the Kelly criterion' (2010), Table 1, sourced to Chopra & Ziemba (1993): average ratio of certainty-equivalent loss for errors in means vs covariances vs variances, at risk tolerance 25 / 50 / 75 = 5.38, 3.22, 1.67 / 22.50, 10.98, 2.05 / 56.84, 21.42, 2.68, converging to the 20:2:1 rule. Verbatim from the paper: 'Given the extreme sensitivity of E log calculations to errors in mean estimates, these estimates must be accurate and to be on the safe side, the size of the wagers should be reduced.'

**Actionable:** Your mean is the least identified parameter you have (see previous finding) and the most damaging to get wrong. This is the formal justification for c well below 0.5.

`confidence=high` · source: https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf

### Betting exactly 2x Kelly reduces the growth rate to the risk-free rate; the growth curve is g(c) = (2c - c^2) * g_max

From MacLean/Thorp/Ziemba appendix, attributed to Thorp (1997), Stutzer (1998) and Janacek (1998), with a CAPM proof due to Markowitz: with g_p = E_p - V_p/2, X = (E_M - r_0)/sigma_M^2 is the Kelly bet with g* = r_0 + (1/2)[(E_M - r_0)/sigma_M]^2; substituting Y = 2X gives g_0 - r_0 = 0. Growth table: c=0.10 -> 0.190 g*; 0.20 -> 0.360; 0.25 -> 0.438; 0.33 -> 0.551; 0.50 -> 0.750; 0.75 -> 0.938; 1.00 -> 1.000; 1.50 -> 0.750; 2.00 -> 0.000; 2.50 -> -1.250. c and 2-c give identical growth, but wildly different drawdowns.

**Actionable:** The growth curve is flat near the top: c=0.5 costs only 25% of growth, c=0.33 costs 45%. Given that your f* estimate has a 1000x range, the asymmetry is decisive — underbetting by 5x costs the same growth as overbetting by 1.8x, but P(ever -50%) is 0.20% at c=0.2 versus 92.6% at c=1.8.

`confidence=high` · source: https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf

### Fractional-Kelly drawdown law verified to 3 decimal places: P(wealth ever falls to fraction x) = x^((2-c)/c)

Derivation: with f = c*f* and f* = mu/sigma^2, log-wealth is BM with drift nu = f*mu - f^2*sigma^2/2 and vol s = f*sigma, so 2*nu/s^2 = 2/c - 1 = (2-c)/c, and P(BM with drift ever hits -a) = exp(-2*nu*a/s^2). Verified by exact Brownian-bridge-corrected Monte Carlo (a naive Euler scheme UNDERSTATES this by ~45% because it misses intra-step minima): c=1.00, x=0.8 -> simulated 0.7995 vs formula 0.8000; x=0.5 -> simulated 0.4989 vs formula 0.5000. Table of P(ever draw down to x) by Kelly fraction c: c=1.00 -> 80.0%/65.0%/50.0%/20.0% for x=0.80/0.65/0.50/0.20; c=0.50 -> 51.2%/27.5%/12.5%/0.80%; c=0.33 -> 32.3%/11.3%/3.00%/0.029%; c=0.25 -> 21.0%/4.90%/0.78%/0.001%; c=0.20 -> 13.4%/2.07%/0.195%/~0; c=0.15 -> 6.38%/0.49%/0.019%/~0; c=0.10 -> 1.44%/0.028%/~0/~0.

**Actionable:** Use this to pick c from a drawdown budget directly: c = 2 / (1 + ln(beta)/ln(x)) for a target P(ever below x) = beta. Example: 'never worse than 35% drawdown with 95% confidence' -> x=0.65, beta=0.05 -> c = 2/(1 + ln(0.05)/ln(0.65)) = 0.19. Your c=0.20 is almost exactly this budget — so 0.20 is defensible IF f* is known. It is not (see the 1000x range finding).

`confidence=high` · source: n/a

### Busseti-Ryu-Boyd: a convex drawdown constraint strictly dominates fractional Kelly at equal risk

arXiv:1603.06183. Exact bound (their eq. 6): for lambda > 0 and alpha, beta in (0,1) with lambda = log(beta)/log(alpha), E[(r^T b)^(-lambda)] <= 1 implies Prob(W_min < alpha) < beta. This single constraint bounds the ENTIRE CDF of W_min: Prob(W_min < alpha) < alpha^lambda for all alpha in (0,1). The risk-constrained Kelly program is: maximise E log(r^T b) s.t. 1^T b = 1, b >= 0, E[(r^T b)^(-lambda)] <= 1 — convex, since (r^T b)^(-lambda) is convex in b. Numerical results (Table 1, finite outcomes): full Kelly growth 0.062 with Prob(W_min<0.7) = 0.397; RCK lambda=6.456 growth 0.043 with actual risk 0.073 (bound 0.100); RCK lambda=5.500 growth 0.047 with risk 0.099. Table 3 (lognormal, n=20): full Kelly growth 0.077 with Prob(W_min<0.7) = 0.569; RCK lambda=6.456 growth 0.039, risk 0.080. Verbatim on the comparison: 'the Kelly fractional bet that achieves our risk bound 0.1 has a growth rate around 0.035, compared with RCK, which has a growth rate 0.047.'

**Actionable:** Full Kelly has a 40-57% chance of a 30% drawdown. That is the number to quote when someone asks why you are not at full Kelly. Implement the RCK constraint E[(1 + f(R-1))^(-lambda)] <= 1 with lambda = ln(beta)/ln(alpha) as a hard sizing gate — it is a one-line check over your simulated payoff sample and it dominates fractional Kelly by ~34% of growth at equal drawdown risk in their lognormal case.

`confidence=high` · source: https://arxiv.org/abs/1603.06183

### lambda values for the RCK constraint, and what they imply for the whole drawdown CDF

lambda = log(beta)/log(alpha). Target P(dd>30%) < 10% -> lambda = 6.456, which also implies P(dd>50%) < 1.14% and P(dd>80%) < 3.07e-3%. Target P(dd>20%) < 10% -> lambda = 10.319 -> P(dd>50%) < 0.078%. Target P(dd>50%) < 5% -> lambda = 4.322. Target P(dd>30%) < 5% -> lambda = 8.399 -> P(dd>50%) < 0.296%. In the heavy-risk-aversion limit lambda -> infinity the constraint reduces to r^T b >= 1 almost surely (risk-free bets only); as lambda -> 0 it vanishes and RCK reduces to plain Kelly.

**Actionable:** Hardcode lambda = 6.456 (P(dd>30%) < 10%) as the default risk-aversion parameter. Note the Monte Carlo realised risk in their experiments came in at 0.073-0.080 against a 0.100 bound, i.e. the bound is conservative by ~25-35%.

`confidence=high` · source: n/a

### Concurrency: total exposure under equicorrelation saturates at 1/rho times a single bet, regardless of how many positions you open

For n identical bets with equicorrelation rho, the growth-optimal weight per asset is f_i = (mu/sigma^2)/(1 + (n-1)rho), so total = n*f_i and the effective number of independent bets is n_eff = n/(1 + (n-1)rho). Table of n_eff: n=2 -> 2.00/1.74/1.54/1.33/1.18 for rho=0/0.15/0.30/0.50/0.70; n=4 -> 4.00/2.76/2.11/1.60/1.29; n=6 -> 6.00/3.43/2.40/1.71/1.33; n=12 -> 12.00/4.53/2.79/1.85/1.38; n=20 -> 20.00/5.19/2.99/1.90/1.40. Saturation: 6.7x at rho=0.15, 3.3x at rho=0.30, 2.0x at rho=0.50, 1.4x at rho=0.70.

**Actionable:** Cap TOTAL simultaneous exposure at f_single/rho_hat, not the position COUNT. At rho=0.30 and f_single=2%, total should not exceed 6.7% — your 6 positions at 2% each is 12%, roughly 1.8x over. Six concurrent positions is the right ORDER of magnitude at rho~0.3 (n_eff = 2.4, and the marginal n_eff gain from 6 to 12 is only 0.39) but the per-position size must come down.

`confidence=high` · source: n/a

### Correlation, not position count, drives your drawdown risk once n > 3

1000-trade simulation, f=2% per position, light policy, common-factor correlation model, P(drawdown > 50%): n=1 -> 17.0/17.1/17.2/17.5/16.6% for rho=0/0.15/0.30/0.60/0.90; n=3 -> 20.8/21.4/24.3/38.6/58.1%; n=6 -> 21.7/23.4/30.8/58.5/84.7%; n=10 -> 20.9/24.6/37.5/73.2/97.2%; n=16 -> 22.1/28.4/44.6/86.7/99.7%. At rho=0 concurrency is nearly free (17% -> 22% going from 1 to 16 positions). At rho=0.9 it is catastrophic (17% -> 99.7%).

**Actionable:** Estimate rho live from the realised correlation of your open positions' 1-minute returns and scale total exposure by 1/rho_hat dynamically. Memecoin correlation is regime-switching: near zero in calm markets, near one during a SOL-wide risk-off or when a launchpad meta dies. Add a kill switch: if realised cross-position correlation over the last 30 minutes exceeds 0.6, halve total exposure.

`confidence=medium` · source: n/a

### Mis-specification ruin: your current policy needs ~3x better base rates than the census to survive; the light policy survives at 1.5x

1000 trades, n=6, rho=0.30. Sized as if the edge were real, simulated at the true selection lift K. LIGHT policy (30@1.5/15@2.5, 25% trail) at f=2%/position (12% total): K=1.0 -> median W = 3.3e-5, P(dd>50%)=99.9%; K=1.5 -> median 1.95e3, P(dd>50%)=29.7%, P(dd>80%)=5.8%; K=2.0 -> median 9.1e10, P(dd>50%)=0.0%. CURRENT policy (5-rung, 35% trail) at f=2%: K=1.0 -> P(dd>50%)=100%; K=1.5 -> 100%; K=2.0 -> median 0.0044, P(dd>50%)=100%, P(dd>80%)=99.7%; only at K=3.0 does it work. At f=0.5%/position the light policy survives K=1.5 with P(dd>50%)=0.3%.

**Actionable:** Set f so that you survive K=1.5, not K=3. That means roughly 0.5-1.0% of bankroll per position at 6 concurrent, i.e. 3-6% total, not 12%. The simulation's absolute levels depend on the selection-lift model, but the RANKING and the break-even K are robust.

`confidence=medium` · source: n/a

### Break-even selection lift: current policy needs K ~= 2.2, the light 2-rung policy needs K ~= 1.35

E[R] by selection lift K, gap 15%, trail 35%: K=1 -> current 0.756, light(15@1.4/15@2.5) 0.915, pure trail 0.968. K=1.5 -> 0.860 / 1.093 / 1.169. K=2 -> 0.965 / 1.273 / 1.372. K=3 -> 1.154 / 1.609 / 1.753. K=4 -> 1.317 / 1.917 / 2.108. K=6 -> 1.598 / 2.446 / 2.768.

**Actionable:** This is the cleanest single statement of the cost of your current exit policy: it requires your filter to be roughly 60% better than the light policy requires, for the same trade to be viable. Measure your actual K by comparing your traded cohort's peak-multiple survival curve to the 924-launch census — this is a direct, cheap measurement you should already be able to make.

`confidence=high` · source: n/a

### The secretary problem and the odds algorithm do NOT apply to this exit and should not be used

Bruss's odds theorem: 'The odds strategy is optimal, that is, it maximizes the probability of stopping on the last 1. The win probability of the odds strategy equals w = Q_s R_s. If R_s >= 1, the win probability w is always at least 1/e = 0.367879..., and this lower bound is best possible.' These results maximise the PROBABILITY of stopping at the maximum, over a known-length sequence of exchangeable observations with no recall. Your problem has (a) unbounded horizon, (b) a payoff linear in the price rather than an indicator of hitting the max, (c) strong positive dependence (momentum) rather than exchangeability, and (d) a hazard of the asset going to zero. Maximising P(sell at the top) is the wrong objective: it would have you sell far too early, because in a power-law regime the correct policy accepts a low probability of catching the top in exchange for capturing the tail.

**Actionable:** Do not implement a 1/e or odds-algorithm observation phase. The correct framework is optimal stopping of a killed diffusion, which yields the alpha(m) threshold rule above.

`confidence=high` · source: https://en.wikipedia.org/wiki/Odds_algorithm

### The correct optimal-stopping criterion for a rising asset with a hazard of going to zero, and why it justifies cost recovery under LOG utility but not under linear utility

Model the price as dS/S = mu dt + sigma dW with a Poisson jump at rate lambda to a fraction rho of the current price. Under linear utility on the position, holding an extra dt is worth mu*dt - lambda*(1-rho)*dt, so HOLD iff mu > lambda*(1-rho). Under LOG utility applied to the position itself, the growth rate of holding is (mu - sigma^2/2) - lambda*ln(1/rho), which tends to minus infinity as rho -> 0: with a genuine jump-to-zero hazard, log utility says never hold ANY of it. The resolution is that log utility applies to PORTFOLIO wealth, not to the position: once the position is a small enough fraction of the bankroll, its contribution to portfolio log-wealth is approximately linear and the linear rule mu > lambda*(1-rho) governs. THIS is the rigorous justification for a cost-recovery rung — it is not about 'getting your money back', it is about shrinking the position until the linear criterion legitimately replaces the log criterion.

**Actionable:** Size the first rung by the rule: sell enough that the residual's total loss moves portfolio log-wealth by less than epsilon. With f = 2% of bankroll and a 1% log-wealth tolerance, the residual can be up to ~50% of the position, so a 15-25% first rung is already sufficient. A 40% rung is over-insurance.

`confidence=high` · source: n/a

### Killed-GBM inversion links your measured alpha directly to the kill hazard, and alpha < 1 is exactly the condition 'drift beats hazard'

For log-price a BM with drift nu = mu - sigma^2/2 and vol sigma, killed at rate lambda, the probability of ever reaching level m is P(peak >= m) = m^(-alpha) with alpha = (sqrt(nu^2 + 2*lambda*sigma^2) - nu)/sigma^2. Rearranging, alpha < 1 <=> lambda < mu. So the empirical fact that alpha = 0.772 on [5x,50x] is a direct statement that, conditional on surviving to 5x, the drift of the surviving cohort exceeds its kill hazard. Inverting with sigma = 300%/hr and nu = 1/hr: alpha=2.737 -> lambda ~= 36/hr (median life ~1.1 min); alpha=1.913 -> lambda ~= 18/hr (~2.3 min); alpha=0.772 -> lambda ~= 3.5/hr (~12 min). The absolute hazards are sensitive to the assumed sigma, but the ORDERING — a ~10x drop in hazard between the sub-2x cohort and the above-5x cohort — is not.

**Actionable:** Survivorship is your friend above 5x and your enemy below 2x. This is the mechanism behind the alpha rule: the tokens that have already run have self-selected into a lower-hazard cohort. Encode it as: time stop should be aggressive at low multiples and relaxed at high multiples — the opposite direction from your trail schedule.

`confidence=medium` · source: n/a

### Theory supports a HYBRID (limit orders plus trailing stop), not a pure trail and not a pure ladder

Leung & Hou, 'Optimal Trading with a Trailing Stop' (arXiv:1701.03960), poses the liquidation as a double stopping problem with a random path-dependent maturity under a general linear diffusion (exponential Ornstein-Uhlenbeck in their empirical case) and establishes 'the optimality of using a sell limit order in conjunction with the trailing stop.' No closed-form optimal distance is given; the optimal acquisition and liquidation regions are computed numerically. Related: 'When to Sell Amid Anxiety About Drawdowns' (arXiv:2006.00282) finds stop-loss and trailing stops emerge as optimal components depending on drawdown-anxiety tolerance.

**Actionable:** Your hybrid architecture is correct; only the weights are wrong. Keep limit-order rungs (they fill on the way up with zero adverse selection) plus a trail on the residual. Do not switch to a pure trail — the simulation shows the hybrid beats pure trail on log-growth at every K >= 2, because the early rung raises the Kelly-feasible position size.

`confidence=medium` · source: https://arxiv.org/abs/1701.03960

### Kaminski & Lo's stop-loss result exists and is the right theoretical reference, but I could not read the text and will not quote numbers from it

'When do stop-loss rules stop losses?', Journal of Financial Markets, 2013, DOI 10.1016/j.finmar.2013.07.001 (earlier SSRN versions: 10.2139/ssrn.968338 (2007), 10.2139/ssrn.972612 (2010)). ScienceDirect returned HTTP 403 and my guess at an NBER working-paper number resolved to an unrelated paper on Chinese exports, which I discarded. The qualitative result I am confident of on general grounds — a stop-loss rule adds expected return only when returns exhibit momentum/negative drift persistence, and strictly subtracts expected return under an i.i.d. random walk with positive drift — but I am NOT quoting their thresholds, windows or measured improvements because I could not verify them.

**Actionable:** Buy or otherwise obtain the paper before relying on it. The practical implication for you is unaffected: memecoin minute-scale returns are demonstrably NOT an i.i.d. random walk with positive drift (your own alpha structure proves momentum conditional on survival), so stops are justified — but justify them from your own alpha(m) measurement, not from a citation you cannot read.

`confidence=low` · source: https://doi.org/10.1016/j.finmar.2013.07.001

### The square-root law of market impact is irrelevant here and should not be imported

Toth et al., 'Anomalous price impact and the critical nature of liquidity in financial markets', Phys. Rev. X 1, 021006 (2011), arXiv:1105.1694. I fetched the abstract page; it confirms the paper 'explains the square-root impact law' and describes a V-shaped latent supply/demand profile vanishing around the current price, but the abstract page does NOT contain the formula and I did not extract it, so I am not quoting a prefactor or exponent. More importantly it does not apply: the square-root law describes metaorder impact in continuous double-auction markets with latent liquidity and impact decay. A constant-product AMM has fully visible, deterministic, path-independent liquidity with NO impact decay. Your impact is exactly r/(1+r), not a power law.

**Actionable:** Use the exact CPMM formulas. Do not fit a square-root impact model to AMM fills — you will get a worse answer than the closed form you already have. The square-root law becomes relevant only if you route across multiple venues including CLOBs.

`confidence=high` · source: https://arxiv.org/abs/1105.1694

### Corroborating base rates from independent datasets are consistent with your census being representative

arXiv:2512.11850 (Mancino, IEEE ISCC 2025): Pump.fun accounted for up to 71.1% of all tokens minted on Solana in Q4 2024 and 40-67.4% of DEX transactions; 'less than 2% of tokens successfully migrated to major decentralized exchanges'; daily active users 60k baseline to 260k peak. arXiv:2608.20271 ('Catching the Rug'): 6.4 million tokens over 7 months on Solana; 'a vast majority of these memecoins exhibit rug pull characteristics within one hour of launch'; XGBoost on the first 5 minutes of trading data achieves robust detection. arXiv:2602.13480 (MELT): 41k+ Solana launchpad launches, 200M+ transactions, 122 behavioural features; 'on average, 36.5% of token supply is held by coordinated accounts.' arXiv:2601.22185 (MemeChain): 34,988 tokens cross-chain; '5.15% cease all trading within 24 hours of launch.'

**Actionable:** The 36.5%-coordinated-supply figure is directly relevant to your holder-concentration filter: if you exclude only pool addresses, you are missing coordinated clusters that hold on average more than a third of supply. Cluster-detect (funding-graph / co-timing) rather than counting raw top-N holders.

`confidence=high` · source: https://arxiv.org/abs/2602.13480

### Time-stop calibration: replace the 15-minute clock with a flow condition, with a 5-6 minute absolute backstop

Justification chain: (a) the linear hold criterion is mu > lambda*(1-rho), and both mu and lambda are estimable in real time from flow, not from the clock; (b) empirically, losers' median hold is 0.60 min and winners' 3.60 min (arXiv:2606.08232), so a 15-minute clock is ~25x the loser median and does essentially nothing; (c) the killed-GBM inversion implies the hazard at low multiples is ~18-36/hr, i.e. a median residual life of 1-2.5 min, so by 15 minutes an unmoved token is overwhelmingly likely already dead; (d) your existing '+12% escape' makes the clock conditional on price, but price is a lagging proxy for the flow that actually drives the hazard.

**Actionable:** Implement: exit if (net buy volume in the last 60s) < 0.25 * (trailing 5-min median net buy volume) OR (unique buying wallets in the last 60s) <= 1, evaluated every 10s starting 60s after fill. Absolute backstop 6 minutes regardless. Drop the +12% escape clause — replace it with 'the flow test is waived while the trail is more than 1 sigma_bar above the entry price'.

`confidence=medium` · source: n/a

### The correct AMM unwind schedule is percentage-of-volume, and it tells you the maximum position you can hold given a time budget

Because CPMM impact is path-independent, the only mechanism that reduces your realised impact is other people's buy flow arriving between your child orders. Time to unwind = Q/(kappa*B) where Q is the position (in quote terms), B the buy-flow rate, kappa the participation rate. Worked table at kappa=0.25: position 2% of quote reserve with buy flow 2%/min -> 4.0 min; 10%/min -> 0.8 min; 30%/min -> 0.3 min. Position 5% -> 10.0 / 2.0 / 0.7 min. Position 10% -> 20.0 / 4.0 / 1.3 min. Position 25% -> 50.0 / 10.0 / 3.3 min.

**Actionable:** Invert this into an entry-time constraint: max_position = kappa * B_observed * T_unwind_budget. With kappa=0.25, T_budget=90s, and measured buy flow B, that is a live, per-token size cap that automatically tightens as a token goes quiet. This is strictly better than a static % of pool depth, because pool depth does not tell you whether anyone is still buying. Combine as: size = min(kelly_size, 0.010*quote_reserve, 0.25*B*90s).

`confidence=high` · source: n/a

### A token too large for its pool cannot be unwound at any speed — the loss is a pure function of size, so the only fix is at entry

Direct corollary of path independence. Selling a position worth 25% of the quote reserve costs 20% average slippage whether you do it in one block or over an hour, absent new buy flow. Halting and 'waiting for liquidity' is a bet that buy flow arrives before the price decays, which for a dying memecoin is a losing bet given a hazard of ~18-36/hr.

**Actionable:** If you find yourself holding more than ~5% of the quote reserve, dump it immediately in one transaction rather than working it — the impact is identical and waiting adds hazard exposure plus fee drag from extra transactions. Enforce the cap at entry; there is no execution-side remedy.

`confidence=high` · source: n/a
