"""The cost stack for one Solana swap.

Numbers come from direct on-chain measurement of 272 successful pump.fun
transactions across 14 consecutive blocks (research/01_latency_infra.md), not
from provider marketing:

* Landed compute-unit price on pump.fun: p50 264,166 / p75 812,534 /
  p90 4,000,000 microlamports per CU. The *global* prioritization floor at the
  same moment was ~2,986 - pump.fun runs a local fee market two orders of
  magnitude hotter, so any generic fee estimator underprices a snipe badly.
* Compute units consumed: pump.fun bonding curve median 153,860;
  PumpSwap AMM median 85,938; Raydium AMM v4 median 142,828.
* Jito tips: only 12% of landed pump.fun txs tip at all; among tippers the
  distribution is bimodal, p50 10,000 lamports but p90 3,000,000.
* Venue fees are higher than commonly quoted: the pump.fun bonding curve takes
  1.25% per side (0.95% protocol + 0.30% creator), and PumpSwap scales 1.25%
  down to a 0.30% floor with market cap. Published estimates put realistic
  round-trip friction at 4-6% once slippage and MEV are included.

The important consequence for strategy design: fixed per-transaction cost is
small (routine entry ~0.0001 SOL) while *proportional* cost - the AMM fee plus
price impact on both sides - is large and is what actually sets the hurdle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LAMPORTS_PER_SOL = 1_000_000_000
BASE_FEE_LAMPORTS = 5_000

Tier = Literal["routine", "contested", "aggressive"]

# (cu_price_microlamports, jito_tip_lamports) by urgency.
TIERS: dict[Tier, tuple[int, int]] = {
    "routine": (500_000, 0),
    "contested": (2_000_000, 1_000_000),
    "aggressive": (4_000_000, 3_000_000),
}

CU_BUDGET = {
    "pumpfun": 150_000,
    "pumpswap": 100_000,
    "raydium": 150_000,
    "jupiter": 220_000,   # router hops cost more
}


@dataclass
class CostModel:
    tier: Tier = "routine"
    venue: str = "pumpfun"
    # Fee charged by a front-end/sniper platform, if one is used. Self-hosted
    # execution is 0; Photon/BullX/Trojan style platforms are 0.5-1.0%.
    platform_fee: float = 0.0
    # Fraction of buy attempts that fail to land and are retried. A failed tx
    # still burns the base fee and the priority fee.
    fail_rate: float = 0.12
    sol_usd: float = 94.0
    # Sandwich losses on an unprotected swap run ~3% versus ~0.7% protected.
    # Charged as an extra proportional cost per side when not using a private
    # or bundled submission path.
    mev_loss: float = 0.0


    def fixed_sol_per_tx(self) -> float:
        cu_price, tip = TIERS[self.tier]
        cu = CU_BUDGET.get(self.venue, 150_000)
        priority = cu_price * cu / 1_000_000  # microlamports*CU -> lamports
        total = BASE_FEE_LAMPORTS + priority + tip
        # Retries: expected number of attempts is 1/(1-fail_rate).
        attempts = 1.0 / max(1e-6, (1.0 - self.fail_rate))
        return (total * attempts) / LAMPORTS_PER_SOL

    def amm_fee(self) -> float:
        """Venue fee per side.

        pump.fun's bonding curve is 1.25%, not the 1% commonly quoted: 0.95%
        protocol plus a 0.30% creator fee. PumpSwap starts at the same 1.25% and
        scales down to a 0.30% floor as market cap rises, reaching the floor
        around 98,240 SOL of market cap; 0.85% is a reasonable blended default
        for the small-cap range this bot actually trades.
        """
        return {
            "pumpfun": 0.0125,
            "pumpswap": 0.0085,
            "raydium": 0.0025,
            "meteora": 0.0025,
        }.get(self.venue, 0.0030)

    def entry_overhead(self, sol_in: float) -> float:
        """SOL consumed by an entry beyond what reaches the pool."""
        return self.fixed_sol_per_tx() + sol_in * (self.platform_fee + self.mev_loss)

    def exit_overhead(self, sol_out: float) -> float:
        return self.fixed_sol_per_tx() + sol_out * (self.platform_fee + self.mev_loss)

    def breakeven_hit_rate(self, target_x: float, loss_frac: float = 1.0, n_tx: int = 4,
                           position_sol: float = 0.5) -> float:
        """Hit rate needed for a target-multiple strategy to break even.

        Published figures for reference, at ~5% round-trip friction with total
        loss on losers: a 2x target needs 52.5%, 5x needs 21.0%, 10x needs
        10.5%. This reproduces that arithmetic with the configured cost stack.
        """
        friction = n_tx * self.fixed_sol_per_tx() / max(position_sol, 1e-9)
        friction += 2 * (self.amm_fee() + self.platform_fee + self.mev_loss)
        net_win = target_x * (1 - friction) - 1.0
        net_loss = loss_frac + friction
        if net_win <= 0:
            return 1.0
        return float(net_loss / (net_win + net_loss))

    def describe(self, position_sol: float) -> dict[str, float]:
        fx = self.fixed_sol_per_tx()
        return {
            "fixed_sol_per_tx": fx,
            "fixed_bps_of_position": 1e4 * fx / position_sol if position_sol else 0.0,
            "amm_fee_bps_each_way": 1e4 * self.amm_fee(),
            "platform_bps_each_way": 1e4 * self.platform_fee,
            "usd_per_tx": fx * self.sol_usd,
        }
