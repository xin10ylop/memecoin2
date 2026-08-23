"""AMM fill mathematics.

A backtest that assumes you buy and sell at the mid price will show a profit on
almost any memecoin rule, because the thing that actually kills these strategies
is that you pay to get in and pay far more to get out. Every simulated trade in
this system is priced through the pool it would really have hit.

Two curve types matter on Solana:

* Constant product (Raydium CPMM, PumpSwap, Meteora DLMM approximated as CP for
  a single swap) - x*y = k.
* The pump.fun bonding curve, which is a constant product over *virtual*
  reserves seeded so that price starts low and rises deterministically with
  supply sold. Same algebra, different reserve accounting.

References for the pump.fun defaults: the launch state is 30 virtual SOL and
1,073,000,000 virtual tokens with 793,100,000 real tokens available, and the
curve completes at ~85 SOL of real reserves. These are configurable because
pump.fun has changed them before and forks use their own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LAMPORTS = 1_000_000_000

# pump.fun bonding-curve launch constants (mainnet defaults).
PF_VIRT_SOL = 30.0
PF_VIRT_TOKENS = 1_073_000_000.0
PF_REAL_TOKENS = 793_100_000.0
PF_COMPLETE_SOL = 85.0
PF_FEE = 0.01  # 1% protocol fee on pump.fun swaps


@dataclass(frozen=True)
class Fill:
    """The realized outcome of one simulated swap."""

    qty_in: float
    qty_out: float
    avg_price: float        # quote units per base unit actually paid/received
    mid_price_before: float
    slippage_frac: float    # signed: positive means worse than mid
    fee_paid: float
    pool_frac: float        # trade size as a fraction of the relevant reserve
    ok: bool = True
    reason: str = ""


def cp_out(reserve_in: float, reserve_out: float, amount_in: float, fee: float) -> float:
    """Constant-product output for `amount_in`, fee taken on the input side."""
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0.0
    eff_in = amount_in * (1.0 - fee)
    return reserve_out * eff_in / (reserve_in + eff_in)


def cp_in_for_out(reserve_in: float, reserve_out: float, amount_out: float, fee: float) -> float:
    """Input required to receive exactly `amount_out`. Infinite past the reserve."""
    if amount_out <= 0 or amount_out >= reserve_out:
        return float("inf")
    return (reserve_in * amount_out) / ((reserve_out - amount_out) * (1.0 - fee))


@dataclass
class Pool:
    """A tradable venue for one token, priced in SOL.

    `sol_reserve`/`token_reserve` are the effective reserves — virtual reserves
    for a bonding curve, real reserves for an AMM.
    """

    sol_reserve: float
    token_reserve: float
    fee: float = 0.0025
    kind: Literal["cp", "bonding"] = "cp"
    real_token_reserve: float | None = None  # bonding curve only

    @classmethod
    def from_pumpfun(cls, sol_raised: float = 0.0) -> "Pool":
        """Curve state after `sol_raised` SOL of net buying."""
        vs = PF_VIRT_SOL + sol_raised
        vt = (PF_VIRT_SOL * PF_VIRT_TOKENS) / vs
        return cls(
            sol_reserve=vs,
            token_reserve=vt,
            fee=PF_FEE,
            kind="bonding",
            real_token_reserve=max(0.0, PF_REAL_TOKENS - (PF_VIRT_TOKENS - vt)),
        )

    @classmethod
    def from_liquidity_usd(cls, liquidity_usd: float, price_usd: float, sol_usd: float, fee: float = 0.0025) -> "Pool":
        """Reconstruct reserves from the (liquidity_usd, price_usd) that every
        data API reports. Convention: liquidity_usd counts both sides, so each
        side is half of it. This is the standard DexScreener/Jupiter meaning."""
        if liquidity_usd <= 0 or price_usd <= 0 or sol_usd <= 0:
            return cls(0.0, 0.0, fee)
        side_usd = liquidity_usd / 2.0
        return cls(sol_reserve=side_usd / sol_usd, token_reserve=side_usd / price_usd, fee=fee)

    @property
    def mid(self) -> float:
        """SOL per token at zero size."""
        if self.token_reserve <= 0:
            return 0.0
        return self.sol_reserve / self.token_reserve

    def buy(self, sol_in: float, max_pool_frac: float = 0.35) -> Fill:
        """Spend `sol_in` SOL, receive tokens."""
        mid = self.mid
        if sol_in <= 0 or self.sol_reserve <= 0 or self.token_reserve <= 0:
            return Fill(sol_in, 0, 0, mid, 0, 0, 0, False, "empty_pool")
        frac = sol_in / self.sol_reserve
        if frac > max_pool_frac:
            return Fill(sol_in, 0, 0, mid, 0, 0, frac, False, "size_exceeds_pool_cap")
        out = cp_out(self.sol_reserve, self.token_reserve, sol_in, self.fee)
        if self.kind == "bonding" and self.real_token_reserve is not None:
            out = min(out, self.real_token_reserve)
        if out <= 0:
            return Fill(sol_in, 0, 0, mid, 0, 0, frac, False, "zero_out")
        avg = sol_in / out
        return Fill(
            qty_in=sol_in,
            qty_out=out,
            avg_price=avg,
            mid_price_before=mid,
            slippage_frac=(avg - mid) / mid if mid > 0 else 0.0,
            fee_paid=sol_in * self.fee,
            pool_frac=frac,
        )

    def sell(self, tokens_in: float, max_pool_frac: float = 0.35) -> Fill:
        """Sell `tokens_in` tokens, receive SOL. This is where bags die."""
        mid = self.mid
        if tokens_in <= 0 or self.sol_reserve <= 0 or self.token_reserve <= 0:
            return Fill(tokens_in, 0, 0, mid, 0, 0, 0, False, "empty_pool")
        frac = tokens_in / self.token_reserve
        if frac > max_pool_frac:
            return Fill(tokens_in, 0, 0, mid, 0, 0, frac, False, "size_exceeds_pool_cap")
        out = cp_out(self.token_reserve, self.sol_reserve, tokens_in, self.fee)
        if out <= 0:
            return Fill(tokens_in, 0, 0, mid, 0, 0, frac, False, "zero_out")
        avg = out / tokens_in
        return Fill(
            qty_in=tokens_in,
            qty_out=out,
            avg_price=avg,
            mid_price_before=mid,
            slippage_frac=(mid - avg) / mid if mid > 0 else 0.0,
            fee_paid=out * self.fee,
            pool_frac=frac,
        )

    def apply(self, fill: Fill, side: Literal["buy", "sell"]) -> "Pool":
        """Return the post-trade pool. Your own trade moves the price."""
        if not fill.ok:
            return self
        if side == "buy":
            return Pool(
                self.sol_reserve + fill.qty_in,
                self.token_reserve - fill.qty_out,
                self.fee,
                self.kind,
                (self.real_token_reserve - fill.qty_out) if self.real_token_reserve is not None else None,
            )
        return Pool(
            self.sol_reserve - fill.qty_out,
            self.token_reserve + fill.qty_in,
            self.fee,
            self.kind,
            (self.real_token_reserve + fill.qty_in) if self.real_token_reserve is not None else None,
        )


def max_size_for_impact(pool: Pool, max_impact: float, side: Literal["buy", "sell"] = "buy") -> float:
    """Largest trade whose average price is within `max_impact` of mid.

    For constant product with fee f, buying with input dx against reserve X:
        avg/mid = (X + dx(1-f)) / (X(1-f))
    Setting that to (1+m) and solving gives the closed form below. The sell side
    is symmetric in the token reserve.
    """
    f = pool.fee
    if f >= 1.0:
        return 0.0
    reserve = pool.sol_reserve if side == "buy" else pool.token_reserve
    if reserve <= 0:
        return 0.0
    m = max_impact
    # avg/mid = (R + d(1-f)) / (R(1-f))  ->  d = R((1+m)(1-f) - 1)/(1-f)
    numer = (1.0 + m) * (1.0 - f) - 1.0
    if numer <= 0:
        return 0.0
    return reserve * numer / (1.0 - f)


def round_trip_cost(pool: Pool, sol_in: float) -> float:
    """Fraction of capital lost buying and immediately selling back.

    This is the hurdle every trade must clear before it makes a cent, and on a
    thin new pool it is routinely 10-25%, not the 1-2% people assume.
    """
    buy = pool.buy(sol_in)
    if not buy.ok:
        return 1.0
    after = pool.apply(buy, "buy")
    sell = after.sell(buy.qty_out)
    if not sell.ok:
        return 1.0
    return 1.0 - (sell.qty_out / sol_in)
