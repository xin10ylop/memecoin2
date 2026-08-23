"""Does complexity help or hurt out of sample?

The full pipeline was tuned hard against the whole census and fails on held-out
data. The obvious hypothesis is that the tuning is the problem, not the signal:
the earliest finding here - wait for holders to arrive - was discovered with
almost no fitting, so it has far less overfitting surface. This measures each
level of complexity on the same held-out window.
"""
import sys; sys.path.insert(0, "src")
import numpy as np, pandas as pd
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
pd.set_option("display.width", 240)
from degen.store.quality import load_clean
snaps = load_clean(report=True)
first = snaps.groupby("mint").observed_at.min().sort_values()
cut = int(len(first) * 0.60)
tr = snaps[snaps.mint.isin(set(first.iloc[:cut].index))]
te = snaps[snaps.mint.isin(set(first.iloc[cut:].index))]
print(f"train {len(set(first.iloc[:cut].index))} mints / test {len(set(first.iloc[cut:].index))} mints\n")

def outcomes(df):
    g = df.sort_values("observed_at").groupby("mint")
    o = g.agg(dev=("dev","first"), created_at=("created_at","first"), p0=("price_usd","first"),
              pmax=("price_usd","max"), liqmax=("liquidity","max"), n=("price_usd","size")).reset_index()
    o = o[(o.n>=4)&(o.p0>0)]; o["maxx"]=o.pmax/o.p0; o=o[o.maxx<200]
    o["success"]=(o.maxx>=2.0)|(o.liqmax>=10000); return o

book = DeployerBook(path="data/deployers_cx.json"); book.devs.clear()
book.build(outcomes(tr).to_dict("records")); set_deployer_book(book)
res = train(build_panel(tr, horizon_s=1800), TrainConfig(target_x=1.5), out_dir="models_cx")
print(f"model on train: lift={res.mean_lift:.2f} precision={res.mean_top_precision:.3f}\n")

saf_default = SafetyConfig()
scorer_full = CompositeScorer(rule_threshold=0.55, model_dir="models_cx")
scorer_rules = CompositeScorer(rule_threshold=0.55, model_dir="__none__")

def n(f, k):
    v = f.get(k)
    try: v = float(v)
    except (TypeError, ValueError): return None
    return v if v == v else None

def gate_holders(f):
    h = n(f,"holder_count")
    return 1.0 if (h is not None and h >= 20) else None

def gate_traction(f):
    h, l, b = n(f,"holder_count"), n(f,"liquidity"), n(f,"buy_sell_ratio")
    if h is None or l is None or b is None: return None
    return 1.0 if (h >= 20 and l >= 3000 and b >= 0.55) else None

def gate_traction_mcap(f):
    h, l, b, m = n(f,"holder_count"), n(f,"liquidity"), n(f,"buy_sell_ratio"), n(f,"mcap")
    if None in (h,l,b,m): return None
    return 1.0 if (h >= 20 and l >= 3000 and b >= 0.55 and m >= 5000) else None

def with_safety(inner):
    def g(f):
        if not check_local(f, saf_default).ok: return None
        return inner(f)
    return g

def scored(scorer):
    def g(f):
        if not check_local(f, saf_default).ok: return None
        r = scorer.score(f)
        return float(np.clip(r.score, 0.3, 1.0)) if r.passed else None
    return g

cfg = BacktestConfig(base_size_sol=0.5, costs=CostModel(venue="pumpfun"))
rows = []
for name, sig in [
    ("1. holders>=20 only", gate_holders),
    ("2. + liq & buy/sell", gate_traction),
    ("3. + market cap", gate_traction_mcap),
    ("4. + full safety stack", with_safety(gate_traction_mcap)),
    ("5. + rule scorer", scored(scorer_rules)),
    ("6. + model (full pipeline)", scored(scorer_full)),
]:
    out = {"strategy": name}
    for tag, df in (("IS", tr), ("OOS", te)):
        t, s = run(df, sig, cfg)
        if s["n_trades"] < 5:
            out[f"{tag}_n"] = s["n_trades"]; continue
        pnl = np.sort(t.pnl_sol.values)[::-1]
        out[f"{tag}_n"] = s["n_trades"]
        out[f"{tag}_win%"] = round(100*s["win_rate"],1)
        out[f"{tag}_ROI%"] = round(100*s["roi"],1)
        out[f"{tag}_PF"] = round(s["profit_factor"],2)
        out[f"{tag}_P>0"] = round(s["prob_expectancy_positive"],2)
    rows.append(out); print("  done", name, flush=True)
print()
print(pd.DataFrame(rows).to_string(index=False))
set_deployer_book(None)
import shutil, pathlib
shutil.rmtree("models_cx", ignore_errors=True); pathlib.Path("data/deployers_cx.json").unlink(missing_ok=True)
