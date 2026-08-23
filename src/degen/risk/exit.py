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
   on assets whose entire life is measured in minutes.

Every threshold is a parameter because every one of them should be fit on data
rather than asserted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Reason = Literal[
    "ladder", "cost_recovery", "trail", "hard_stop", "time_stop",
    "liquidity_collapse", "end_of_data", "rug",
]


@dataclass
class ExitConfig:
    # (multiple_of_entry, fraction_of_ORIGINAL_position_to_sell)
    ladder: tuple[tuple[float, float], ...] = (
        (1.6, 0.40),   # recover most of the stake early - hit rate is low
        (2.5, 0.25),
        (4.0, 0.15),
        (8.0, 0.10),
        (20.0, 0.05),
    )
    # Trailing stop as drawdown-from-peak, tightening as the trade matures.
    # (peak_multiple_reached, allowed_drawdown_from_peak)
    trail_schedule: tuple[tuple[float, float], ...] = (
        (1.5, 0.45),
        (3.0, 0.35),
        (6.0, 0.28),
        (15.0, 0.22),
    )
    hard_stop_x: float = 0.55           # cut at -45% from entry
    time_stop_s: float = 900.0          # 15 min to show something
    time_stop_min_x: float = 1.12       # ...unless it is already up 12%
    max_hold_s: float = 6 * 3600.0      # absolute cap
    liq_collapse_frac: float = 0.45     # exit if pool drops to <45% of entry depth
    min_liq_usd: float = 700.0          # unexitable below this


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

    def __post_init__(self) -> None:
        self.peak_price = self.entry_price


@dataclass
class ExitDecision:
    sell_frac_of_original: float
    reason: Reason
    close: bool


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
) -> ExitDecision | None:
    """Decide what to do with an open position at the current observation."""
    if pos.remaining_frac <= 1e-9 or price <= 0:
        return None
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
