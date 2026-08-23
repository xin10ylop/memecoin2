"""Out-of-sample validation.

Everything reported so far shares one weakness: the strategy was tuned on the
same data it was measured on, and the model's backtest overlaps its training
period. This splits the census by time, fits on the earlier part only, and
measures on the later part, which is the only arrangement that answers "would
this have worked" rather than "does this describe what happened".

The deployer book is rebuilt from the training window alone, so a creator's
reputation at test time reflects only launches the bot could have seen.
"""
import sys; sys.path.insert(0, "src")
import json, shutil
from pathlib import Path

import numpy as np
import pandas as pd

from degen.features.build import build_panel, set_deployer_book
from degen.model.train import TrainConfig, train
from degen.safety.filters import SafetyConfig, check_local
from degen.signals.deployer import DeployerBook
from degen.signals.score import CompositeScorer
from degen.sim.backtest import BacktestConfig, run
from degen.sim.costs import CostModel
from degen.store.lake import lake
from degen.util.log import setup

setup("WARNING")
snaps = lake().df("snapshots")
snaps = snaps[snaps.price_usd.notna() & (snaps.price_usd > 0) & snaps.age_s.notna()]
# Split by chronological rank of mints rather than by wall-clock. The collector
# runs in a container that suspends when the session idles, so elapsed time is
# not proportional to data collected - a time split put 9% of mints on one side.
first = snaps.groupby("mint").observed_at.min().sort_values()
cut = int(len(first) * 0.60)
split = float(first.iloc[cut])
t0, t1 = snaps.observed_at.min(), snaps.observed_at.max()
print(f"census spans {(t1-t0)/3600:.2f}h across {len(first)} mints")
print(f"split at the {cut}th mint chronologically\n")

# Each mint belongs wholly to one side, so no token straddles the boundary.
train_mints = set(first.iloc[:cut].index)
test_mints = set(first.iloc[cut:].index)
tr = snaps[snaps.mint.isin(train_mints)]
te = snaps[snaps.mint.isin(test_mints)]
print(f"train mints={len(train_mints)} snaps={len(tr)}   test mints={len(test_mints)} snaps={len(te)}")

# --- deployer book from the training window only ---
def outcomes(df):
    g = df.sort_values("observed_at").groupby("mint")
    out = g.agg(dev=("dev", "first"), created_at=("created_at", "first"),
                p0=("price_usd", "first"), pmax=("price_usd", "max"),
                liqmax=("liquidity", "max"), n=("price_usd", "size")).reset_index()
    out = out[(out.n >= 4) & (out.p0 > 0)]
    out["maxx"] = out.pmax / out.p0
    out = out[out.maxx < 200]
    out["success"] = (out.maxx >= 2.0) | (out.liqmax >= 10000)
    return out

tr_out = outcomes(tr)
book = DeployerBook(path="data/deployers_wf.json")
book.devs.clear()
book.build(tr_out.to_dict("records"))
set_deployer_book(book)

# --- fit the model on the training window only ---
tr_panel = build_panel(tr, horizon_s=1800)
res = train(tr_panel, TrainConfig(target_x=1.5), out_dir="models_wf")
print(f"\nmodel fit on train only: ok={res.ok} rows={res.n_rows} pos={res.n_positives} "
      f"lift={res.mean_lift:.2f} top_precision={res.mean_top_precision:.3f}")

saf = SafetyConfig()
cfg = BacktestConfig(base_size_sol=0.5, costs=CostModel(venue="pumpfun"))

def evaluate(df, scorer, label):
    def sig(f):
        if not check_local(f, saf).ok:
            return None
        r = scorer.score(f)
        return float(np.clip(r.score, 0.3, 1.0)) if r.passed else None
    t, s = run(df, sig, cfg)
    if s["n_trades"] < 3:
        print(f"{label:34} trades={s['n_trades']} (too few)")
        return None
    pnl = np.sort(t.pnl_sol.values)[::-1]
    print(f"{label:34} trades={s['n_trades']:3d} win={100*s['win_rate']:5.1f}% "
          f"ROI={100*s['roi']:7.1f}% exp={s['expectancy_x']:.3f}x med={s['median_x']:.3f}x "
          f"PF={s['profit_factor']:6.2f} P(exp>0)={s['prob_expectancy_positive']:.3f} "
          f"ex_top3={pnl[3:].mean() if len(pnl)>3 else float('nan'):+.4f}")
    return s

print()
rules_only = CompositeScorer(rule_threshold=0.55, model_dir="__none__")
with_model = CompositeScorer(rule_threshold=0.55, model_dir="models_wf")
print("IN-SAMPLE (train window, for reference):")
evaluate(tr, rules_only, "  rules only")
evaluate(tr, with_model, "  rules + model")
print("\nOUT-OF-SAMPLE (test window, never seen by the model):")
evaluate(te, rules_only, "  rules only")
evaluate(te, with_model, "  rules + model")
set_deployer_book(None)
shutil.rmtree("models_wf", ignore_errors=True)
Path("data/deployers_wf.json").unlink(missing_ok=True)
