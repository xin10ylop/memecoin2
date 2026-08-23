# Research dossiers

Seventeen structured research reports, ~1.2 MB, produced by parallel agents that
between them ran roughly 1,350 web searches and fetches. Each dossier carries an
executive summary, numeric parameters intended to be hardcoded, concrete API
endpoints with pricing and rate limits, findings with confidence ratings and
sources, and a list of traps.

| file | subject |
|---|---|
| `01_latency_infra.md`, `05_latency_infra_b.md` | Priority fees, Jito tips, compute budgets, detection paths, MEV — with live on-chain measurements |
| `02_execution_platforms.md` | Photon, Axiom, Trojan, GMGN, Maestro, BonkBot and the rest, teardown by teardown; why self-hosting wins |
| `03_data_apis.md` | Every market-data API with auth, cost, limits and history depth |
| `04_quant_literature.md` | Published base rates, survival curves, rug fractions, trader profitability |
| `06_practitioner_methods.md`, `06b_practitioner.md` | What profitable traders actually do, with numbers; real shared screener presets |
| `07_social_alpha.md`, `12_social_ingest.md` | X and Telegram ingestion economics, and whether social alpha is real |
| `08_exit_sizing.md`, `08b_exit_sizing.md` | Optimal exits and sizing for power-law payoffs; the Pareto analysis that reshaped our exit policy |
| `09_ml_features.md`, `09b_ml_features.md` | Predictive features and modelling for early-token outcomes |
| `10_rug_detection.md` | Rug, honeypot and bundle detection, with exact thresholds and calls |
| `11_smart_money.md` | Wallet PnL tracking, copy-trading, alpha persistence and decay |
| `13_backtest_methodology.md` | How to backtest this without fooling yourself |
| `14_ops_security.md` | Wallet architecture, key handling, tax and regulatory posture, catastrophic failure modes |

**How to read them.** These are inputs, not conclusions. Several claims in here
were tested against this system's own data and did not survive — a published
35–110x deployer-reputation lift measured 2.09x walk-forward, and a claim that
trade count "mostly measures bots" is contradicted outright by our census, where
it is the single strongest predictor. Where a dossier and our own measurement
disagree, `docs/RESEARCH.md` records which won and why.
