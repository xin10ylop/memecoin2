"""Hard-reject safety filters.

Ordered cheapest-first on purpose. The bot evaluates thousands of tokens an
hour; a check that costs a network round-trip must never run on a token that a
free field already disqualifies. Stage 0 is pure arithmetic on data we already
hold, stage 1 is one RugCheck call, stage 2 is direct RPC.

A note on what these filters are and are not. They cannot tell you a token will
go up. They exist to remove the specific ways a position becomes unexitable:
the mint can be inflated, the account can be frozen, the transfer can be
taxed or blocked, the liquidity can be pulled, or the float can be so
concentrated that one wallet's exit is your entire loss. Everything else is the
model's job.

Every threshold is a named constant with a stated reason, because a filter
whose numbers nobody can defend is a filter that will silently be wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..sources.rugcheck import report as rugcheck_report
from ..util.log import get


def _num(v: Any) -> float | None:
    """Coerce a possibly-NaN, possibly-None, possibly-string field to a float."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN != NaN


def _text(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _flag(v: Any) -> bool | None:
    """Tri-state: True/False/unknown. Pandas turns missing booleans into NaN."""
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return bool(f)

log = get("degen.safety")

# Token-2022 extensions that let an issuer tax, freeze, seize, or block a
# transfer. Any of them on a memecoin is disqualifying: they are never
# necessary for a legitimate meme launch and are the standard honeypot vector.
LETHAL_EXTENSIONS = {
    "transferFeeConfig",
    "transferHook",
    "permanentDelegate",
    "nonTransferable",
    "defaultAccountState",
    "confidentialTransferMint",
    "pausable",
}


@dataclass
class SafetyConfig:
    # --- liquidity: below this you cannot get out at any price ---
    min_liquidity_usd: float = 3_000.0
    max_liquidity_usd: float = 5_000_000.0   # too big to move meaningfully

    # Initial market cap turned out to be the strongest single filter in our own
    # census and - importantly - it is nearly uncorrelated with liquidity
    # (r = 0.06), so it is independent evidence rather than a restatement.
    # Sweep on 1,021 launches: mcap >= 3k gave a 14.7% 2x rate, >= 5k gave
    # 24.3%, >= 7.5k gave 35.1%, against an 8.7% base. The threshold is set at
    # 5k rather than the sweep optimum because the optimum is fitted and a
    # market-cap level is regime-dependent; the scorer grades it continuously.
    min_mcap_usd: float = 5_000.0

    # --- coordinated supply ---
    # Naive top-10 concentration is the wrong quantity, and it is wrong in the
    # direction the adversary optimises for: attributing bundled and co-funded
    # wallets to single entities raises median top-10 holding by 24 percentage
    # points on high-risk tokens against 6pp on low-risk ones. Bundled accounts
    # average 28% of holders and 36% of supply. RugCheck publishes the detected
    # clusters, so entity-level share is computable rather than guessed.
    max_clustered_supply_pct: float = 20.0
    max_insider_clusters: int = 6

    # --- float concentration ---
    # Top holders excluding LP/burn. Above ~40% one wallet can end the token.
    max_top_holders_pct: float = 55.0
    max_single_holder_pct: float = 25.0
    max_dev_balance_pct: float = 12.0

    # --- creator history ---
    # A creator on their thousandth mint is running a factory. Empirically the
    # winners in our own census came from creators with a handful of mints.
    max_dev_mints: int = 250

    # --- authorities ---
    require_mint_auth_revoked: bool = True
    require_freeze_auth_revoked: bool = True
    allow_token_2022: bool = True   # allowed, but only after extensions are verified clean

    # --- activity: a token nobody is trading cannot be exited ---
    min_holders: int = 12
    min_trades_5m: int = 6
    min_unique_traders_5m: int = 5
    max_sell_pressure: float = 0.75          # sells/(buys+sells) ceiling

    # --- rugcheck ---
    use_rugcheck: bool = True
    reject_rugcheck_danger: bool = True
    max_rugcheck_score: float = 40_000.0     # raw score; danger risks dominate it
    reject_if_rugged: bool = True
    min_lp_locked_or_burned_pct: float = 0.0  # pump.fun pre-migration has none

    # --- economics: the trade must be able to clear its own cost ---
    max_round_trip_cost: float = 0.12


@dataclass
class SafetyResult:
    ok: bool
    rejects: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    stage: str = "local"

    def reject(self, why: str) -> "SafetyResult":
        self.ok = False
        self.rejects.append(why)
        return self


def check_local(feat: dict[str, Any], cfg: SafetyConfig | None = None) -> SafetyResult:
    """Stage 0 - free. Runs on the feature row we already built."""
    cfg = cfg or SafetyConfig()
    r = SafetyResult(ok=True, stage="local")

    liq = _num(feat.get("liquidity"))
    if liq is None or liq < cfg.min_liquidity_usd:
        r.reject(f"liquidity {liq} < {cfg.min_liquidity_usd}")
    elif liq > cfg.max_liquidity_usd:
        r.reject(f"liquidity {liq} > {cfg.max_liquidity_usd}")

    mcap = _num(feat.get("mcap"))
    if mcap is not None and mcap < cfg.min_mcap_usd:
        r.reject(f"mcap ${mcap:,.0f} < ${cfg.min_mcap_usd:,.0f}")

    top = _num(feat.get("top_holders_pct"))
    if top is not None and top > cfg.max_top_holders_pct:
        r.reject(f"top_holders {top:.1f}% > {cfg.max_top_holders_pct}%")

    devbal = _num(feat.get("dev_balance_pct"))
    if devbal is not None and devbal > cfg.max_dev_balance_pct:
        r.reject(f"dev holds {devbal:.1f}% > {cfg.max_dev_balance_pct}%")

    dm = _num(feat.get("dev_mints"))
    if dm is not None and dm > cfg.max_dev_mints:
        r.reject(f"dev_mints {dm:.0f} > {cfg.max_dev_mints} (token factory)")

    if cfg.require_mint_auth_revoked and _flag(feat.get("mint_auth_disabled")) is False:
        r.reject("mint authority live")
    if cfg.require_freeze_auth_revoked and _flag(feat.get("freeze_auth_disabled")) is False:
        r.reject("freeze authority live")

    # Token-2022 is now the majority program for new pump.fun launches, so the
    # program id alone says nothing. What matters is which extensions are set,
    # and that needs the deep check - so flag it and require one.
    prog = _text(feat.get("token_program"))
    tags = feat.get("tags")
    is_t22 = prog.startswith("Tokenz") or (isinstance(tags, (list, tuple)) and "token-2022" in tags)
    if is_t22:
        if not cfg.allow_token_2022:
            r.reject("token-2022 and allow_token_2022=False")
        else:
            r.warnings.append("token-2022: extensions must be verified")
            r.detail["requires_extension_check"] = True

    holders = _num(feat.get("holder_count"))
    if holders is not None and holders < cfg.min_holders:
        r.reject(f"holders {holders:.0f} < {cfg.min_holders}")

    trades = _num(feat.get("trades_total")) or 0.0
    if trades < cfg.min_trades_5m:
        r.reject(f"trades_5m {trades:.0f} < {cfg.min_trades_5m}")

    traders = _num(feat.get("s5m_numTraders"))
    if traders is not None and traders < cfg.min_unique_traders_5m:
        r.reject(f"unique_traders_5m {traders:.0f} < {cfg.min_unique_traders_5m}")

    bsr = _num(feat.get("buy_sell_ratio"))
    if bsr is not None and (1.0 - bsr) > cfg.max_sell_pressure:
        r.reject(f"sell pressure {(1-bsr):.2f} > {cfg.max_sell_pressure}")

    # Soft signals - recorded but not disqualifying on their own.
    if feat.get("dev_is_factory"):
        r.warnings.append("creator has >=100 prior mints")
    if not feat.get("has_socials"):
        r.warnings.append("no socials")
    if (_num(feat.get("liq_per_holder")) or 0) > 5000:
        r.warnings.append("liquidity concentrated in few holders")
    # update, never assign: earlier checks (e.g. the token-2022 flag) already
    # wrote into detail and must not be discarded here.
    r.detail.update({"liquidity": liq, "holders": holders, "trades": trades, "dev_mints": dm})
    return r


def check_rugcheck(mint: str, cfg: SafetyConfig | None = None) -> SafetyResult:
    """Stage 1 - one free API call. Only for tokens that passed stage 0."""
    cfg = cfg or SafetyConfig()
    r = SafetyResult(ok=True, stage="rugcheck")
    if not cfg.use_rugcheck:
        return r
    rep = rugcheck_report(mint)
    if rep is None:
        r.warnings.append("rugcheck unavailable")
        return r

    if cfg.reject_if_rugged and rep.get("rugged"):
        r.reject("rugcheck: flagged rugged")

    risks = rep.get("risks") or []
    danger = [x.get("name") for x in risks if x.get("level") == "danger"]
    if cfg.reject_rugcheck_danger and danger:
        # LP-unlocked is expected and unavoidable pre-migration on pump.fun, so
        # it is not by itself a reason to skip every bonding-curve token.
        real = [d for d in danger if d and "LP Unlocked" not in d]
        if real:
            r.reject(f"rugcheck danger: {', '.join(str(d) for d in real[:3])}")
        else:
            r.warnings.append("LP unlocked (expected pre-migration)")

    score = rep.get("score")
    if score is not None and score > cfg.max_rugcheck_score:
        r.reject(f"rugcheck score {score} > {cfg.max_rugcheck_score}")

    tf = rep.get("transferFee") or {}
    if (tf.get("pct") or 0) > 0:
        r.reject(f"transfer fee {tf.get('pct')}% (honeypot)")

    exts = rep.get("token_extensions")
    names: set[str] = set()
    if isinstance(exts, dict):
        names = {k for k, v in exts.items() if v}
    elif isinstance(exts, list):
        for e in exts:
            if isinstance(e, str):
                names.add(e)
            elif isinstance(e, dict):
                n = e.get("extension") or e.get("name")
                if n:
                    names.add(str(n))
    present = {n for n in names if n in LETHAL_EXTENSIONS}
    if present:
        r.reject(f"lethal token-2022 extensions: {sorted(present)}")
    r.detail["extensions"] = sorted(names)

    if rep.get("mintAuthority"):
        r.reject("mint authority present (rugcheck)")
    if rep.get("freezeAuthority"):
        r.reject("freeze authority present (rugcheck)")

    # Single-wallet concentration, excluding the pool, LP vaults and lockers.
    # This is the check that gets written wrong most often: the largest holder
    # of almost every token is its own AMM reserve account, so a naive
    # "top holder > 25%" rule rejects the entire universe.
    infra: set[str] = set()
    for addr, meta in (rep.get("knownAccounts") or {}).items():
        if (meta or {}).get("type") in ("AMM", "LOCKER", "DEPLOYER"):
            infra.add(addr)
    for mk in rep.get("markets") or []:
        for k in ("pubkey", "liquidityA", "liquidityB", "mintLP"):
            if mk.get(k):
                infra.add(mk[k])

    retail = [
        h for h in (rep.get("topHolders") or [])
        if h.get("address") not in infra and h.get("owner") not in infra
    ]
    if retail:
        biggest = max((h.get("pct") or 0) for h in retail)
        if biggest > cfg.max_single_holder_pct:
            r.reject(f"non-pool holder {biggest:.1f}% > {cfg.max_single_holder_pct}%")
        top10 = sum(sorted((h.get("pct") or 0) for h in retail)[-10:])
        r.detail["retail_top10_pct"] = round(top10, 2)
        if top10 > cfg.max_top_holders_pct:
            r.reject(f"retail top-10 {top10:.1f}% > {cfg.max_top_holders_pct}%")

    # Coordinated supply, at entity level rather than address level.
    supply = ((rep.get("token") or {}).get("supply")) or 0
    networks = rep.get("insiderNetworks") or []
    clustered_pct = 0.0
    if supply and networks:
        clustered_raw = sum((n or {}).get("tokenAmount", 0) or 0 for n in networks)
        clustered_pct = 100.0 * clustered_raw / supply
        if clustered_pct > cfg.max_clustered_supply_pct:
            r.reject(f"coordinated wallets hold {clustered_pct:.1f}% of supply "
                     f"across {len(networks)} clusters (limit {cfg.max_clustered_supply_pct}%)")
        elif clustered_pct > cfg.max_clustered_supply_pct / 2:
            r.warnings.append(f"coordinated wallets hold {clustered_pct:.1f}% of supply")
    if len(networks) > cfg.max_insider_clusters:
        r.reject(f"{len(networks)} distinct coordinated clusters "
                 f"(limit {cfg.max_insider_clusters})")

    insiders = rep.get("graphInsidersDetected") or 0
    if insiders > 0:
        r.warnings.append(f"{insiders} linked insider wallets")

    r.detail = {
        "clustered_supply_pct": round(clustered_pct, 2),
        "insider_clusters": len(networks),
        "score": score,
        "score_normalised": rep.get("score_normalised"),
        "total_holders": rep.get("totalHolders"),
        "market_liquidity": rep.get("totalMarketLiquidity"),
        "lp_providers": rep.get("totalLPProviders"),
        "creator_tokens": len(rep.get("creatorTokens") or []),
        "insiders": insiders,
        "danger_risks": danger,
    }
    return r


def full_check(feat: dict[str, Any], cfg: SafetyConfig | None = None, deep: bool = True) -> SafetyResult:
    """Run the stages in order, stopping at the first failure."""
    cfg = cfg or SafetyConfig()
    local = check_local(feat, cfg)
    if not local.ok or not deep:
        return local
    deep_r = check_rugcheck(str(feat.get("mint")), cfg)
    merged = SafetyResult(
        ok=local.ok and deep_r.ok,
        rejects=local.rejects + deep_r.rejects,
        warnings=local.warnings + deep_r.warnings,
        detail={**local.detail, **deep_r.detail},
        stage="full",
    )
    return merged
