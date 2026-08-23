"""Minimal Solana JSON-RPC client.

Only the handful of methods the bot genuinely needs. Set DEGEN_RPC_URL to a
paid endpoint (Helius, Triton, QuickNode) for production - the public endpoint
is heavily rate-limited and is fine for research but not for execution.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

from ..util.http import post_json
from ..util.log import get

log = get("degen.src.rpc")

RPC_URL = os.getenv("DEGEN_RPC_URL", "https://api.mainnet-beta.solana.com")

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Programs whose token accounts are protocol infrastructure, not a holder.
KNOWN_AMM_PROGRAMS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM v4",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora Pools",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "Meteora DAMM v2",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6",
}


def _rpc(method: str, params: list[Any] | None = None) -> Any | None:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    d = post_json(RPC_URL, body, tries=3)
    if d is None:
        return None
    if "error" in d:
        log.debug("rpc %s error: %s", method, d["error"])
        return None
    return d.get("result")


def get_slot() -> int | None:
    return _rpc("getSlot")


def get_health() -> bool:
    return _rpc("getHealth") == "ok"


def get_multiple_accounts(pubkeys: Iterable[str]) -> list[dict | None]:
    keys = list(pubkeys)[:100]
    if not keys:
        return []
    r = _rpc("getMultipleAccounts", [keys, {"encoding": "base64"}])
    return (r or {}).get("value") or []


def classify_owners(owners: Iterable[str]) -> dict[str, str]:
    """Label each address as 'wallet', an AMM/protocol name, or 'program'.

    This is what separates a real concentrated holder from a pool reserve: a
    plain wallet is owned by the System Program, while a pool's authority is
    owned by (or is) an AMM program.
    """
    keys = list(dict.fromkeys(owners))
    out: dict[str, str] = {}
    for i in range(0, len(keys), 100):
        chunk = keys[i : i + 100]
        accs = get_multiple_accounts(chunk)
        for k, acc in zip(chunk, accs):
            if acc is None:
                out[k] = "unknown"
                continue
            prog = acc.get("owner")
            if prog == SYSTEM_PROGRAM:
                out[k] = "wallet"
            elif prog in KNOWN_AMM_PROGRAMS:
                out[k] = KNOWN_AMM_PROGRAMS[prog]
            elif acc.get("executable"):
                out[k] = "program"
            else:
                out[k] = f"pda:{prog[:8]}" if prog else "unknown"
    return out


def get_recent_prioritization_fees(accounts: Iterable[str] | None = None) -> list[dict]:
    """Local fee market for the given hot accounts.

    Calling this with no accounts returns the *global* floor, which on Solana is
    near zero and is not what a pump.fun snipe competes against - the local
    market around the bonding curve runs two orders of magnitude higher.
    """
    params = [list(accounts)[:128]] if accounts else []
    return _rpc("getRecentPrioritizationFees", params) or []


def suggest_cu_price(accounts: Iterable[str] | None = None, percentile: float = 75, floor: int = 500_000) -> int:
    """Compute-unit price in microlamports from the local market, floored.

    The floor matters: measured landed pump.fun transactions sit at a median of
    ~264k microlamports/CU, so an estimator that returns 40k will simply not
    land in a contested block.
    """
    fees = get_recent_prioritization_fees(accounts)
    vals = sorted(f.get("prioritizationFee", 0) for f in fees if f.get("prioritizationFee"))
    if not vals:
        return floor
    idx = min(len(vals) - 1, int(len(vals) * percentile / 100))
    return max(floor, int(vals[idx]))
