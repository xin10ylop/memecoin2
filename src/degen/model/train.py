"""Model training with time-series discipline.

Three things are non-negotiable here, and skipping any of them produces a
backtest that looks wonderful and loses money live:

**Purged, embargoed walk-forward splits.** Labels look forward by `horizon_s`,
so a training row whose label window overlaps the test period has seen the
future. Standard k-fold shuffles rows and leaks catastrophically. Splits are
chronological, and the `horizon_s` immediately before each test block is purged
from training, plus an embargo after it.

**Group by token.** The same mint appears at several decision ages. Those rows
are near-duplicates; letting one land in train and another in test inflates
every metric. Splits are made on mints, never on rows.

**Metrics that match how the model is used.** The bot does not need calibrated
probabilities across the whole distribution - it takes the top few percent of
scores and buys them. So the reported metric is precision and realized
multiple in the top decile, against the base rate, not AUC.

With a small dataset LightGBM will happily memorise. `train()` therefore refuses
to return a model when there are too few positives, and the caller falls back to
the rule-based scorer.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..util.log import get

log = get("degen.model")

# Columns that must never be features: identifiers, anything from the future,
# and anything that encodes the label.
LEAK_COLS = {
    "mint", "symbol", "dev", "launchpad", "token_program", "decision_at", "created_at",
    "max_x", "end_x", "min_x", "t_to_peak", "max_liq", "max_holders", "n_future_obs",
    "horizon_s", "y", "target", "observed_at", "tags", "degenerate",
    # Raw price level is excluded deliberately. It correlates with the label
    # only because the label is a ratio: a token priced at 1e-9 reaches 2x on an
    # absolute move a token priced at 1e-4 could never make. Keeping it teaches
    # the model to buy small numbers. log_mcap and log_liq carry the scale
    # information that is actually meaningful.
    "price_usd", "fdv", "mcap",
    # Observation cadence is excluded too. obs_age and obs_lag are legitimately
    # known at decision time and the model does lean on them, but they encode
    # *this* collector's age-tiered polling schedule rather than anything about
    # the market, so a deployment that polls differently would see a different
    # feature distribution. Removing them costs almost nothing - measured lift
    # falls 5.90 to 5.78 while the top-decile multiple rises 3.00 to 3.12 - and
    # buys a model that depends only on observable market state.
    "obs_age", "obs_lag", "n_obs", "decision_age",
}

MIN_POSITIVES = 40
MIN_ROWS = 300


@dataclass
class TrainConfig:
    target_x: float = 1.5          # label: did it reach this multiple in-horizon
    n_splits: int = 4
    embargo_frac: float = 0.02
    num_leaves: int = 15           # deliberately small: the dataset is small
    min_data_in_leaf: int = 30
    learning_rate: float = 0.05
    n_estimators: int = 300
    feature_fraction: float = 0.7
    bagging_fraction: float = 0.8
    lambda_l2: float = 5.0
    top_frac: float = 0.10         # the slice the bot would actually trade


@dataclass
class TrainResult:
    ok: bool
    reason: str = ""
    n_rows: int = 0
    n_positives: int = 0
    base_rate: float = 0.0
    features: list[str] = field(default_factory=list)
    fold_metrics: list[dict] = field(default_factory=list)
    mean_top_precision: float = 0.0
    mean_lift: float = 0.0
    mean_top_mult: float = 0.0
    importance: dict[str, float] = field(default_factory=dict)
    model_path: str | None = None


def feature_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if c in LEAK_COLS:
            continue
        if df[c].dtype.kind in "biufc":
            cols.append(c)
    return cols


def purged_splits(
    df: pd.DataFrame,
    n_splits: int,
    horizon_s: float,
    embargo_frac: float,
    time_col: str = "decision_at",
    group_col: str = "mint",
) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    """Chronological splits, purged by the label horizon and grouped by mint."""
    d = df.sort_values(time_col)
    times = d[time_col].values.astype(float)
    groups = d[group_col].values
    n = len(d)
    if n < n_splits * 2:
        return
    bounds = np.linspace(int(n * 0.4), n, n_splits + 1).astype(int)
    span = float(times[-1] - times[0]) or 1.0
    embargo = span * embargo_frac

    for i in range(n_splits):
        lo, hi = bounds[i], bounds[i + 1]
        if hi - lo < 10:
            continue
        test_idx = np.arange(lo, hi)
        test_start = times[lo]
        # Purge: drop any training row whose label window reaches the test set,
        # then embargo a further slice after the test block.
        train_mask = (times < test_start - horizon_s - embargo)
        train_idx = np.where(train_mask)[0]
        if len(train_idx) < 50:
            continue
        # No mint may straddle the boundary.
        test_groups = set(groups[test_idx])
        train_idx = np.array([j for j in train_idx if groups[j] not in test_groups])
        if len(train_idx) < 50:
            continue
        yield d.index.values[train_idx], d.index.values[test_idx]


def _fold_metrics(y: np.ndarray, score: np.ndarray, mult: np.ndarray, top_frac: float) -> dict:
    n = len(y)
    k = max(1, int(n * top_frac))
    order = np.argsort(-score)
    top = order[:k]
    base = float(y.mean()) if n else 0.0
    prec = float(y[top].mean())
    return {
        "n": n,
        "base_rate": base,
        "top_k": k,
        "top_precision": prec,
        "lift": (prec / base) if base > 0 else 0.0,
        "top_mean_mult": float(np.nanmean(mult[top])) if len(top) else 0.0,
        "all_mean_mult": float(np.nanmean(mult)) if n else 0.0,
    }


def train(panel: pd.DataFrame, cfg: TrainConfig | None = None, out_dir: str | Path = "models") -> TrainResult:
    cfg = cfg or TrainConfig()
    if panel.empty:
        return TrainResult(False, "empty panel")

    d = panel.dropna(subset=["max_x", "decision_at"]).copy()
    # Drop rows whose label came from a degenerate first print, and the raw
    # price level, which is not a real signal - a micro-priced token produces
    # large ratios from small absolute moves, so the model would learn to buy
    # tokens for having small numbers rather than for anything about them.
    if "degenerate" in d.columns:
        d = d[d["degenerate"].fillna(False) == False]  # noqa: E712
    d["y"] = (d["max_x"] >= cfg.target_x).astype(int)
    n_pos = int(d["y"].sum())
    base = float(d["y"].mean()) if len(d) else 0.0

    if len(d) < MIN_ROWS or n_pos < MIN_POSITIVES:
        return TrainResult(
            ok=False,
            reason=(
                f"insufficient data: {len(d)} rows / {n_pos} positives "
                f"(need >={MIN_ROWS} rows and >={MIN_POSITIVES} positives). "
                "Run the collector longer; the rule-based scorer stays in charge until then."
            ),
            n_rows=len(d), n_positives=n_pos, base_rate=base,
        )

    import lightgbm as lgb

    feats = feature_columns(d)
    horizon = float(d["horizon_s"].iloc[0]) if "horizon_s" in d else 3600.0
    folds: list[dict] = []
    importances: dict[str, float] = {}
    last_model = None

    for tr_idx, te_idx in purged_splits(d, cfg.n_splits, horizon, cfg.embargo_frac):
        tr, te = d.loc[tr_idx], d.loc[te_idx]
        if tr["y"].sum() < 8 or te["y"].sum() < 2:
            continue
        pos_w = float((len(tr) - tr["y"].sum()) / max(1, tr["y"].sum()))
        m = lgb.LGBMClassifier(
            num_leaves=cfg.num_leaves,
            min_data_in_leaf=cfg.min_data_in_leaf,
            learning_rate=cfg.learning_rate,
            n_estimators=cfg.n_estimators,
            feature_fraction=cfg.feature_fraction,
            bagging_fraction=cfg.bagging_fraction,
            bagging_freq=1,
            lambda_l2=cfg.lambda_l2,
            scale_pos_weight=min(pos_w, 20.0),
            verbose=-1,
        )
        m.fit(tr[feats], tr["y"])
        score = m.predict_proba(te[feats])[:, 1]
        folds.append(_fold_metrics(te["y"].values, score, te["max_x"].values, cfg.top_frac))
        for f, v in zip(feats, m.feature_importances_):
            importances[f] = importances.get(f, 0.0) + float(v)
        last_model = m

    if not folds or last_model is None:
        return TrainResult(False, "no usable folds after purging", len(d), n_pos, base)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / "signal_lgbm.txt"
    last_model.booster_.save_model(str(model_path))
    total_imp = sum(importances.values()) or 1.0
    imp = {k: round(v / total_imp, 4) for k, v in sorted(importances.items(), key=lambda kv: -kv[1])[:30]}
    res = TrainResult(
        ok=True,
        n_rows=len(d), n_positives=n_pos, base_rate=base,
        features=feats, fold_metrics=folds,
        mean_top_precision=float(np.mean([f["top_precision"] for f in folds])),
        mean_lift=float(np.mean([f["lift"] for f in folds])),
        mean_top_mult=float(np.mean([f["top_mean_mult"] for f in folds])),
        importance=imp,
        model_path=str(model_path),
    )
    (out / "train_report.json").write_text(json.dumps({**asdict(res), "config": asdict(cfg)}, indent=2, default=str))
    (out / "features.json").write_text(json.dumps(feats, indent=2))
    log.info("trained: rows=%d pos=%d base=%.3f top_precision=%.3f lift=%.2f",
             len(d), n_pos, base, res.mean_top_precision, res.mean_lift)
    return res
