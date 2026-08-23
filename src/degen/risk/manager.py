"""Portfolio risk control.

The strategy's edge, if it exists at all, is small and fat-tailed. What
reliably destroys accounts running strategies like this is not a bad entry rule
- it is sizing, correlation, and the absence of a stop on the *system* rather
than on the trade.

Sizing uses a heavily fractional Kelly. Full Kelly on an edge estimated from a
few hundred trades is a mathematical guarantee of ruin, because the estimate's
error is larger than the edge. The default is a fifth of Kelly, capped
absolutely, which is roughly the most aggressive setting that survives being
wrong about the win rate by a factor of two.

The kill switches are deliberately blunt. Each one answers "what would tell me
the world has changed and my model no longer applies", and each halts trading
rather than reducing size, because a system that is quietly wrong should stop,
not slow down.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..util.log import get
from ..util.timeutil import now

log = get("degen.risk")


@dataclass
class RiskConfig:
    # --- sizing ---
    bankroll_sol: float = 5.0
    base_frac: float = 0.02              # 2% of bankroll at conviction 1.0
    min_size_sol: float = 0.05
    max_size_sol: float = 0.5
    max_frac_of_pool: float = 0.02       # never exceed 2% of pool depth
    kelly_fraction: float = 0.20         # fraction of full Kelly to use

    # --- concurrency and exposure ---
    max_open_positions: int = 6          # positions with capital still at risk
    # Residuals running on house money still need managing, but they cost
    # nothing to hold; this only bounds bookkeeping and API load.
    max_tracked_positions: int = 24
    max_total_exposure_frac: float = 0.30
    max_per_creator: int = 1             # never hold two tokens from one dev
    cooldown_after_loss_s: float = 90.0
    min_seconds_between_entries: float = 8.0

    # --- circuit breakers ---
    daily_loss_limit_frac: float = 0.15
    max_consecutive_losses: int = 8
    session_loss_limit_frac: float = 0.25
    halt_on_fill_failures: int = 6       # consecutive failed sends -> stop

    # --- sanity ---
    min_bankroll_sol: float = 0.25


@dataclass
class RiskState:
    realized_pnl_sol: float = 0.0
    day_start: float = field(default_factory=now)
    day_pnl_sol: float = 0.0
    consecutive_losses: int = 0
    consecutive_fill_failures: int = 0
    last_entry_at: float = 0.0
    last_loss_at: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    open_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    creators: dict[str, int] = field(default_factory=dict)


class RiskManager:
    def __init__(self, cfg: RiskConfig | None = None) -> None:
        self.cfg = cfg or RiskConfig()
        self.state = RiskState()
        self._start_bankroll = self.cfg.bankroll_sol

    # ---------------- sizing ----------------

    def kelly_size(self, win_prob: float, win_mult: float, loss_frac: float = 1.0) -> float:
        """Fractional Kelly for a binary approximation of the payoff.

        b = net odds on a win, p = win probability, q = 1 - p.
        f* = (b*p - q) / b, then scaled by kelly_fraction.
        """
        b = max(1e-6, win_mult - 1.0) / max(1e-6, loss_frac)
        p = min(max(win_prob, 0.0), 1.0)
        f = (b * p - (1.0 - p)) / b
        return max(0.0, f) * self.cfg.kelly_fraction

    def size_for(
        self,
        conviction: float,
        pool_sol_reserve: float | None = None,
        win_prob: float | None = None,
        win_mult: float = 2.5,
    ) -> tuple[float, str]:
        """Returns (size_in_sol, explanation). Zero means do not trade."""
        c = self.cfg
        if self.state.halted:
            return 0.0, f"halted: {self.state.halt_reason}"
        bankroll = self.bankroll()
        if bankroll < c.min_bankroll_sol:
            return 0.0, f"bankroll {bankroll:.3f} below floor {c.min_bankroll_sol}"

        frac = c.base_frac * max(0.0, min(1.0, conviction))
        why = f"base {c.base_frac:.3f}*conv {conviction:.2f}"
        if win_prob is not None:
            kf = self.kelly_size(win_prob, win_mult)
            frac = min(frac, kf) if kf > 0 else 0.0
            why += f" | kelly({win_prob:.3f},{win_mult:.1f})={kf:.4f}"
            if frac <= 0:
                return 0.0, why + " -> negative edge"

        size = bankroll * frac
        size = min(size, c.max_size_sol)
        if pool_sol_reserve:
            cap = pool_sol_reserve * c.max_frac_of_pool
            if cap < size:
                why += f" | pool cap {cap:.3f}"
            size = min(size, cap)
        if size < c.min_size_sol:
            return 0.0, why + f" | below min size ({size:.4f} < {c.min_size_sol})"
        return round(size, 4), why

    def bankroll(self) -> float:
        return self._start_bankroll + self.state.realized_pnl_sol

    # ---------------- gates ----------------

    def can_enter(self, mint: str, creator: str | None = None) -> tuple[bool, str]:
        c, s = self.cfg, self.state
        self._roll_day()
        if s.halted:
            return False, f"halted: {s.halt_reason}"
        if mint in s.open_positions:
            return False, "already holding"
        # The cap counts positions with capital still at risk. A residual held
        # on house money is free to keep running.
        at_risk = self.at_risk_positions()
        if at_risk >= c.max_open_positions:
            return False, f"max open positions ({c.max_open_positions} at risk)"
        if len(s.open_positions) >= c.max_tracked_positions:
            return False, f"max tracked positions ({c.max_tracked_positions})"
        exposure = self.exposure()
        if exposure >= c.max_total_exposure_frac * self.bankroll():
            return False, f"exposure cap ({exposure:.3f} SOL at risk)"
        if creator and s.creators.get(creator, 0) >= c.max_per_creator:
            return False, f"already holding a token from creator {creator[:8]}"
        t = now()
        if t - s.last_entry_at < c.min_seconds_between_entries:
            return False, "entry rate limit"
        if s.consecutive_losses > 0 and t - s.last_loss_at < c.cooldown_after_loss_s:
            return False, f"cooldown after loss ({c.cooldown_after_loss_s:.0f}s)"
        return True, "ok"

    # ---------------- lifecycle ----------------

    def on_entry(self, mint: str, sol_in: float, creator: str | None = None, meta: dict | None = None) -> None:
        self.state.open_positions[mint] = {
            "sol_in": sol_in, "sol_out": 0.0, "at": now(), "creator": creator, **(meta or {})
        }
        self.state.last_entry_at = now()
        if creator:
            self.state.creators[creator] = self.state.creators.get(creator, 0) + 1

    def on_partial_exit(self, mint: str, sol_out: float) -> None:
        """Record proceeds from a ladder rung without closing the position.

        This is what lets the concurrency cap mean what it is supposed to mean.
        Once a position has returned its cost basis it has no capital at risk,
        so continuing to hold the residual for the tail should not consume a
        slot that a new opportunity could use.
        """
        pos = self.state.open_positions.get(mint)
        if pos is not None:
            pos["sol_out"] = pos.get("sol_out", 0.0) + max(0.0, sol_out)

    def at_risk_positions(self) -> int:
        """Positions that have not yet returned their cost basis."""
        return sum(
            1 for p in self.state.open_positions.values()
            if p.get("sol_out", 0.0) < p.get("sol_in", 0.0)
        )

    def exposure(self) -> float:
        """Capital still at risk, net of proceeds already banked."""
        return sum(
            max(0.0, p.get("sol_in", 0.0) - p.get("sol_out", 0.0))
            for p in self.state.open_positions.values()
        )

    def on_exit(self, mint: str, pnl_sol: float) -> None:
        pos = self.state.open_positions.pop(mint, None)
        if pos and pos.get("creator"):
            k = pos["creator"]
            self.state.creators[k] = max(0, self.state.creators.get(k, 1) - 1)
            if self.state.creators[k] == 0:
                self.state.creators.pop(k, None)
        s = self.state
        s.realized_pnl_sol += pnl_sol
        s.day_pnl_sol += pnl_sol
        if pnl_sol < 0:
            s.consecutive_losses += 1
            s.last_loss_at = now()
        else:
            s.consecutive_losses = 0
        self._check_breakers()

    def on_fill_failure(self) -> None:
        self.state.consecutive_fill_failures += 1
        if self.state.consecutive_fill_failures >= self.cfg.halt_on_fill_failures:
            self._halt(f"{self.state.consecutive_fill_failures} consecutive fill failures")

    def on_fill_success(self) -> None:
        self.state.consecutive_fill_failures = 0

    # ---------------- breakers ----------------

    def _roll_day(self) -> None:
        if now() - self.state.day_start >= 86400:
            self.state.day_start = now()
            self.state.day_pnl_sol = 0.0
            if self.state.halt_reason.startswith("daily"):
                self.state.halted = False
                self.state.halt_reason = ""
                log.info("daily loss halt cleared")

    def _halt(self, reason: str) -> None:
        if not self.state.halted:
            self.state.halted = True
            self.state.halt_reason = reason
            log.error("TRADING HALTED: %s", reason)

    def _check_breakers(self) -> None:
        c, s = self.cfg, self.state
        if s.day_pnl_sol <= -c.daily_loss_limit_frac * self._start_bankroll:
            self._halt(f"daily loss limit: {s.day_pnl_sol:.3f} SOL")
        if s.realized_pnl_sol <= -c.session_loss_limit_frac * self._start_bankroll:
            self._halt(f"session loss limit: {s.realized_pnl_sol:.3f} SOL")
        if s.consecutive_losses >= c.max_consecutive_losses:
            self._halt(f"{s.consecutive_losses} consecutive losses")

    def resume(self) -> None:
        self.state.halted = False
        self.state.halt_reason = ""
        self.state.consecutive_losses = 0
        self.state.consecutive_fill_failures = 0
        log.info("trading resumed by operator")

    def summary(self) -> dict[str, Any]:
        s = self.state
        return {
            "bankroll_sol": round(self.bankroll(), 4),
            "realized_pnl_sol": round(s.realized_pnl_sol, 4),
            "day_pnl_sol": round(s.day_pnl_sol, 4),
            "open_positions": len(s.open_positions),
            "at_risk_positions": self.at_risk_positions(),
            "exposure_sol": round(self.exposure(), 4),
            "consecutive_losses": s.consecutive_losses,
            "halted": s.halted,
            "halt_reason": s.halt_reason,
        }
