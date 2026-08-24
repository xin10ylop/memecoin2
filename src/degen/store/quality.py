"""Data-quality guards on the snapshot stream.

Why this exists. An early version of this system reported a strategy returning
+279% with a profit factor of 18, driven by tokens showing 90x to 190x moves.
Inspecting one of them showed its price rising roughly 190-fold while its
reported liquidity sat flat at about $72,000 for the entire run. That is not a
price move; it is two fields disagreeing.

In a constant-product pool the two are mechanically linked. Pool value is
2*sqrt(k*P) in quote terms, so a pure swap that moves price by factor f moves
reported liquidity by sqrt(f): a 100x price move must show a ~10x liquidity
move. When price jumps and liquidity does not follow, the price field is wrong -
usually a decimals or supply misparse upstream, and disproportionately on
tokens that mimic real tickers.

The guard is deliberately loose. Liquidity genuinely changes for reasons other
than price (LP adds and removes, migration), so it only fires when a *large*
price move is accompanied by a liquidity move far smaller than the square-root
relationship demands. Small moves are never flagged, because the relationship is
swamped by noise there.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Only judge moves larger than this; below it the sqrt relationship is noise.
MIN_MOVE_RATIO = 3.0
# Fraction of the implied liquidity move that must actually be present.
MIN_LIQ_RESPONSE = 0.35
# A token may show one odd print in the 3-5x band; that happens.
MAX_SUSPICIOUS_STEPS = 1
# Above this, a single unsupported step is disqualifying on its own. A 95x price
# move against flat pool depth is not a rare event, it is a broken field - and
# it was exactly this case that a laxer threshold let through, producing a
# fictional 136x trade at the top of the results table.
IMPOSSIBLE_MOVE_RATIO = 5.0


def flag_price_liquidity_divergence(hist: pd.DataFrame) -> dict[str, Any]:
    """Judge one token's snapshot series. `hist` must be sorted by time."""
    out = {"ok": True, "bad_steps": 0, "impossible_steps": 0, "worst_ratio": 1.0, "reason": ""}
    if len(hist) < 2:
        return out
    p = pd.to_numeric(hist.get("price_usd"), errors="coerce").to_numpy(dtype="float64")
    liq = pd.to_numeric(hist.get("liquidity"), errors="coerce").to_numpy(dtype="float64")
    ok = np.isfinite(p) & np.isfinite(liq) & (p > 0) & (liq > 0)
    p, liq = p[ok], liq[ok]
    if len(p) < 2:
        return out

    bad = 0
    impossible = 0
    worst = 1.0
    for i in range(1, len(p)):
        f = p[i] / p[i - 1]
        if f <= 0:
            continue
        move = max(f, 1.0 / f)
        if move < MIN_MOVE_RATIO:
            continue
        implied = math.sqrt(move)                      # expected liquidity factor
        actual = max(liq[i] / liq[i - 1], liq[i - 1] / liq[i])
        # Liquidity must move at least a fraction of the way the price implies.
        if (actual - 1.0) < MIN_LIQ_RESPONSE * (implied - 1.0):
            bad += 1
            worst = max(worst, move)
            if move >= IMPOSSIBLE_MOVE_RATIO:
                impossible += 1
    out["bad_steps"] = bad
    out["impossible_steps"] = impossible
    out["worst_ratio"] = round(worst, 2)
    if impossible >= 1:
        out["ok"] = False
        out["reason"] = (
            f"a {worst:.0f}x price step with no matching liquidity response "
            "(price field inconsistent with pool depth)"
        )
    elif bad > MAX_SUSPICIOUS_STEPS:
        out["ok"] = False
        out["reason"] = f"{bad} unsupported price steps up to {worst:.0f}x"
    return out


def clean_snapshots(snaps: pd.DataFrame, report: bool = False) -> pd.DataFrame:
    """Drop tokens whose price series contradicts their liquidity series."""
    if snaps.empty:
        return snaps
    keep: list[str] = []
    dropped: list[tuple[str, str]] = []
    for mint, hist in snaps.sort_values("observed_at").groupby("mint", sort=False):
        v = flag_price_liquidity_divergence(hist)
        if v["ok"]:
            keep.append(mint)
        else:
            dropped.append((str(mint), v["reason"]))
    if report:
        print(f"data quality: kept {len(keep)} mints, dropped {len(dropped)} "
              f"({100*len(dropped)/max(1,len(keep)+len(dropped)):.1f}%) for price/liquidity divergence")
        for m, why in dropped[:5]:
            print(f"  {m[:14]} {why}")
    return snaps[snaps.mint.isin(set(keep))]


# Columns the feature builder and backtester actually read. The raw snapshot
# frame carries ~143 columns; holding all of them through a groupby made the
# analysis scripts run out of memory and be killed silently.
NEEDED_COLS = (
    "mint", "symbol", "dev", "launchpad", "token_program", "tags",
    "observed_at", "created_at", "age_s", "decimals",
    "price_usd", "liquidity", "mcap", "fdv", "holder_count",
    "top_holders_pct", "dev_balance_pct", "dev_mints", "dev_migrations",
    "organic_score", "mint_auth_disabled", "freeze_auth_disabled",
    "has_socials", "telegram", "twitter", "website",
    "s5m_numBuys", "s5m_numSells", "s5m_numTraders", "s5m_numNetBuyers",
    "s5m_buyVolume", "s5m_sellVolume", "s5m_priceChange", "s5m_liquidityChange",
    "s1h_numBuys", "s1h_numSells", "s1h_numTraders", "s1h_numNetBuyers",
    "s1h_buyVolume", "s1h_sellVolume", "s1h_priceChange",
)


def trim(df, extra: tuple[str, ...] = ()):
    """Keep only the columns downstream code reads."""
    want = [c for c in (*NEEDED_COLS, *extra) if c in df.columns]
    return df[want] if want else df


SEED_PATH = Path(__file__).resolve().parents[3] / "data" / "seed" / "snapshots_seed.parquet"


def load_seed():
    """The committed census, already cleaned and trimmed.

    Lets a fresh clone run every analysis before collecting anything, and keeps
    the reported results reproducible. It is under three hours of real uptime
    from one afternoon - see data/seed/README.md before drawing conclusions from
    it alone.
    """
    import pandas as pd

    if not SEED_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(SEED_PATH)


def load_clean(dataset: str = "snapshots", report: bool = False, lean: bool = True,
               allow_seed: bool = True):
    """The standard way to load the panel: usable rows only, artefacts removed.

    Every analysis path should go through this rather than reading the lake
    directly, so a data artefact cannot quietly become a headline result again.
    """
    from .lake import lake

    df = lake().df(dataset)
    if df.empty:
        if allow_seed and dataset == "snapshots":
            seed = load_seed()
            if not seed.empty and report:
                print(f"lake is empty; using the committed seed census "
                      f"({len(seed):,} snapshots, {seed.mint.nunique():,} mints)")
            return seed
        return df
    df = df[df["price_usd"].notna() & (df["price_usd"] > 0)]
    if "age_s" in df.columns:
        df = df[df["age_s"].notna()]
    if lean:
        df = trim(df)
    return clean_snapshots(df, report=report)
