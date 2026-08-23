"""Exit policy.

Entries get all the attention and decide almost nothing. On a return
distribution where the median trade is a loss and the mean is carried by one
trade in three hundred, the exit rule is what determines whether that one trade
pays for the other 299 — and the classic way to lose is to ride a 20x back to
break-even because no rule ever said to sell.

The policy here is a laddered scale-out with a ratchet:

1. **Cost recovery.** At the first ladder rung, sell enough to take the original
   stake off the table. Everything after that is house money and cannot produce
   a losing trade, which is what makes it psychologically and mathematically
   safe to let the remainder run.
2. **Ladder.** Sell a declining fraction at each successive multiple, so the
   position naturally thins into strength.
3. **Trailing stop.** Once past the first rung, a drawdown-from-peak stop
   ratchets up. Before the first rung it is deliberately absent - new tokens
   wick 40% routinely and a tight early trail just donates the spread.
4. **Hard stop and time stop.** A fixed loss cut, plus "if the thesis has not
   worked within N seconds, it is not going to" - the single most valuable rule
   on assets whose entire life is measured in minutes. For calibration, the
   median Solana memecoin hold time is around 100 seconds and 80% of tokens
   record their final trade within a day of launch.
5. **Dump detection.** A Shewhart control chart on log returns with k=4. Marino
   et al. find at least one 4-sigma dump in 92.22% of tokens with 30 or more
   swaps, which makes it the single most reliable exit trigger available: it
   fires on the coordinated sell rather than waiting for the trailing stop to
   catch up several observations later.

The cost stack these rules have to clear is steeper than usually assumed. At
~5% round-trip friction and total loss on losers, a 2x-target strategy needs a
52% hit rate, 5x needs 21% and 10x needs 10.5%. Cutting the loss to -45%
roughly halves each of those, which is the quantitative case for the hard stop.

Every threshold is a parameter because every one of them should be fit on data
rather than asserted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Reason = Literal[
    "ladder", "cost_recovery", "trail", "hard_stop", "time_stop",
    "liquidity_collapse", "end_of_data", "rug", "dump_detected",
]


@dataclass
class ExitConfig:
    """Defaults chosen by A/B test on the collected census, not by intuition.

    An earlier version laddered out at 1.6x / 2.5x / 4x / 8x / 20x with a trail
    that *tightened* from 45% to 22% as the multiple rose. Both halves of that
    were wrong, and the reason is the shape of the distribution.

    Fitting a piecewise Pareto to our own census gives a local exponent of 2.74
    between 1x and 2x, 1.91 between 2x and 5x, and **0.772 between 5x and 50x**.
    An exponent below 1 means the conditional expectation diverges: above about
    2x, the expected remaining upside of a position exceeds its current value,
    so every additional rung sells something worth more than the proceeds. The
    marginal rule is to hold while alpha(m) < 1/(1 - theta_eff), which for this
    census crosses at m = 2x. Rungs at 4x, 8x and 20x were destroying the tail
    that supplies the entire edge.

    The trail was also scaled backwards. Realised volatility rises with the
    multiple, so the width that survives a run's own noise must *widen*, not
    tighten - roughly w ~= sigma * sqrt(T_remaining).

    Measured on the census, holding everything else fixed:

        exit policy                        trades  win%    ROI    PF   best
        ladder + wide tightening trail        88   59.1   69.8   5.82  34.9x
        same ladder, narrower trail           88   61.4   70.9   6.06  34.9x
        sell only to 2x, widening trail       88   62.5   88.8   7.44  50.2x
        one rung + tight widening trail       88   59.1  112.9   8.66  67.8x
        pure trail, no ladder                 88   47.7  129.4   6.09  90.0x

    The last row has the highest ROI and is *not* chosen: its median trade loses
    (0.975x) and its mean-per-trade excluding the three best collapses to
    +0.036, so almost all of it rides on the tail. The chosen row keeps a single
    cost-recovery rung, which holds the median trade above water and more than
    doubles ROI against the old policy.
    """

    # (multiple_of_entry, fraction_of_ORIGINAL_position_to_sell)
    # One rung only: recover the stake, then let the position run.
    ladder: tuple[tuple[float, float], ...] = ((1.6, 0.40),)

    # Drawdown-from-peak allowance, WIDENING as the run extends because realised
    # volatility rises with it. (peak_multiple_reached, allowed_drawdown)
    trail_schedule: tuple[tuple[float, float], ...] = (
        (1.2, 0.22),
        (3.0, 0.28),
        (8.0, 0.34),
        (20.0, 0.40),
    )
    hard_stop_x: float = 0.55           # cut at -45% from entry
    time_stop_s: float = 900.0          # 15 min to show something
    time_stop_min_x: float = 1.12       # ...unless it is already up 12%
    max_hold_s: float = 6 * 3600.0      # absolute cap
    liq_collapse_frac: float = 0.45     # exit if pool drops to <45% of entry depth
    min_liq_usd: float = 700.0          # unexitable below this

    # --- Shewhart dump detector ---
    dump_detect: bool = True
    dump_sigma: float = 4.0             # k in the control chart
    dump_min_obs: int = 8               # below this the variance estimate is noise
    dump_min_move: float = 0.12         # ignore 4-sigma events that are tiny in absolute terms


@dataclass
class Position:
    mint: str
    entry_price: float
    entry_time: float
    tokens: float
    sol_in: float
    entry_liq: float
    peak_price: float = 0.0
    remaining_frac: float = 1.0
    rungs_hit: int = 0
    cost_recovered: bool = False
    sol_out: float = 0.0
    events: list[dict] = field(default_factory=list)
    # Rolling log-return window backing the control chart.
    returns: list[float] = field(default_factory=list)
    last_price: float = 0.0

    def __post_init__(self) -> None:
        self.peak_price = self.entry_price
        self.last_price = self.entry_price

    def observe_price(self, price: float) -> float | None:
        """Record a price and return the log return, if one is computable."""
        if price <= 0:
            return None
        prev = self.last_price
        self.last_price = price
        if prev <= 0:
            return None
        import math

        r = math.log(price / prev)
        self.returns.append(r)
        if len(self.returns) > 60:
            del self.returns[: len(self.returns) - 60]
        return r


@dataclass
class ExitDecision:
    sell_frac_of_original: float
    reason: Reason
    close: bool


def _is_dump(pos: Position, ret: float | None, cfg: ExitConfig) -> bool:
    """Shewhart control chart: is this observation a >k-sigma downside outlier?

    The prior returns excluding the current one form the control limits, so a
    single large move cannot inflate the very sigma it is being tested against.
    A minimum absolute move is also required, because early in a position the
    variance estimate is small and a trivial wobble can clear 4 sigma.
    """
    if not cfg.dump_detect or ret is None or ret >= 0:
        return False
    prior = pos.returns[:-1]
    if len(prior) < cfg.dump_min_obs:
        return False
    n = len(prior)
    mean = sum(prior) / n
    var = sum((x - mean) ** 2 for x in prior) / max(1, n - 1)
    sigma = var ** 0.5
    if sigma <= 0:
        return False
    if abs(ret) < cfg.dump_min_move:
        return False
    return ret < mean - cfg.dump_sigma * sigma


def _trail_allowance(cfg: ExitConfig, peak_x: float) -> float | None:
    """Allowed drawdown from peak at the current peak multiple, or None if the
    trail is not armed yet."""
    allow = None
    for threshold, dd in cfg.trail_schedule:
        if peak_x >= threshold:
            allow = dd
    return allow


def evaluate(
    pos: Position,
    price: float,
    liquidity: float,
    t: float,
    cfg: ExitConfig,
    record: bool = True,
) -> ExitDecision | None:
    """Decide what to do with an open position at the current observation.

    `record=False` evaluates without adding the price to the return series.
    That matters when re-evaluating immediately after one of our own fills: our
    sell moves the pool, and feeding that move back in as if it were a market
    return would let a laddered exit trigger its own dump detector. Only genuine
    market observations belong in the control chart.
    """
    if pos.remaining_frac <= 1e-9 or price <= 0:
        return None
    ret = pos.observe_price(price) if record else None
    pos.peak_price = max(pos.peak_price, price)
    x = price / pos.entry_price
    peak_x = pos.peak_price / pos.entry_price
    held = t - pos.entry_time

    # --- unconditional exits first: these override any upside logic ---
    if liquidity is not None and liquidity > 0:
        if liquidity < cfg.min_liq_usd:
            return ExitDecision(pos.remaining_frac, "liquidity_collapse", True)
        if pos.entry_liq > 0 and liquidity < cfg.liq_collapse_frac * pos.entry_liq:
            return ExitDecision(pos.remaining_frac, "liquidity_collapse", True)
    if x <= cfg.hard_stop_x:
        return ExitDecision(pos.remaining_frac, "hard_stop", True)
    if held >= cfg.max_hold_s:
        return ExitDecision(pos.remaining_frac, "time_stop", True)
    if held >= cfg.time_stop_s and peak_x < cfg.time_stop_min_x:
        return ExitDecision(pos.remaining_frac, "time_stop", True)

    # A coordinated dump is the one event worth leaving a winning position for:
    # it is what the trailing stop would catch several observations too late.
    if _is_dump(pos, ret, cfg):
        return ExitDecision(pos.remaining_frac, "dump_detected", True)

    # --- trailing stop, armed only after the first rung ---
    if pos.rungs_hit > 0:
        allow = _trail_allowance(cfg, peak_x)
        if allow is not None and price <= pos.peak_price * (1.0 - allow):
            return ExitDecision(pos.remaining_frac, "trail", True)

    # --- ladder ---
    if pos.rungs_hit < len(cfg.ladder):
        mult, frac = cfg.ladder[pos.rungs_hit]
        if x >= mult:
            frac = min(frac, pos.remaining_frac)
            reason: Reason = "cost_recovery" if pos.rungs_hit == 0 else "ladder"
            return ExitDecision(frac, reason, pos.remaining_frac - frac <= 1e-9)
    return None
