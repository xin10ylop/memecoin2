"""Decision-point feature construction.

The design rule that everything else depends on: a feature vector is built at a
specific *token age* T, using only snapshots with age <= T. Nothing that happens
after T may touch it. This matters more than it sounds — the obvious way to
build this dataset ("use the first snapshot of each token") silently leaks,
because how much has happened by the first snapshot depends on how long the
collector took to notice the token, and tokens that get noticed late are the
ones that were already busy. Fixing T removes that confound and makes the
backtest correspond to a decision the live bot can actually make.

For each token the live bot evaluates the same function at the same ages, so
`features_at()` is shared by the backtest and the runtime. There is no second
implementation to drift.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

# Ages (seconds since first pool) at which the bot considers a token.
#
# These are measured, not chosen. Backtesting each checkpoint in isolation on
# the collected census produces a clean inverted-U in every metric:
#
#     entry age    trades   win%     ROI    profit factor
#        30s          19    31.6%   -4.4%       0.81
#        60s          49    65.3%  +26.2%       3.11
#       180s          31    67.7%  +157.9%     26.55
#       300s          25    48.0%  +24.2%       2.23
#       600s          22    45.5%  +14.2%       2.00
#      1800s           4    25.0%   -9.9%       0.23
#
# Sniping the mint loses money outright: at 30 seconds there is nothing to
# measure yet and the bot is buying at the base rate, where the median token
# never moves. By half an hour the move has happened and what is left is the
# distribution. The information arrives in between, and peaks near three
# minutes. The window below brackets that peak and deliberately excludes both
# tails, which were losing configurations rather than merely weaker ones.
DECISION_AGES: tuple[int, ...] = (60, 120, 180, 240, 300)

# Optional deployer-reputation book. Injected rather than imported at module
# scope so feature construction stays usable with no persisted history, and so
# the backtest can supply a book built strictly from earlier data.
_DEPLOYER_BOOK: Any = None


def set_deployer_book(book: Any) -> None:
    global _DEPLOYER_BOOK
    _DEPLOYER_BOOK = book

# Snapshot columns carried straight through (state at T).
LEVEL_COLS = (
    "price_usd", "liquidity", "mcap", "fdv", "holder_count",
    "top_holders_pct", "dev_balance_pct", "organic_score",
    "s5m_numBuys", "s5m_numSells", "s5m_numTraders", "s5m_numNetBuyers",
    "s5m_buyVolume", "s5m_sellVolume", "s5m_priceChange", "s5m_liquidityChange",
    "s1h_numBuys", "s1h_numSells", "s1h_numTraders", "s1h_numNetBuyers",
    "s1h_buyVolume", "s1h_sellVolume", "s1h_priceChange",
)

# Static per-token attributes.
STATIC_COLS = (
    "dev_mints", "dev_migrations", "mint_auth_disabled", "freeze_auth_disabled",
    "has_socials", "launchpad", "token_program", "symbol", "dev",
)


def _safe_div(a: float | None, b: float | None, default: float = 0.0) -> float:
    if a is None or b is None:
        return default
    try:
        if b == 0 or not math.isfinite(b) or not math.isfinite(a):
            return default
        v = a / b
        return v if math.isfinite(v) else default
    except (TypeError, ZeroDivisionError):
        return default


def _log1p(x: float | None) -> float:
    if x is None or not math.isfinite(float(x)) or x < 0:
        return 0.0
    return float(np.log1p(x))


def _slope(times: Sequence[float], values: Sequence[float]) -> float:
    """Least-squares slope per second. Returns 0 when undetermined."""
    if len(times) < 2:
        return 0.0
    t = np.asarray(times, dtype=float)
    v = np.asarray(values, dtype=float)
    m = np.isfinite(t) & np.isfinite(v)
    if m.sum() < 2:
        return 0.0
    t, v = t[m], v[m]
    t = t - t[0]
    var = float(((t - t.mean()) ** 2).sum())
    if var <= 0:
        return 0.0
    return float(((t - t.mean()) * (v - v.mean())).sum() / var)


def features_at(hist: pd.DataFrame, age: float) -> dict[str, Any] | None:
    """Build one feature row from a token's snapshots, using only age <= `age`.

    `hist` must be that single token's snapshots sorted by observed_at and must
    carry an `age_s` column. Returns None when there is nothing usable yet.
    """
    h = hist[hist["age_s"].notna() & (hist["age_s"] <= age)]
    if h.empty:
        return None
    h = h.sort_values("age_s")
    last = h.iloc[-1]
    if not last.get("price_usd") or float(last["price_usd"]) <= 0:
        return None

    f: dict[str, Any] = {
        "mint": last["mint"],
        "decision_age": age,
        "decision_at": float(last["observed_at"]),
        "created_at": float(last["created_at"]) if pd.notna(last.get("created_at")) else None,
        "obs_age": float(last["age_s"]),
        "n_obs": int(len(h)),
        # How stale is our view at the moment of decision? The live bot has the
        # same limitation, so the model should be allowed to see it.
        "obs_lag": float(age - last["age_s"]),
    }

    for c in STATIC_COLS:
        if c in h.columns:
            f[c] = last.get(c)
    for c in LEVEL_COLS:
        if c in h.columns:
            v = last.get(c)
            f[c] = float(v) if pd.notna(v) else None

    # --- derived ratios: scale-free and therefore comparable across tokens ---
    liq = float(last.get("liquidity") or 0.0)
    mcap = float(last.get("mcap") or 0.0)
    buys = float(last.get("s5m_numBuys") or 0.0)
    sells = float(last.get("s5m_numSells") or 0.0)
    bvol = float(last.get("s5m_buyVolume") or 0.0)
    svol = float(last.get("s5m_sellVolume") or 0.0)
    holders = float(last.get("holder_count") or 0.0)

    f["log_liq"] = _log1p(liq)
    f["log_mcap"] = _log1p(mcap)
    f["log_holders"] = _log1p(holders)
    f["liq_over_mcap"] = _safe_div(liq, mcap)
    f["vol_over_liq"] = _safe_div(bvol + svol, liq)
    f["buy_sell_ratio"] = _safe_div(buys, buys + sells, 0.5)
    f["vol_imbalance"] = _safe_div(bvol - svol, bvol + svol)
    f["net_sol_inflow"] = bvol - svol
    f["trades_total"] = buys + sells
    f["log_trades"] = _log1p(buys + sells)
    f["avg_trade_size"] = _safe_div(bvol + svol, buys + sells)
    # Liquidity accumulation speed. The strongest published predictor of
    # graduation in a 655,770-token census: tokens that reach a given SOL
    # level in <=10 trades graduate far more often than those needing 1,000+.
    # It inverts the naive "many trades means interest" reading, which mostly
    # measures bots and wash trades - what matters is SOL arriving per trade,
    # not trades arriving.
    f["sol_per_trade"] = _safe_div(bvol, buys)
    f["liq_per_trade"] = _safe_div(liq, buys + sells)
    f["accum_speed"] = _safe_div(liq, (buys + sells) * max(1.0, float(last.get("age_s") or 1.0)) / 60.0)
    f["holders_per_trade"] = _safe_div(holders, buys + sells)
    f["traders_over_trades"] = _safe_div(last.get("s5m_numTraders"), buys + sells)
    f["liq_per_holder"] = _safe_div(liq, holders)

    # Volume is the least trustworthy field available: roughly a fifth of
    # pre-migration pump.fun transaction volume is wash trading, and on an
    # individual token it can be nearly all of it. Carry both the suspicion
    # score and a corrected volume so downstream code never has to trust the
    # raw number.
    from ..signals.washtrade import assess as _wash_assess

    wash = _wash_assess(f)
    f.update(wash.as_features())
    f["vol_corrected"] = (bvol + svol) * wash.discount
    f["vol_corrected_over_liq"] = _safe_div(f["vol_corrected"], liq)

    # Serial-launcher prior. A creator on their 20,000th mint is running a
    # factory; a creator on their first has at least some skin in the game.
    dm = last.get("dev_mints")
    f["log_dev_mints"] = _log1p(float(dm)) if pd.notna(dm) else None
    f["dev_is_fresh"] = int(float(dm) <= 3) if pd.notna(dm) else None
    f["dev_is_factory"] = int(float(dm) >= 100) if pd.notna(dm) else None

    # Creator's realized track record, scored strictly from launches that
    # happened before this one. Measured walk-forward on our own census the
    # lift is 2.09x - real, but an order of magnitude below the 35-110x that
    # gets published, because those figures select the elite tier on the same
    # statistic they then report.
    if _DEPLOYER_BOOK is not None:
        at = f.get("created_at") or f.get("decision_at")
        if at:
            try:
                f.update(_DEPLOYER_BOOK.score_at(last.get("dev"), float(at)))
            except Exception:
                pass

    # --- trajectory: the shape of the path to T, not just the level at T ---
    times = h["age_s"].astype(float).tolist()
    for col, key in (("price_usd", "px"), ("liquidity", "liq"), ("holder_count", "hold")):
        if col not in h.columns:
            continue
        vals = pd.to_numeric(h[col], errors="coerce").tolist()
        clean = [(t, v) for t, v in zip(times, vals) if v is not None and pd.notna(v)]
        if len(clean) < 2:
            f[f"{key}_slope"] = 0.0
            f[f"{key}_chg"] = 0.0
            f[f"{key}_accel"] = 0.0
            f[f"{key}_maxdd"] = 0.0
            continue
        tt = [c[0] for c in clean]
        vv = [float(c[1]) for c in clean]
        f[f"{key}_slope"] = _slope(tt, [math.log1p(max(v, 0.0)) for v in vv])
        f[f"{key}_chg"] = _safe_div(vv[-1] - vv[0], abs(vv[0]) if vv[0] else None)
        mid = len(clean) // 2
        if mid >= 1 and len(clean) - mid >= 2:
            s1 = _slope(tt[:mid + 1], [math.log1p(max(v, 0.0)) for v in vv[:mid + 1]])
            s2 = _slope(tt[mid:], [math.log1p(max(v, 0.0)) for v in vv[mid:]])
            f[f"{key}_accel"] = s2 - s1
        else:
            f[f"{key}_accel"] = 0.0
        peak = max(vv)
        f[f"{key}_maxdd"] = _safe_div(peak - vv[-1], peak) if peak > 0 else 0.0

    # Price relative to its own path so far.
    px = pd.to_numeric(h["price_usd"], errors="coerce").dropna()
    if len(px) >= 2:
        f["px_from_first"] = _safe_div(float(px.iloc[-1]), float(px.iloc[0]), 1.0)
        f["px_from_peak"] = _safe_div(float(px.iloc[-1]), float(px.max()), 1.0)
        f["px_vol"] = float(np.std(np.diff(np.log(px.clip(lower=1e-18))))) if len(px) > 2 else 0.0
    else:
        f["px_from_first"] = 1.0
        f["px_from_peak"] = 1.0
        f["px_vol"] = 0.0
    return f


# A first print at or below this is a data artefact, not a tradable price; the
# ratio it produces is meaningless and inflates every aggregate that touches it.
MIN_VALID_PRICE = 1e-12
# Nothing above this is a real tradable outcome for our size, and leaving the
# tail uncapped lets a single artefact dominate any mean or any model fit.
MAX_VALID_MULTIPLE = 200.0


def label_forward(
    hist: pd.DataFrame,
    age: float,
    horizon_s: float,
    entry_price: float,
) -> dict[str, Any]:
    """Outcome over (age, age + horizon]. Strictly after the decision point."""
    fut = hist[(hist["age_s"] > age) & (hist["age_s"] <= age + horizon_s)]
    fut = fut[fut["price_usd"].notna() & (fut["price_usd"] > 0)]
    out: dict[str, Any] = {
        "horizon_s": horizon_s,
        "n_future_obs": int(len(fut)),
    }
    if fut.empty or entry_price <= MIN_VALID_PRICE:
        out.update(max_x=None, end_x=None, min_x=None, t_to_peak=None, max_liq=None)
        return out
    px = pd.to_numeric(fut["price_usd"], errors="coerce").dropna()
    mx = float(px.max())
    raw_max = mx / entry_price
    if raw_max > MAX_VALID_MULTIPLE:
        # Almost always a near-zero first print rather than a 1000x. Recorded so
        # it can be inspected, and excluded from anything that averages.
        out["degenerate"] = True
    out["max_x"] = min(raw_max, MAX_VALID_MULTIPLE)
    out["end_x"] = min(float(px.iloc[-1]) / entry_price, MAX_VALID_MULTIPLE)
    out["min_x"] = float(px.min()) / entry_price
    peak_row = fut.loc[px.idxmax()]
    out["t_to_peak"] = float(peak_row["age_s"] - age)
    out["max_liq"] = float(pd.to_numeric(fut["liquidity"], errors="coerce").max() or 0.0)
    out["max_holders"] = float(pd.to_numeric(fut["holder_count"], errors="coerce").max() or 0.0)
    return out


def build_panel(
    snapshots: pd.DataFrame,
    ages: Iterable[float] = DECISION_AGES,
    horizon_s: float = 3600.0,
    min_future_obs: int = 2,
) -> pd.DataFrame:
    """Cross every token with every decision age to make the training table."""
    if snapshots.empty:
        return pd.DataFrame()
    snapshots = snapshots[snapshots["age_s"].notna()].copy()
    rows: list[dict[str, Any]] = []
    for mint, hist in snapshots.groupby("mint", sort=False):
        hist = hist.sort_values("age_s")
        for age in ages:
            # Require the token to actually have been observed both before and
            # after the checkpoint, or the row is fiction.
            if hist["age_s"].min() > age or hist["age_s"].max() < age:
                continue
            f = features_at(hist, age)
            if f is None:
                continue
            lab = label_forward(hist, age, horizon_s, f["price_usd"])
            if lab["n_future_obs"] < min_future_obs:
                continue
            f.update(lab)
            rows.append(f)
    return pd.DataFrame(rows)
