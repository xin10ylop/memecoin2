"""Event-driven backtester over the snapshot panel.

Design commitments that keep the result honest:

* **No mid-price fills.** Every buy and sell goes through `sim.amm`, priced off
  the pool reserves reconstructed from the liquidity and price recorded in the
  snapshot. Your own order moves the pool and the next sell sees the moved pool.
* **Latency.** A signal at time t executes against the first observation at or
  after t + latency. Nothing fills at the price that triggered it.
* **Exit liquidity is a constraint, not an assumption.** Selling is capped at a
  fraction of the token reserve; a position too large for the pool is
  liquidated over successive observations at progressively worse prices, which
  is exactly what happens in practice.
* **The full cost stack** from `sim.costs` applies to every transaction, and
  failed transactions are priced in.
* **Only forward information.** Entry decisions call the same `features_at`
  the live bot calls, restricted to observations at or before the decision age.

What it cannot model, and where the results should therefore be discounted:
the snapshot cadence is 20-60s, so intra-interval wicks are invisible - stops
trigger later and at better prices than reality. Treat reported drawdowns as
optimistic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from ..features.build import DECISION_AGES, features_at
from ..risk.exit import ExitConfig, Position, evaluate
from ..util.log import get
from .amm import Pool
from .costs import CostModel

log = get("degen.backtest")

# A signal returns None to pass, or a float conviction score in (0, 1].
SignalFn = Callable[[dict], float | None]


@dataclass
class BacktestConfig:
    sol_usd: float = 94.0
    base_size_sol: float = 0.5
    max_size_sol: float = 2.0
    latency_s: float = 3.0
    decision_ages: tuple[int, ...] = DECISION_AGES
    max_pool_frac_buy: float = 0.03     # never be more than 3% of the pool on entry
    max_pool_frac_sell: float = 0.15    # and unwind in slices
    exit: ExitConfig = field(default_factory=ExitConfig)
    costs: CostModel = field(default_factory=CostModel)
    size_by_conviction: bool = True


@dataclass
class Trade:
    mint: str
    symbol: str | None
    entry_time: float
    entry_age: float
    entry_price: float
    sol_in: float
    sol_out: float
    exit_time: float
    hold_s: float
    peak_x: float
    realized_x: float
    pnl_sol: float
    reason: str
    conviction: float
    entry_liq: float
    n_exits: int
    entry_slip: float


def _pool_from_snapshot(row: pd.Series, sol_usd: float, fee: float) -> Pool | None:
    liq = row.get("liquidity")
    px = row.get("price_usd")
    if not liq or not px or liq <= 0 or px <= 0:
        return None
    return Pool.from_liquidity_usd(float(liq), float(px), sol_usd, fee=fee)


def run(
    snapshots: pd.DataFrame,
    signal: SignalFn,
    cfg: BacktestConfig | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Backtest `signal` over the panel. Returns (trades, summary)."""
    cfg = cfg or BacktestConfig()
    snaps = snapshots[snapshots["age_s"].notna() & snapshots["price_usd"].notna()].copy()
    snaps = snaps[snaps["price_usd"] > 0]
    if snaps.empty:
        return pd.DataFrame(), {"n_trades": 0, "note": "no usable snapshots"}

    fee = cfg.costs.amm_fee()
    trades: list[Trade] = []
    n_signals = 0
    n_skipped_liq = 0

    for mint, hist in snaps.groupby("mint", sort=False):
        hist = hist.sort_values("age_s").reset_index(drop=True)
        ages = hist["age_s"].astype(float).values
        if len(hist) < 3:
            continue

        entered = False
        for age in cfg.decision_ages:
            if entered:
                break
            if ages.min() > age or ages.max() < age:
                continue
            feat = features_at(hist, age)
            if feat is None:
                continue
            conv = signal(feat)
            if conv is None or conv <= 0:
                continue
            n_signals += 1

            # --- execution after latency ---
            exec_idx = int(np.searchsorted(ages, age + cfg.latency_s, side="left"))
            if exec_idx >= len(hist):
                continue
            erow = hist.iloc[exec_idx]
            pool = _pool_from_snapshot(erow, cfg.sol_usd, fee)
            if pool is None or pool.sol_reserve <= 0:
                n_skipped_liq += 1
                continue

            size = cfg.base_size_sol * (conv if cfg.size_by_conviction else 1.0)
            size = float(np.clip(size, 0.0, cfg.max_size_sol))
            size = min(size, pool.sol_reserve * cfg.max_pool_frac_buy)
            if size <= 0.001:
                n_skipped_liq += 1
                continue

            fill = pool.buy(size, max_pool_frac=cfg.max_pool_frac_buy)
            if not fill.ok or fill.qty_out <= 0:
                n_skipped_liq += 1
                continue
            spent = size + cfg.costs.entry_overhead(size)
            entry_px_sol = fill.avg_price
            pos = Position(
                mint=str(mint),
                entry_price=entry_px_sol,
                entry_time=float(erow["observed_at"]),
                tokens=fill.qty_out,
                sol_in=spent,
                entry_liq=float(erow.get("liquidity") or 0.0),
            )
            pool = pool.apply(fill, "buy")
            entered = True

            # --- manage the position forward ---
            sol_out = 0.0
            n_exits = 0
            reason = "end_of_data"
            exit_time = pos.entry_time
            tokens_left = fill.qty_out
            original_tokens = fill.qty_out

            for j in range(exec_idx + 1, len(hist)):
                row = hist.iloc[j]
                p = _pool_from_snapshot(row, cfg.sol_usd, fee)
                if p is None:
                    continue
                # The market moved; our earlier impact has been absorbed.
                pool = p
                # Value the position at the pool's mid in SOL terms.
                mid_sol = pool.mid
                if mid_sol <= 0:
                    continue
                t = float(row["observed_at"])
                liq_now = float(row.get("liquidity") or 0.0)

                dec = evaluate(pos, mid_sol, liq_now, t, cfg.exit)
                while dec is not None and tokens_left > 0:
                    want = dec.sell_frac_of_original * original_tokens
                    want = min(want, tokens_left, pool.token_reserve * cfg.max_pool_frac_sell)
                    if want <= 0:
                        break
                    sfill = pool.sell(want, max_pool_frac=cfg.max_pool_frac_sell)
                    if not sfill.ok or sfill.qty_out <= 0:
                        break
                    got = sfill.qty_out - cfg.costs.exit_overhead(sfill.qty_out)
                    sol_out += max(0.0, got)
                    tokens_left -= want
                    pool = pool.apply(sfill, "sell")
                    pos.remaining_frac = tokens_left / original_tokens if original_tokens else 0.0
                    if dec.reason in ("ladder", "cost_recovery"):
                        pos.rungs_hit += 1
                    n_exits += 1
                    reason = dec.reason
                    exit_time = t
                    if dec.close or pos.remaining_frac <= 1e-9:
                        break
                    # Our own fill just moved the pool; re-evaluate at the new
                    # price but do not record it as a market observation.
                    dec = evaluate(pos, pool.mid, liq_now, t, cfg.exit, record=False)
                if tokens_left <= 1e-9:
                    break

            # Anything still held at the end of data is marked out at what it
            # would actually fetch, not at the last printed price.
            if tokens_left > 1e-9 and pool.token_reserve > 0:
                want = min(tokens_left, pool.token_reserve * cfg.max_pool_frac_sell)
                sfill = pool.sell(want, max_pool_frac=cfg.max_pool_frac_sell)
                if sfill.ok:
                    sol_out += max(0.0, sfill.qty_out - cfg.costs.exit_overhead(sfill.qty_out))
                    n_exits += 1
                    tokens_left -= want
                    exit_time = float(hist.iloc[-1]["observed_at"])

            peak_x = pos.peak_price / pos.entry_price if pos.entry_price else 0.0
            trades.append(
                Trade(
                    mint=str(mint),
                    symbol=erow.get("symbol"),
                    entry_time=pos.entry_time,
                    entry_age=float(erow["age_s"]),
                    entry_price=entry_px_sol,
                    sol_in=spent,
                    sol_out=sol_out,
                    exit_time=exit_time,
                    hold_s=max(0.0, exit_time - pos.entry_time),
                    peak_x=peak_x,
                    realized_x=sol_out / spent if spent else 0.0,
                    pnl_sol=sol_out - spent,
                    reason=reason,
                    conviction=conv,
                    entry_liq=pos.entry_liq,
                    n_exits=n_exits,
                    entry_slip=fill.slippage_frac,
                )
            )

    tdf = pd.DataFrame([t.__dict__ for t in trades])
    return tdf, summarize(tdf, n_signals, n_skipped_liq)


def summarize(t: pd.DataFrame, n_signals: int = 0, n_skipped: int = 0) -> dict:
    """Metrics chosen for lottery-shaped payoffs. Sharpe is deliberately absent:
    with a right-skewed return distribution it punishes exactly the trades that
    make the strategy work."""
    if t.empty:
        return {"n_trades": 0, "n_signals": n_signals, "n_skipped_liquidity": n_skipped}
    x = t["realized_x"].astype(float)
    pnl = t["pnl_sol"].astype(float)
    wins = pnl > 0
    gross_win = pnl[wins].sum()
    gross_loss = -pnl[~wins].sum()
    boot = np.random.default_rng(7).choice(pnl.values, size=(2000, len(pnl)), replace=True).mean(axis=1)
    return {
        "n_signals": n_signals,
        "n_trades": int(len(t)),
        "n_skipped_liquidity": n_skipped,
        "win_rate": float(wins.mean()),
        "total_pnl_sol": float(pnl.sum()),
        "total_in_sol": float(t["sol_in"].sum()),
        "roi": float(pnl.sum() / t["sol_in"].sum()) if t["sol_in"].sum() else 0.0,
        "expectancy_sol": float(pnl.mean()),
        "expectancy_x": float(x.mean()),
        "median_x": float(x.median()),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "best_x": float(x.max()),
        "worst_x": float(x.min()),
        "p90_x": float(x.quantile(0.90)),
        "pct_total_loss": float((x < 0.1).mean()),
        "median_hold_min": float(t["hold_s"].median() / 60),
        "mean_entry_slip": float(t["entry_slip"].mean()),
        # Bootstrap CI on mean PnL per trade: the single most important number,
        # because a positive point estimate from 40 trades means nothing.
        "expectancy_ci95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
        "prob_expectancy_positive": float((boot > 0).mean()),
        "exit_reasons": t["reason"].value_counts().to_dict(),
    }
