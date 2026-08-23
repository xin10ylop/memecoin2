"""Market regime detection.

The single most important correction from the published literature: the
pump.fun graduation rate is not a constant, it is a regime variable. Measured
values across periods range from 0.198% (May-Jun 2026) through 0.63%, 0.84%,
1.15% and 1.79% to 4.7-6.7% immediately after the BOOST mechanic launched in
late July 2026 - a factor of thirty between the extremes.

A strategy calibrated in a hot regime and run in a cold one is not the same
strategy. Rather than re-tune by hand, the bot measures the regime continuously
from its own collected data and scales its aggression: how many positions it
will hold, how large they are, and how selective the score threshold is.

The proxy used is the fraction of recently launched tokens reaching meaningful
liquidity, which is observable from the collector's own census without needing
anyone else's graduation feed, and moves with the same underlying quantity.

An important caveat about the thresholds below. The published graduation rates
measure completion of the bonding curve at roughly $69k market cap; this proxy
measures reaching $10k of pool liquidity, which is a considerably lower bar and
therefore runs several times higher. The two are *not* interchangeable, and the
band edges here are set on the proxy's own scale - anchored on ~6% measured
during development - not transplanted from the graduation literature. They are
starting values. `calibrate()` re-derives them from the operator's own history
once there is enough of it, and until that has been run the bands should be
treated as provisional.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..util.log import get
from ..util.timeutil import now

log = get("degen.regime")

Band = Literal["dead", "defensive", "normal", "aggressive"]

# Band edges on the *proxy* scale (fraction of launches reaching $10k
# liquidity), not on the graduation scale. Provisional until calibrate() runs.
BANDS: tuple[tuple[float, Band], ...] = (
    (0.010, "dead"),
    (0.030, "defensive"),
    (0.100, "normal"),
    (1.000, "aggressive"),
)


@dataclass
class RegimeState:
    band: Band = "normal"
    grad_proxy: float = 0.0
    n_launches: int = 0
    n_reached: int = 0
    launches_per_hour: float = 0.0
    median_peak_x: float = 1.0
    measured_at: float = 0.0
    confident: bool = False

    # Multipliers applied to the trading configuration.
    size_mult: float = 1.0
    max_positions_mult: float = 1.0
    threshold_delta: float = 0.0

    def describe(self) -> str:
        conf = "" if self.confident else " (low confidence)"
        return (
            f"regime={self.band}{conf} grad_proxy={100*self.grad_proxy:.2f}% "
            f"({self.n_reached}/{self.n_launches}) launches={self.launches_per_hour:.0f}/h "
            f"size×{self.size_mult:.2f} positions×{self.max_positions_mult:.2f} "
            f"threshold{self.threshold_delta:+.2f}"
        )


# How each band adjusts trading. Cold regimes are not merely less profitable,
# they are differently shaped: fewer tokens reach the liquidity where an exit
# exists, so both size and concurrency come down and selectivity goes up.
POLICY: dict[Band, tuple[float, float, float]] = {
    # band          size   positions  threshold delta
    "dead":        (0.25,   0.34,      +0.15),
    "defensive":   (0.55,   0.67,      +0.07),
    "normal":      (1.00,   1.00,       0.00),
    "aggressive":  (1.25,   1.34,      -0.03),
}

MIN_LAUNCHES_FOR_CONFIDENCE = 300


def classify(grad_proxy: float, bands: tuple[tuple[float, Band], ...] | None = None) -> Band:
    for threshold, band in (bands or BANDS):
        if grad_proxy < threshold:
            return band
    return "aggressive"


def calibrate(lake_con: Any, days: float = 14.0, liq_threshold: float = 10_000.0) -> tuple[tuple[float, Band], ...]:
    """Re-derive band edges from the operator's own history.

    Splits the observed distribution of the daily proxy rate at its 20th, 40th
    and 80th percentiles, so "normal" means normal *for this operator's data*
    rather than for whatever conditions prevailed during development. Returns
    the existing bands unchanged when there is not enough history to beat them.
    """
    import numpy as np

    cutoff = now() - days * 86400
    try:
        df = lake_con.execute(
            """
            with f as (
              select mint, price_usd p0, observed_at t0, age_s a0 from (
                select *, row_number() over (partition by mint order by observed_at) rn
                from snapshots where price_usd > 0) where rn = 1)
            select date_trunc('day', to_timestamp(f.t0)) d,
                   count(*) n,
                   sum(case when x.liqmax >= ? then 1 else 0 end) reached
            from f join (
              select mint, max(liquidity) liqmax, count(*) c
              from snapshots where price_usd > 0 group by mint) x on x.mint = f.mint
            where f.t0 >= ? and f.a0 < 300 and x.c >= 4
            group by 1 having count(*) >= 100
            """,
            [liq_threshold, cutoff],
        ).df()
    except Exception as exc:
        log.warning("regime calibration failed: %s", exc)
        return BANDS
    if len(df) < 5:
        log.info("regime calibration needs >=5 days of >=100 launches; have %d", len(df))
        return BANDS
    rates = (df["reached"] / df["n"]).values
    q20, q40, q80 = (float(np.percentile(rates, q)) for q in (20, 40, 80))
    bands = ((q20, "dead"), (q40, "defensive"), (q80, "normal"), (1.0, "aggressive"))
    log.info("calibrated regime bands from %d days: %s", len(df),
             [(round(b, 4), n) for b, n in bands])
    return bands  # type: ignore[return-value]


def measure(
    lake_con: Any,
    window_h: float = 6.0,
    liq_threshold: float = 10_000.0,
    min_obs: int = 4,
    bands: tuple[tuple[float, Band], ...] | None = None,
) -> RegimeState:
    """Measure the current regime from the collector's own census."""
    cutoff = now() - window_h * 3600
    try:
        df = lake_con.execute(
            """
            with f as (
              select mint, price_usd p0, observed_at t0, age_s a0 from (
                select *, row_number() over (partition by mint order by observed_at) rn
                from snapshots where price_usd > 0) where rn = 1)
            select f.mint, f.t0,
                   max(s.liquidity) liqmax,
                   max(s.price_usd) / f.p0 maxx,
                   count(*) n
            from snapshots s join f on s.mint = f.mint
            where s.price_usd > 0 and f.t0 >= ? and f.a0 < 300
            group by f.mint, f.p0, f.t0
            """,
            [cutoff],
        ).df()
    except Exception as exc:
        log.warning("regime measurement failed: %s", exc)
        return RegimeState(measured_at=now())

    df = df[(df["n"] >= min_obs) & (df["maxx"] < 1e5)]
    n = len(df)
    if n == 0:
        return RegimeState(band="normal", measured_at=now(), confident=False)

    reached = int((df["liqmax"] >= liq_threshold).sum())
    proxy = reached / n
    band = classify(proxy, bands)
    size_m, pos_m, thr_d = POLICY[band]
    confident = n >= MIN_LAUNCHES_FOR_CONFIDENCE

    if not confident:
        # Not enough evidence to justify a large deviation; pull the multipliers
        # partway back toward neutral rather than acting on a noisy estimate.
        w = n / MIN_LAUNCHES_FOR_CONFIDENCE
        size_m = 1.0 + (size_m - 1.0) * w
        pos_m = 1.0 + (pos_m - 1.0) * w
        thr_d = thr_d * w

    span_h = max(1e-6, (df["t0"].max() - df["t0"].min()) / 3600.0) if n > 1 else window_h
    return RegimeState(
        band=band,
        grad_proxy=proxy,
        n_launches=n,
        n_reached=reached,
        launches_per_hour=n / span_h,
        median_peak_x=float(df["maxx"].median()),
        measured_at=now(),
        confident=confident,
        size_mult=round(size_m, 3),
        max_positions_mult=round(pos_m, 3),
        threshold_delta=round(thr_d, 3),
    )
