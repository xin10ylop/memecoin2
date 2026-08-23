"""Command line interface.

    degen status                      what the system knows right now
    degen scan                        top current candidates, with reasons
    degen collect                     run the research data collector
    degen harvest                     pull historical price paths
    degen backtest                    evaluate the strategy on collected data
    degen basrates                    the honest outcome distribution
    degen train                       fit the model (refuses if underpowered)
    degen trade --mode paper          run the trader
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from .util.log import setup

app = typer.Typer(add_completion=False, help="Quantitative memecoin trading system.")


@app.command()
def status() -> None:
    """Show data volume, model state, and open positions."""
    setup()
    from .store.lake import DISCOVERIES, EVENTS, OHLCV, SNAPSHOTS, TRADES, lake

    lk = lake()
    print("=== data lake ===")
    for ds in (DISCOVERIES, SNAPSHOTS, OHLCV, TRADES, EVENTS):
        print(f"  {ds:14} {lk.count(ds):>9,} rows")
    con = lk.con()
    try:
        r = con.execute("select count(distinct mint) m, min(observed_at) a, max(observed_at) b from snapshots").fetchone()
        span = (r[2] - r[1]) / 3600 if r[1] and r[2] else 0
        print(f"  tracked mints: {r[0]:,}   span: {span:.2f} h")
    except Exception:
        pass
    mp = Path("models/signal_lgbm.txt")
    print(f"\n=== model ===\n  {'trained: ' + str(mp) if mp.exists() else 'not trained (rule scorer active)'}")
    if Path("models/train_report.json").exists():
        rep = json.loads(Path("models/train_report.json").read_text())
        print(f"  top-decile precision {rep.get('mean_top_precision'):.3f}  lift {rep.get('mean_lift'):.2f}")
    try:
        t = con.execute("select mode, count(*) n, sum(pnl_sol) pnl, avg(realized_x) x from trades group by 1").df()
        if len(t):
            print("\n=== trades ===")
            print(t.to_string(index=False))
    except Exception:
        pass


@app.command()
def doctor() -> None:
    """Check that this machine can actually collect and trade.

    Written for a fresh server: it verifies each external dependency the bot
    needs and says which of them are optional, so a red line is unambiguous
    rather than something to squint at.
    """
    setup("WARNING")
    import time

    from .sources import dexscreener, jupiter
    from .sources.rugcheck import summary as rug_summary
    from .sources.solana_rpc import RPC_URL, get_slot
    from .store.lake import lake

    ok = True

    def check(name: str, fn, required: bool = True, detail: str = "") -> None:
        nonlocal ok
        t0 = time.time()
        try:
            res = fn()
            good = bool(res)
        except Exception as exc:
            res, good = f"{type(exc).__name__}: {exc}", False
        ms = (time.time() - t0) * 1000
        if good:
            mark = "\033[32m  ok \033[0m"
        elif required:
            mark, ok = "\033[31mFAIL \033[0m", False
        else:
            mark = "\033[33mwarn \033[0m"
        extra = detail or (str(res)[:52] if good else str(res)[:52])
        print(f"  [{mark}] {name:<26} {ms:>6.0f}ms  {extra}")

    print("\ndata sources (all free, no key needed)")
    check("Jupiter recent tokens", lambda: len(jupiter.recent()) > 0)
    check("Jupiter batch lookup", lambda: len(jupiter.search([jupiter.SOL_MINT])) > 0)
    check("Jupiter quote", lambda: jupiter.quote(jupiter.SOL_MINT, jupiter.USDC_MINT, 10**8) is not None)
    check("DexScreener", lambda: len(dexscreener.search("SOL")) > 0)
    check("RugCheck", lambda: rug_summary(jupiter.USDC_MINT) is not None)

    print("\nchain access")
    slot = get_slot()
    check("Solana RPC", lambda: slot is not None, detail=f"slot {slot} via {RPC_URL.split('//')[-1][:34]}")
    if "mainnet-beta.solana.com" in RPC_URL:
        print("         \033[33mnote\033[0m  public RPC is fine for collecting, too slow for live trading")

    print("\nlocal state")
    lk = lake()
    n_snap = lk.count("snapshots")
    check("data lake writable", lambda: (lk.dataset_dir("snapshots").exists()), detail=str(lk.root))
    check("snapshots collected", lambda: True, detail=f"{n_snap:,} rows")
    model = Path("models/signal_lgbm.txt")
    check("model artifact", lambda: model.exists(), required=False,
          detail="present" if model.exists() else "not trained (the gate does not need it)")
    key = os.getenv("DEGEN_WALLET_KEY", "")
    check("wallet key", lambda: bool(key), required=False,
          detail="loaded" if key else "unset (only needed for --mode live)")

    print()
    if n_snap < 50_000:
        print(f"  \033[33mThe census is small ({n_snap:,} snapshots). `degen validate` needs weeks of\033[0m")
        print("  \033[33mcollection before its answer means anything.\033[0m\n")
    print("  \033[32mall required checks passed\033[0m\n" if ok
          else "  \033[31msomething required is broken - see FAIL above\033[0m\n")
    raise typer.Exit(0 if ok else 1)


@app.command()
def scan(top: int = 15, age: int = 300, deep: bool = False) -> None:
    """Score every token currently being tracked and print the best."""
    setup()
    from .features.build import features_at
    from .safety.filters import SafetyConfig, check_local, check_rugcheck
    from .signals.score import CompositeScorer
    from .store.lake import lake

    from .store.quality import load_clean

    snaps = load_clean()
    if snaps.empty:
        print("no usable snapshots yet - run `degen collect` first")
        raise typer.Exit(1)
    sc, cfg = CompositeScorer(), SafetyConfig()
    out = []
    for mint, h in snaps.groupby("mint"):
        f = features_at(h.sort_values("age_s"), age)
        if f is None:
            continue
        safe = check_local(f, cfg)
        r = sc.score(f)
        out.append((r.score, safe.ok, f, r, safe))
    out.sort(key=lambda x: -x[0])
    print(f"{'symbol':<14}{'score':>7}{'safe':>6}{'liq$':>11}{'hold':>6}{'devmints':>9}  why")
    for s, ok, f, r, safe in out[:top]:
        why = r.explain().split("] ", 1)[-1][:70]
        print(f"{str(f.get('symbol'))[:13]:<14}{s:>7.3f}{('yes' if ok else 'NO'):>6}"
              f"{(f.get('liquidity') or 0):>11,.0f}{str(f.get('holder_count')):>6}"
              f"{str(f.get('dev_mints')):>9}  {why}")
        if not ok:
            print(f"{'':14}  rejected: {safe.rejects[:2]}")
        if deep and ok:
            d = check_rugcheck(str(f["mint"]), cfg)
            print(f"{'':14}  rugcheck: ok={d.ok} {d.rejects[:2]} {d.warnings[:1]}")


@app.command()
def collect() -> None:
    """Run the data collector (discovery + forward tracking)."""
    from .collect.collector import main as run

    run()


@app.command()
def harvest(pages: int = 6, max_pages: int = 5, limit: int = 0) -> None:
    """Harvest historical OHLCV price paths for exit-policy fitting."""
    setup()
    from .collect.ohlcv_harvest import build_universe, harvest as do

    uni = build_universe(pages)
    do(uni, max_pages=max_pages, limit=limit or None)


@app.command()
def baserates(min_age: int = 180, min_obs: int = 5) -> None:
    """The honest outcome distribution of the launches we observed."""
    setup()
    import numpy as np

    from .store.quality import load_clean

    snaps = load_clean(report=True)
    if snaps.empty:
        print("not enough data yet")
        raise typer.Exit(1)
    snaps = snaps.sort_values("observed_at")
    g = snaps.groupby("mint", sort=False)
    d = g.agg(a0=("age_s", "first"), p0=("price_usd", "first"), pmax=("price_usd", "max"),
              liqmax=("liquidity", "max"), hmax=("holder_count", "max"),
              n=("price_usd", "size")).reset_index()
    d = d[(d.a0 < min_age) & (d.n >= min_obs) & (d.p0 > 0)]
    d["maxx"] = d.pmax / d.p0
    d = d[np.isfinite(d.maxx) & (d.maxx < 1e5)]
    if d.empty:
        print("not enough data yet")
        raise typer.Exit(1)
    n = len(d)
    print(f"\ncensus of {n} launches caught <{min_age}s old with >={min_obs} observations\n")
    for m in (1.3, 1.5, 2, 3, 5, 10, 20, 50):
        k = int((d.maxx >= m).sum())
        lo, hi = _wilson(k, n)
        print(f"  ever >= {m:>4}x : {k:>5} / {n}  = {100*k/n:6.3f}%   95% CI [{100*lo:.3f}%, {100*hi:.3f}%]")
    trimmed = d.maxx[d.maxx <= d.maxx.quantile(0.99)]
    print(f"\n  median peak multiple      : {d.maxx.median():.3f}x")
    print(f"  mean peak multiple        : {d.maxx.mean():.3f}x   (the tail is the whole story)")
    print(f"  mean excluding top 1%     : {trimmed.mean():.3f}x   (what you get without a jackpot)")
    print(f"  p99 peak multiple         : {d.maxx.quantile(0.99):.2f}x")
    print(f"  reached $10k liquidity: {100*(d.liqmax>=10000).mean():.2f}%")
    print(f"  reached 50 holders    : {100*(d.hmax>=50).mean():.2f}%")


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, c - h), min(1.0, c + h)


@app.command()
def backtest(rule: str = "gate", threshold: float = 0.55, size: float = 0.5,
             safety: bool = True, venue: str = "pumpfun") -> None:
    """Backtest on collected snapshots. `rule` is "gate" (default, validated out
    of sample) or "scorer" (better in-sample, worse out of sample)."""
    setup()
    import numpy as np

    from .safety.filters import SafetyConfig, check_local
    from .signals.score import CompositeScorer
    from .sim.backtest import BacktestConfig, run
    from .sim.costs import CostModel
    from .store.lake import lake

    from .store.quality import load_clean

    snaps = load_clean(report=True)
    if snaps.empty:
        print("no data")
        raise typer.Exit(1)
    from .signals.gate import TractionGate

    saf = SafetyConfig()
    if rule == "gate":
        gate = TractionGate()

        def sig(f):
            if safety and not check_local(f, saf).ok:
                return None
            return gate.signal(f)
    else:
        sc = CompositeScorer(rule_threshold=threshold)

        def sig(f):
            if safety and not check_local(f, saf).ok:
                return None
            r = sc.score(f)
            return float(np.clip(r.score, 0.3, 1.0)) if r.score >= threshold else None

    t, s = run(snaps, sig, BacktestConfig(base_size_sol=size, costs=CostModel(venue=venue)))
    print(json.dumps(s, indent=2, default=str))
    if not t.empty:
        pnl = np.sort(t.pnl_sol.values)[::-1]
        print("\nrobustness (profit is meaningless if one trade carries it):")
        for k in range(0, min(4, len(pnl))):
            rest = pnl[k:]
            print(f"  drop top {k}: total={rest.sum():+.4f} SOL  mean={rest.mean():+.5f}  win%={100*(rest>0).mean():.1f}")


@app.command()
def train(target: float = 1.5, horizon: int = 3600) -> None:
    """Fit the signal model. Refuses when the data cannot support it."""
    setup()
    from .features.build import build_panel
    from .model.train import TrainConfig, train as do
    from .store.lake import lake

    from .store.quality import load_clean

    snaps = load_clean(report=True)
    panel = build_panel(snaps, horizon_s=horizon)
    print(f"panel: {len(panel)} rows from {snaps.mint.nunique()} mints")
    r = do(panel, TrainConfig(target_x=target))
    if not r.ok:
        print(f"NOT TRAINED: {r.reason}")
        raise typer.Exit(1)
    print(f"trained. rows={r.n_rows} positives={r.n_positives} base={r.base_rate:.4f}")
    print(f"top-decile precision {r.mean_top_precision:.3f} (lift {r.mean_lift:.2f}x), "
          f"mean multiple in top decile {r.mean_top_mult:.3f}")
    print("top features:", list(r.importance)[:12])


@app.command()
def validate(train_frac: float = 0.60, horizon: int = 1800) -> None:
    """Out-of-sample walk-forward test. The only number that means anything.

    Splits the census chronologically, fits the model and the deployer book on
    the earlier part only, and measures on the later part. Everything else this
    tool reports is in-sample and will flatter the strategy.
    """
    setup()
    import subprocess
    import sys as _sys

    print("running walk-forward validation (this takes a few minutes)...\n")
    subprocess.run([_sys.executable, "scripts/walk_forward.py"], check=False)


@app.command()
def trade(
    mode: str = typer.Option("paper", help="paper | dry | live"),
    bankroll: float = 5.0,
    rule: str = typer.Option("gate", help="gate (validated) | scorer (opt-in)"),
    threshold: float = 0.55,
    i_understand_the_risk: bool = typer.Option(False, help="required for --mode live"),
) -> None:
    """Run the trader."""
    setup()
    from .live.trader import LiveTrader, TraderConfig
    from .risk.manager import RiskConfig

    if mode == "live" and not i_understand_the_risk:
        print("live mode requires --i-understand-the-risk and DEGEN_WALLET_KEY.")
        raise typer.Exit(2)
    cfg = TraderConfig(mode=mode, entry_rule=rule, score_threshold=threshold,
                       risk=RiskConfig(bankroll_sol=bankroll))
    LiveTrader(cfg).run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
