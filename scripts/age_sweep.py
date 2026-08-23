"""Is the entry-age window costing us the winners?"""
import sys; sys.path.insert(0, "src")
import numpy as np, pandas as pd
from degen.store.lake import lake
from degen.sim.backtest import run, BacktestConfig
from degen.sim.costs import CostModel
from degen.signals.score import CompositeScorer
from degen.safety.filters import check_local, SafetyConfig
from degen.util.log import setup

setup("WARNING")
pd.set_option("display.width", 210)
from degen.store.quality import load_clean
snaps = load_clean(report=True)
sc = CompositeScorer(rule_threshold=0.55); saf = SafetyConfig()
def sig(f):
    if not check_local(f, saf).ok: return None
    r = sc.score(f)
    return float(np.clip(r.score, 0.3, 1.0)) if r.passed else None

rows = []
for name, ages in [
    ("30s only", (30,)), ("60s only", (60,)), ("180s only", (180,)),
    ("300s only", (300,)), ("600s only", (600,)), ("1800s only", (1800,)),
    ("30+60 early", (30, 60)), ("60+180+300 current", (60, 180, 300)),
    ("current + late", (60, 180, 300, 600, 1800)),
]:
    t, s = run(snaps, sig, BacktestConfig(base_size_sol=0.5, decision_ages=ages,
                                          costs=CostModel(venue="pumpfun")))
    if s["n_trades"] < 3:
        rows.append({"entry ages": name, "trades": s["n_trades"]}); continue
    pnl = np.sort(t.pnl_sol.values)[::-1]
    rows.append({"entry ages": name, "trades": s["n_trades"],
                 "win%": round(100*s["win_rate"],1), "ROI%": round(100*s["roi"],1),
                 "exp_x": round(s["expectancy_x"],3), "med_x": round(s["median_x"],3),
                 "PF": round(s["profit_factor"],2), "best": round(s["best_x"],1),
                 "ex_top3/trade": round(pnl[3:].mean(),4) if len(pnl)>3 else None})
    print(f"  done {name}", flush=True)
print()
print(pd.DataFrame(rows).to_string(index=False))
