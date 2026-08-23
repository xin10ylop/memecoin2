"""Broker interface: one API, three backends.

`PaperBroker` prices fills against the live pool using the same AMM math the
backtester uses, so paper results are directly comparable to backtest results
and to live results. `JupiterBroker` signs and sends real transactions.

The split exists so that exactly one code path decides *what* to trade. A bot
whose paper mode and live mode take different routes through the code is a bot
whose paper results mean nothing.
"""
from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from typing import Any

from ..sim.amm import Pool
from ..sim.costs import CostModel
from ..util.log import get
from ..util.timeutil import now

log = get("degen.exec")

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000


@dataclass
class OrderResult:
    ok: bool
    mint: str
    side: str                    # "buy" | "sell"
    sol_delta: float             # signed: negative spends SOL, positive receives
    tokens_delta: float          # signed
    price: float                 # SOL per token, average realized
    fee_sol: float = 0.0
    slippage: float = 0.0
    signature: str | None = None
    reason: str = ""
    at: float = field(default_factory=now)
    raw: dict[str, Any] = field(default_factory=dict)


class Broker(abc.ABC):
    @abc.abstractmethod
    def buy(self, mint: str, sol_amount: float, slippage_bps: int, ctx: dict) -> OrderResult: ...

    @abc.abstractmethod
    def sell(self, mint: str, token_amount: float, slippage_bps: int, ctx: dict) -> OrderResult: ...

    @abc.abstractmethod
    def sol_balance(self) -> float: ...


class PaperBroker(Broker):
    """Simulated fills against the real, current pool state.

    `ctx` must carry `liquidity` (USD), `price_usd` and `sol_usd` so the pool
    can be reconstructed. Without them the order is refused rather than filled
    at a made-up price - a paper broker that always fills teaches the operator
    the wrong lesson.
    """

    def __init__(self, start_sol: float = 10.0, costs: CostModel | None = None) -> None:
        self._sol = start_sol
        self.costs = costs or CostModel()
        self.positions: dict[str, float] = {}
        self.fills: list[OrderResult] = []

    def sol_balance(self) -> float:
        return self._sol

    def _pool(self, ctx: dict) -> Pool | None:
        liq, px, sol_usd = ctx.get("liquidity"), ctx.get("price_usd"), ctx.get("sol_usd")
        if not liq or not px or not sol_usd or liq <= 0 or px <= 0:
            return None
        return Pool.from_liquidity_usd(float(liq), float(px), float(sol_usd), fee=self.costs.amm_fee())

    def buy(self, mint: str, sol_amount: float, slippage_bps: int, ctx: dict) -> OrderResult:
        pool = self._pool(ctx)
        if pool is None:
            return OrderResult(False, mint, "buy", 0, 0, 0, reason="no pool state in ctx")
        if sol_amount > self._sol:
            return OrderResult(False, mint, "buy", 0, 0, 0, reason="insufficient SOL")
        fill = pool.buy(sol_amount)
        if not fill.ok:
            return OrderResult(False, mint, "buy", 0, 0, 0, reason=fill.reason)
        if fill.slippage_frac * 1e4 > slippage_bps:
            return OrderResult(False, mint, "buy", 0, 0, 0, reason=f"slippage {fill.slippage_frac*1e4:.0f}bps > {slippage_bps}")
        fee = self.costs.entry_overhead(sol_amount)
        self._sol -= (sol_amount + fee)
        self.positions[mint] = self.positions.get(mint, 0.0) + fill.qty_out
        r = OrderResult(True, mint, "buy", -(sol_amount + fee), fill.qty_out, fill.avg_price,
                        fee, fill.slippage_frac, reason="paper")
        self.fills.append(r)
        return r

    def sell(self, mint: str, token_amount: float, slippage_bps: int, ctx: dict) -> OrderResult:
        pool = self._pool(ctx)
        if pool is None:
            return OrderResult(False, mint, "sell", 0, 0, 0, reason="no pool state in ctx")
        held = self.positions.get(mint, 0.0)
        token_amount = min(token_amount, held)
        if token_amount <= 0:
            return OrderResult(False, mint, "sell", 0, 0, 0, reason="no position")
        fill = pool.sell(token_amount)
        if not fill.ok:
            return OrderResult(False, mint, "sell", 0, 0, 0, reason=fill.reason)
        fee = self.costs.exit_overhead(fill.qty_out)
        net = max(0.0, fill.qty_out - fee)
        self._sol += net
        self.positions[mint] = held - token_amount
        if self.positions[mint] <= 1e-9:
            self.positions.pop(mint, None)
        r = OrderResult(True, mint, "sell", net, -token_amount, fill.avg_price, fee, fill.slippage_frac, reason="paper")
        self.fills.append(r)
        return r


class JupiterBroker(Broker):
    """Live execution through the Jupiter aggregator.

    Jupiter is the default rather than a direct pump.fun/Raydium instruction
    builder because it routes across every venue, handles migrated tokens
    without a code change, and is a single well-documented surface. A direct
    builder is faster by roughly the width of one hop and is the right upgrade
    once the strategy is proven; it is not the right thing to debug first.

    The wallet key is read from DEGEN_WALLET_KEY (base58 secret key) and is
    never logged, echoed, or written to the lake.
    """

    def __init__(self, rpc_url: str | None = None, costs: CostModel | None = None,
                 dry_run: bool = True, priority_cu_price: int = 500_000) -> None:
        self.costs = costs or CostModel()
        self.dry_run = dry_run
        self.priority_cu_price = priority_cu_price
        self.rpc_url = rpc_url or os.getenv("DEGEN_RPC_URL", "https://api.mainnet-beta.solana.com")
        self._kp = None
        self.pubkey: str | None = None
        key = os.getenv("DEGEN_WALLET_KEY", "").strip()
        if key:
            try:
                import base58
                from solders.keypair import Keypair

                self._kp = Keypair.from_bytes(base58.b58decode(key))
                self.pubkey = str(self._kp.pubkey())
                log.info("live wallet loaded: %s", self.pubkey)
            except Exception as exc:
                log.error("could not load DEGEN_WALLET_KEY: %s", type(exc).__name__)
                self._kp = None
        if self._kp is None and not dry_run:
            raise RuntimeError("live mode requires a valid DEGEN_WALLET_KEY")

    # -------- balances --------

    def sol_balance(self) -> float:
        if not self.pubkey:
            return 0.0
        from ..sources.solana_rpc import _rpc

        r = _rpc("getBalance", [self.pubkey])
        return ((r or {}).get("value") or 0) / LAMPORTS

    def token_balance(self, mint: str) -> float:
        if not self.pubkey:
            return 0.0
        from ..sources.solana_rpc import _rpc

        r = _rpc("getTokenAccountsByOwner", [self.pubkey, {"mint": mint}, {"encoding": "jsonParsed"}])
        total = 0.0
        for acc in (r or {}).get("value", []) or []:
            info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            total += float(info.get("uiAmount") or 0.0)
        return total

    # -------- swaps --------

    def _swap(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> tuple[bool, dict]:
        from ..sources import jupiter
        from ..util.http import post_json

        quote = jupiter.quote(input_mint, output_mint, amount_raw, slippage_bps)
        if not quote or "outAmount" not in quote:
            return False, {"reason": "no route"}
        if self.dry_run or self._kp is None:
            return True, {"quote": quote, "dry_run": True}

        body = {
            "quoteResponse": quote,
            "userPublicKey": self.pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {
                "priorityLevelWithMaxLamports": {
                    "maxLamports": int(self.priority_cu_price * 150_000 / 1_000_000),
                    "priorityLevel": "veryHigh",
                }
            },
        }
        resp = post_json(f"{jupiter.BASE}/swap/v1/swap", body, tries=2)
        if not resp or "swapTransaction" not in resp:
            return False, {"reason": "swap build failed", "resp": resp}

        import base64

        from solders.transaction import VersionedTransaction

        raw = base64.b64decode(resp["swapTransaction"])
        unsigned = VersionedTransaction.from_bytes(raw)
        signed = VersionedTransaction(unsigned.message, [self._kp])
        sig = self._send(bytes(signed))
        if sig is None:
            return False, {"reason": "send failed", "quote": quote}
        return True, {"quote": quote, "signature": sig}

    def _send(self, tx_bytes: bytes) -> str | None:
        import base64

        from ..util.http import post_json

        body = {
            "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [base64.b64encode(tx_bytes).decode(),
                       {"encoding": "base64", "skipPreflight": True, "maxRetries": 3}],
        }
        d = post_json(self.rpc_url, body, tries=3)
        if not d or "error" in (d or {}):
            log.error("sendTransaction failed: %s", (d or {}).get("error"))
            return None
        return d.get("result")

    def buy(self, mint: str, sol_amount: float, slippage_bps: int, ctx: dict) -> OrderResult:
        lamports = int(sol_amount * LAMPORTS)
        ok, info = self._swap(SOL_MINT, mint, lamports, slippage_bps)
        if not ok:
            return OrderResult(False, mint, "buy", 0, 0, 0, reason=str(info.get("reason")), raw=info)
        q = info["quote"]
        dec = int(ctx.get("decimals") or 6)
        out = int(q["outAmount"]) / (10 ** dec)
        return OrderResult(
            True, mint, "buy", -sol_amount, out,
            price=sol_amount / out if out else 0.0,
            slippage=float(q.get("priceImpactPct") or 0.0),
            signature=info.get("signature"),
            reason="dry_run" if info.get("dry_run") else "live",
            raw={"quote_out": q.get("outAmount"), "impact": q.get("priceImpactPct")},
        )

    def sell(self, mint: str, token_amount: float, slippage_bps: int, ctx: dict) -> OrderResult:
        dec = int(ctx.get("decimals") or 6)
        raw_amt = int(token_amount * (10 ** dec))
        ok, info = self._swap(mint, SOL_MINT, raw_amt, slippage_bps)
        if not ok:
            return OrderResult(False, mint, "sell", 0, 0, 0, reason=str(info.get("reason")), raw=info)
        q = info["quote"]
        sol_out = int(q["outAmount"]) / LAMPORTS
        return OrderResult(
            True, mint, "sell", sol_out, -token_amount,
            price=sol_out / token_amount if token_amount else 0.0,
            slippage=float(q.get("priceImpactPct") or 0.0),
            signature=info.get("signature"),
            reason="dry_run" if info.get("dry_run") else "live",
            raw={"quote_out": q.get("outAmount"), "impact": q.get("priceImpactPct")},
        )
