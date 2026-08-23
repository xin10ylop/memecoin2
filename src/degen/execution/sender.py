"""Transaction submission paths.

Research conclusion driving this module: none of the retail sniper platforms
(Photon, Axiom, Trojan, Bloom, Nova, Vector, Padre, Pepeboost, BonkBot, Banana
Gun, Maestro) expose a supported programmatic trading API - they are UI or
Telegram surfaces, Photon states explicitly that third-party automation should
be treated as unsafe, and BullX suspended trading entirely on 1 June 2026.
GMGN publishes a real REST trading API but rate-limits it to one call every
five seconds, which rules it out of any hot path. So a bot that must fire
inside a second has to self-host submission, and self-hosting also saves the
~1% platform fee, which is the largest controllable cost in the whole stack.

Three submission paths, in increasing order of speed and cost:

* `RpcSender`        - plain `sendTransaction`. Fine for exits and for paper.
* `HeliusSender`     - Helius Sender: fans a transaction out across Helius,
                       Jito, Harmonic and Rakurai simultaneously. Requires both
                       a tip transfer and a compute-unit-price instruction in
                       every transaction. `Max` mode needs a 0.001 SOL minimum
                       tip; `swqos_only=true` drops that to 0.000005 SOL.
* `JitoBundleSender` - atomic bundles, up to 5 transactions, 1,000 lamport
                       minimum tip, rate-limited to one request per second per
                       IP per region (submit to several regions concurrently to
                       get around it).

On tips: live tip-floor data shows the median landed tip is around 5e-6 SOL and
even the 99th percentile is below the 0.001 SOL that Sender Max forces. So
route choice is a real cost decision - Sender Max costs roughly 200x the median
tip and buys reliability, which is worth it on a contested mint and wasteful on
a routine exit.
"""
from __future__ import annotations

import base64
import os
import random
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..util.http import get_json, post_json, request
from ..util.log import get

log = get("degen.sender")

# Jito tip accounts. Pick one at random per transaction: they are sharded to
# avoid write-lock contention, so always using the same one throttles you.
JITO_TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]

# Helius Sender regional endpoints. Co-locate near validator concentration and
# pin the nearest one; the global /fast endpoint adds a routing hop.
SENDER_REGIONS = {
    "global": "https://sender.helius-rpc.com/fast",
    "slc": "http://slc-sender.helius-rpc.com/fast",
    "ewr": "http://ewr-sender.helius-rpc.com/fast",
    "lon": "http://lon-sender.helius-rpc.com/fast",
    "fra": "http://fra-sender.helius-rpc.com/fast",
    "ams": "http://ams-sender.helius-rpc.com/fast",
    "sg": "http://sg-sender.helius-rpc.com/fast",
    "tyo": "http://tyo-sender.helius-rpc.com/fast",
}
SENDER_PING = "https://sender.helius-rpc.com/ping"

JITO_REGIONS = [
    "https://mainnet.block-engine.jito.wtf",
    "https://amsterdam.mainnet.block-engine.jito.wtf",
    "https://frankfurt.mainnet.block-engine.jito.wtf",
    "https://ny.mainnet.block-engine.jito.wtf",
    "https://tokyo.mainnet.block-engine.jito.wtf",
]

SENDER_MAX_MIN_TIP = 1_000_000       # 0.001 SOL
SENDER_SWQOS_MIN_TIP = 5_000         # 0.000005 SOL
JITO_MIN_TIP = 1_000


def tip_account() -> str:
    return random.choice(JITO_TIP_ACCOUNTS)


@dataclass
class SendResult:
    ok: bool
    signature: str | None = None
    path: str = ""
    reason: str = ""


class Sender(ABC):
    name = "base"

    @abstractmethod
    def send(self, tx_b64: str) -> SendResult: ...

    def warm(self) -> None:
        """Optional: pre-establish the connection so the first send is not
        paying TCP and TLS setup latency."""


class RpcSender(Sender):
    name = "rpc"

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.getenv("DEGEN_RPC_URL", "https://api.mainnet-beta.solana.com")

    def send(self, tx_b64: str) -> SendResult:
        d = post_json(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [tx_b64, {"encoding": "base64", "skipPreflight": True, "maxRetries": 2}],
        }, tries=2)
        if not d or "error" in (d or {}):
            return SendResult(False, path=self.name, reason=str((d or {}).get("error", "no response")))
        return SendResult(True, d.get("result"), self.name)


class HeliusSender(Sender):
    """Multi-path submission. Fastest single primitive available.

    Every transaction sent through this MUST already contain a tip transfer to
    a Jito tip account and a SetComputeUnitPrice instruction, or it is rejected.
    That is the caller's job when building the transaction; this class only
    submits.
    """

    name = "helius_sender"

    def __init__(self, region: str = "global", swqos_only: bool = False) -> None:
        self.region = region if region in SENDER_REGIONS else "global"
        self.url = SENDER_REGIONS[self.region]
        self.swqos_only = swqos_only
        if swqos_only:
            self.url += "?swqos_only=true"
        self.min_tip = SENDER_SWQOS_MIN_TIP if swqos_only else SENDER_MAX_MIN_TIP
        self._warm_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def warm(self) -> None:
        """Keep the connection hot. Free latency that most implementations skip."""
        if self._warm_thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    request("GET", SENDER_PING, tries=1, timeout=5, rate_limit=False)
                except Exception:
                    pass
                self._stop.wait(5.0)

        self._warm_thread = threading.Thread(target=loop, name="sender-warm", daemon=True)
        self._warm_thread.start()

    def close(self) -> None:
        self._stop.set()

    def send(self, tx_b64: str) -> SendResult:
        d = post_json(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [tx_b64, {"encoding": "base64", "skipPreflight": True, "maxRetries": 0}],
        }, tries=2, rate_limit=False)
        if not d or "error" in (d or {}):
            return SendResult(False, path=f"{self.name}:{self.region}",
                              reason=str((d or {}).get("error", "no response")))
        return SendResult(True, d.get("result"), f"{self.name}:{self.region}")


class JitoBundleSender(Sender):
    """Atomic bundles. Use when a buy must land together with a guard or an
    approval, or when you want protection from being sandwiched inside your own
    transaction group."""

    name = "jito"
    MAX_BUNDLE = 5

    def __init__(self, regions: list[str] | None = None) -> None:
        self.regions = regions or JITO_REGIONS[:3]

    def send(self, tx_b64: str) -> SendResult:
        return self.send_bundle([tx_b64])

    def send_bundle(self, txs_b64: list[str]) -> SendResult:
        if not txs_b64:
            return SendResult(False, path=self.name, reason="empty bundle")
        if len(txs_b64) > self.MAX_BUNDLE:
            return SendResult(False, path=self.name, reason=f"bundle >{self.MAX_BUNDLE} transactions")
        body = {"jsonrpc": "2.0", "id": 1, "method": "sendBundle",
                "params": [txs_b64, {"encoding": "base64"}]}
        # The rate limit is per IP per region, so trying regions in turn both
        # routes around a 429 and reaches whichever block engine is closest.
        last = "no regions"
        for base in self.regions:
            d = post_json(f"{base}/api/v1/bundles", body, tries=1, rate_limit=False)
            if d and "error" not in d:
                return SendResult(True, d.get("result"), f"{self.name}:{base.split('//')[1].split('.')[0]}")
            last = str((d or {}).get("error", "no response"))
        return SendResult(False, path=self.name, reason=last)


def tip_floor() -> dict[str, float]:
    """Live Jito tip percentiles, in SOL.

    Worth calling rather than hardcoding: the landed-tip market moves, and the
    gap between the median (~5e-6 SOL) and what Sender Max forces (1e-3 SOL) is
    large enough that the choice matters to the cost model.
    """
    d = get_json("https://bundles.jito.wtf/api/v1/bundles/tip_floor", tries=2)
    if not d:
        return {}
    row = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else {})
    return {
        k.replace("landed_tips_", "").replace("_percentile", ""): v
        for k, v in row.items()
        if k.startswith("landed_tips_")
    }


def build_sender(kind: str = "auto", region: str = "global") -> Sender:
    kind = (kind or "auto").lower()
    if kind in ("helius", "sender"):
        return HeliusSender(region)
    if kind == "jito":
        return JitoBundleSender()
    if kind == "rpc":
        return RpcSender()
    # auto: use Helius Sender when a Helius endpoint is configured, else plain RPC.
    if "helius" in os.getenv("DEGEN_RPC_URL", "").lower():
        return HeliusSender(region)
    return RpcSender()
