"""The live trading loop.

Structure mirrors the backtester exactly: discover -> feature -> safety -> score
-> size -> enter -> manage -> exit. Same `features_at`, same `SafetyConfig`,
same `CompositeScorer`, same `ExitConfig`, same AMM math. Anything that behaves
differently between backtest and live is a bug, not a design choice.

Modes:
  paper  - PaperBroker, fills simulated against live pool state. Default.
  dry    - JupiterBroker with dry_run: real routes and real quotes, no sends.
  live   - JupiterBroker signing and sending. Requires DEGEN_WALLET_KEY.

Every decision, including every rejection and the reason for it, is appended to
the `events` dataset. When the bot does something surprising, the record of why
already exists.
"""
from __future__ import annotations

import signal
import threading
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..execution.broker import Broker, JupiterBroker, PaperBroker
from ..features.build import features_at
from ..risk.exit import ExitConfig, Position, evaluate
from ..risk.manager import RiskConfig, RiskManager
from ..safety.filters import SafetyConfig, check_local, check_rugcheck
from ..signals.score import CompositeScorer
from ..sim.amm import Pool
from ..sim.costs import CostModel
from ..sources import jupiter
from ..store.lake import EVENTS, TRADES, lake
from ..util.log import get
from ..util.timeutil import human_age, now

log = get("degen.live")

CONSIDER_MIN_AGE = 45.0
CONSIDER_MAX_AGE = 2400.0


@dataclass
class TraderConfig:
    mode: str = "paper"                 # paper | dry | live
    poll_interval_s: float = 6.0
    manage_interval_s: float = 5.0
    slippage_bps: int = 1200            # thin pools move between quote and land
    sol_usd_refresh_s: float = 120.0
    deep_check: bool = True             # RugCheck before entry
    score_threshold: float = 0.55
    max_watch: int = 800
    risk: RiskConfig = field(default_factory=RiskConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    costs: CostModel = field(default_factory=CostModel)


@dataclass
class LivePosition:
    pos: Position
    tokens: float
    original_tokens: float
    decimals: int
    symbol: str | None
    creator: str | None


class LiveTrader:
    def __init__(self, cfg: TraderConfig | None = None) -> None:
        self.cfg = cfg or TraderConfig()
        self.lake = lake()
        self.scorer = CompositeScorer(rule_threshold=self.cfg.score_threshold)
        self.risk = RiskManager(self.cfg.risk)
        self.stop = threading.Event()
        self.sol_usd = 94.0
        self._sol_usd_at = 0.0
        self.history: dict[str, list[dict]] = {}
        self.positions: dict[str, LivePosition] = {}
        self.broker: Broker = self._make_broker()
        self.n_entries = 0
        self.n_exits = 0

    def _make_broker(self) -> Broker:
        m = self.cfg.mode
        if m == "paper":
            return PaperBroker(start_sol=self.cfg.risk.bankroll_sol, costs=self.cfg.costs)
        if m == "dry":
            return JupiterBroker(dry_run=True, costs=self.cfg.costs)
        if m == "live":
            return JupiterBroker(dry_run=False, costs=self.cfg.costs)
        raise ValueError(f"unknown mode {m}")

    # ---------------- helpers ----------------

    def _event(self, kind: str, **kw: Any) -> None:
        self.lake.append(EVENTS, {"t": now(), "kind": kind, "mode": self.cfg.mode, **kw})

    def _refresh_sol(self) -> None:
        if now() - self._sol_usd_at < self.cfg.sol_usd_refresh_s:
            return
        p = jupiter.price([jupiter.SOL_MINT])
        v = (p.get(jupiter.SOL_MINT) or {}).get("usdPrice")
        if v:
            self.sol_usd = float(v)
        self._sol_usd_at = now()

    def _remember(self, rows: list[dict]) -> None:
        for r in rows:
            m = r.get("mint")
            if not m or r.get("age_s") is None:
                continue
            h = self.history.setdefault(m, [])
            h.append(r)
            if len(h) > 60:
                del h[: len(h) - 60]
        if len(self.history) > self.cfg.max_watch:
            drop = sorted(
                (m for m in self.history if m not in self.positions),
                key=lambda m: self.history[m][-1].get("age_s") or 0,
                reverse=True,
            )[: len(self.history) - self.cfg.max_watch]
            for m in drop:
                self.history.pop(m, None)

    def _ctx(self, row: dict) -> dict:
        return {
            "liquidity": row.get("liquidity"),
            "price_usd": row.get("price_usd"),
            "sol_usd": self.sol_usd,
            "decimals": row.get("decimals") or 6,
        }

    def _pool(self, row: dict) -> Pool | None:
        liq, px = row.get("liquidity"), row.get("price_usd")
        if not liq or not px or liq <= 0 or px <= 0:
            return None
        return Pool.from_liquidity_usd(float(liq), float(px), self.sol_usd, fee=self.cfg.costs.amm_fee())

    # ---------------- entry ----------------

    def consider(self, mint: str) -> None:
        h = self.history.get(mint)
        if not h or mint in self.positions:
            return
        last = h[-1]
        age = float(last.get("age_s") or 0)
        if age < CONSIDER_MIN_AGE or age > CONSIDER_MAX_AGE:
            return

        feat = features_at(pd.DataFrame(h), age)
        if feat is None:
            return

        safe = check_local(feat, self.cfg.safety)
        if not safe.ok:
            return

        s = self.scorer.score(feat)
        if not s.passed:
            return

        ok, why = self.risk.can_enter(mint, creator=feat.get("dev"))
        if not ok:
            self._event("reject", mint=mint, stage="risk", why=why, score=s.score)
            return

        if self.cfg.deep_check:
            deep = check_rugcheck(mint, self.cfg.safety)
            if not deep.ok:
                self._event("reject", mint=mint, stage="rugcheck", why=deep.rejects[:3], score=s.score)
                log.info("skip %-12s rugcheck: %s", str(last.get("symbol"))[:12], deep.rejects[:2])
                return

        pool = self._pool(last)
        size, why_size = self.risk.size_for(s.score, pool_sol_reserve=pool.sol_reserve if pool else None)
        if size <= 0:
            self._event("reject", mint=mint, stage="sizing", why=why_size, score=s.score)
            return

        res = self.broker.buy(mint, size, self.cfg.slippage_bps, self._ctx(last))
        if not res.ok:
            self.risk.on_fill_failure()
            self._event("buy_failed", mint=mint, why=res.reason, size=size, score=s.score)
            log.warning("buy failed %s: %s", str(last.get("symbol"))[:12], res.reason)
            return
        self.risk.on_fill_success()

        entry_px_sol = res.price if res.price > 0 else (float(last["price_usd"]) / self.sol_usd)
        pos = Position(
            mint=mint, entry_price=entry_px_sol, entry_time=now(),
            tokens=res.tokens_delta, sol_in=abs(res.sol_delta),
            entry_liq=float(last.get("liquidity") or 0.0),
        )
        self.positions[mint] = LivePosition(
            pos=pos, tokens=res.tokens_delta, original_tokens=res.tokens_delta,
            decimals=int(last.get("decimals") or 6), symbol=last.get("symbol"), creator=feat.get("dev"),
        )
        self.risk.on_entry(mint, abs(res.sol_delta), creator=feat.get("dev"),
                           meta={"symbol": last.get("symbol"), "score": s.score})
        self.n_entries += 1
        log.info("BUY  %-12s %.4f SOL @ %.3e | score=%.3f liq=$%s holders=%s",
                 str(last.get("symbol"))[:12], abs(res.sol_delta), entry_px_sol, s.score,
                 f"{last.get('liquidity') or 0:,.0f}", last.get("holder_count"))
        self._event("buy", mint=mint, symbol=last.get("symbol"), size_sol=abs(res.sol_delta),
                    price=entry_px_sol, score=s.score, terms=s.terms, sig=res.signature,
                    liquidity=last.get("liquidity"), holders=last.get("holder_count"))

    # ---------------- management ----------------

    def manage(self) -> None:
        if not self.positions:
            return
        mints = list(self.positions.keys())
        rows = {r["mint"]: r for r in jupiter.search(mints) if r.get("mint")}
        for mint in mints:
            lp = self.positions.get(mint)
            row = rows.get(mint)
            if lp is None or row is None:
                continue
            px_usd = row.get("price_usd")
            if not px_usd or px_usd <= 0:
                continue
            px_sol = float(px_usd) / self.sol_usd
            liq = float(row.get("liquidity") or 0.0)
            dec = evaluate(lp.pos, px_sol, liq, now(), self.cfg.exit)
            if dec is None:
                continue
            want = min(dec.sell_frac_of_original * lp.original_tokens, lp.tokens)
            if want <= 0:
                continue
            res = self.broker.sell(mint, want, self.cfg.slippage_bps, self._ctx(row))
            if not res.ok:
                self.risk.on_fill_failure()
                self._event("sell_failed", mint=mint, why=res.reason, want=want)
                continue
            self.risk.on_fill_success()
            lp.tokens -= want
            lp.pos.sol_out += res.sol_delta
            lp.pos.remaining_frac = lp.tokens / lp.original_tokens if lp.original_tokens else 0.0
            if dec.reason in ("ladder", "cost_recovery"):
                lp.pos.rungs_hit += 1
            self.n_exits += 1
            x = px_sol / lp.pos.entry_price if lp.pos.entry_price else 0.0
            log.info("SELL %-12s %5.1f%% @ %.2fx [%s] remaining=%.0f%%",
                     str(lp.symbol)[:12], 100 * dec.sell_frac_of_original, x, dec.reason,
                     100 * lp.pos.remaining_frac)
            self._event("sell", mint=mint, symbol=lp.symbol, frac=dec.sell_frac_of_original,
                        x=x, reason=dec.reason, sol_out=res.sol_delta, sig=res.signature)

            if lp.pos.remaining_frac <= 1e-6 or dec.close:
                pnl = lp.pos.sol_out - lp.pos.sol_in
                self.risk.on_exit(mint, pnl)
                self.lake.append(TRADES, {
                    "mint": mint, "symbol": lp.symbol, "mode": self.cfg.mode,
                    "entry_time": lp.pos.entry_time, "exit_time": now(),
                    "hold_s": now() - lp.pos.entry_time,
                    "sol_in": lp.pos.sol_in, "sol_out": lp.pos.sol_out, "pnl_sol": pnl,
                    "realized_x": lp.pos.sol_out / lp.pos.sol_in if lp.pos.sol_in else 0.0,
                    "peak_x": lp.pos.peak_price / lp.pos.entry_price if lp.pos.entry_price else 0.0,
                    "reason": dec.reason, "creator": lp.creator,
                })
                log.info("CLOSED %-12s pnl=%+.4f SOL (%.2fx) | %s", str(lp.symbol)[:12], pnl,
                         lp.pos.sol_out / lp.pos.sol_in if lp.pos.sol_in else 0, dec.reason)
                self.positions.pop(mint, None)

    # ---------------- loops ----------------

    def discovery_loop(self) -> None:
        while not self.stop.is_set():
            t0 = now()
            try:
                self._refresh_sol()
                self._remember(jupiter.recent())
                watch = [
                    m for m, h in self.history.items()
                    if m not in self.positions and h
                    and CONSIDER_MIN_AGE <= (h[-1].get("age_s") or 0) <= CONSIDER_MAX_AGE
                ][: jupiter.MAX_BATCH]
                if watch:
                    self._remember(jupiter.search(watch))
                for m in list(self.history.keys()):
                    self.consider(m)
            except Exception as exc:
                log.warning("discovery loop error: %s", exc)
            self.stop.wait(max(0.0, self.cfg.poll_interval_s - (now() - t0)))

    def manage_loop(self) -> None:
        while not self.stop.is_set():
            t0 = now()
            try:
                self.manage()
            except Exception as exc:
                log.warning("manage loop error: %s", exc)
            self.stop.wait(max(0.0, self.cfg.manage_interval_s - (now() - t0)))

    def report_loop(self) -> None:
        started = now()
        while not self.stop.is_set():
            self.stop.wait(60)
            if self.stop.is_set():
                break
            s = self.risk.summary()
            log.info("up=%s watch=%d open=%d entries=%d exits=%d | bankroll=%.4f pnl=%+.4f %s",
                     human_age(now() - started), len(self.history), len(self.positions),
                     self.n_entries, self.n_exits, s["bankroll_sol"], s["realized_pnl_sol"],
                     f"HALTED({s['halt_reason']})" if s["halted"] else "")

    def run(self) -> None:
        log.info("live trader starting in %s mode | bankroll=%.3f SOL threshold=%.2f",
                 self.cfg.mode, self.cfg.risk.bankroll_sol, self.cfg.score_threshold)
        if self.cfg.mode == "live":
            log.warning("LIVE MODE: real funds will be spent")
        self._event("start", cfg_mode=self.cfg.mode, bankroll=self.cfg.risk.bankroll_sol)
        threads = [
            threading.Thread(target=self.discovery_loop, name="discover", daemon=True),
            threading.Thread(target=self.manage_loop, name="manage", daemon=True),
            threading.Thread(target=self.report_loop, name="report", daemon=True),
        ]
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                self.stop.wait(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            log.info("stopped. %s", self.risk.summary())
            self._event("stop", **self.risk.summary())


def main(mode: str = "paper") -> None:
    from ..util.log import setup

    setup()
    t = LiveTrader(TraderConfig(mode=mode))
    signal.signal(signal.SIGTERM, lambda *_: t.stop.set())
    signal.signal(signal.SIGINT, lambda *_: t.stop.set())
    t.run()


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else "paper")
