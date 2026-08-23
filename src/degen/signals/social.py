"""Social signal ingestion: X/Twitter and Telegram.

What this is for, and what it is honestly worth.

**The measured literature says social feeds are a disqualifier, not an entry
trigger, and this module is built on that finding rather than against it.**

Buying at a call has negative expected value, repeatedly measured. Across 10,687
pump events on 765 coins the price peaks at roughly two minutes and the average
five-minute maximum gain is 15% on liquid venues; ranked members of tiered
channels receive the signal 1-10 seconds before ordinary members, and abnormal
*sell* volume shows up at second 19 - the dump begins while followers are still
buying. One study documents a channel announcing a coin when the price was
already at its peak, making follower profit arithmetically impossible. The
correlation between a channel's audience size and the resulting pump is
**-0.162**: a bigger channel is not a better signal. Both of the best-performing
published Solana rug models use zero social features.

So this module exists to answer three questions, none of which is "should I
buy":

1. **Is the promotion fake?** Bot-amplified mention campaigns are measurable -
   61% of accounts amplifying a sampled set of promotions were bot-like, and 36%
   of the promoted projects were fraudulent.
2. **Is the token's own account borrowed?** An old account that pivoted to crypto
   two weeks ago after a long silence is a repurposed or hacked handle.
3. **Is anyone real arriving?** Attention *breadth* - distinct credible accounts
   rather than volume - as a weak confirming input, never an originating one.

Nobody writes anything analysable about a token that is four minutes old, so
sentiment is not on the list.

Two structural cautions that shape the design:

**By the time a call lands you are frequently the exit.** A channel that posts a
contract address to fifty thousand people creates a buy spike followed by a
distribution into it. The tracker therefore measures *velocity and breadth*, not
"a big account posted", and the scorer treats a single large account posting a
brand-new CA as a mild negative unless breadth confirms it.

**Most of the volume is manufactured.** Reply-spam bots post contract addresses
under every large crypto tweet. Quality weighting (account age, follower count,
whether the account posts a different CA every ten minutes) is not optional
garnish - without it the signal measures bot activity.

No provider here is required. With no credentials the tracker returns
`available=False` and the composite scorer simply ignores social entirely
rather than silently scoring zero, which would penalise every token equally and
add noise.

Provider options, cheapest first:
  - `NullProvider`      - default. No credentials, no signal.
  - `XApiProvider`      - official X API v2 recent search. Needs a bearer token;
                          the paid tiers are the only ones with usable volume.
  - `GenericHttpProvider` - any third-party search-for-hire endpoint that returns
                          JSON; configured by URL template and a small field map,
                          so swapping vendors is configuration, not code.
  - `TelegramProvider`  - Telethon client reading channels you have joined.
"""
from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..util.http import get_json
from ..util.log import get
from ..util.timeutil import now, parse_iso

log = get("degen.social")

# Solana mint addresses are base58, 32-44 chars. Used to find CAs in free text.
CA_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


@dataclass
class Mention:
    text: str
    author: str
    author_followers: int = 0
    author_created_at: float | None = None
    at: float = field(default_factory=now)
    url: str | None = None
    source: str = "unknown"

    @property
    def author_age_days(self) -> float:
        if not self.author_created_at:
            return 0.0
        return max(0.0, (now() - self.author_created_at) / 86400.0)

    def quality(self) -> float:
        """0..1 credibility weight.

        Deliberately harsh. A three-day-old account with eleven followers
        posting a contract address is the single most common artefact in this
        data and must not count the same as a real account.
        """
        import math

        f = math.log1p(max(0, self.author_followers)) / math.log1p(100_000)
        a = min(1.0, self.author_age_days / 180.0)
        return float(min(1.0, 0.65 * f + 0.35 * a))


class SocialProvider(ABC):
    name = "base"

    @property
    def available(self) -> bool:
        return False

    @abstractmethod
    def search(self, query: str, limit: int = 50) -> list[Mention]: ...


class NullProvider(SocialProvider):
    name = "null"

    def search(self, query: str, limit: int = 50) -> list[Mention]:
        return []


class XApiProvider(SocialProvider):
    """Official X API v2 recent search.

    Set DEGEN_X_BEARER. Note the free tier does not include recent search at
    usable volume; this needs a paid tier to be worth wiring up.
    """

    name = "x_api"
    BASE = "https://api.x.com/2/tweets/search/recent"

    def __init__(self, bearer: str | None = None) -> None:
        self.bearer = (bearer or os.getenv("DEGEN_X_BEARER", "")).strip()

    @property
    def available(self) -> bool:
        return bool(self.bearer)

    def search(self, query: str, limit: int = 50) -> list[Mention]:
        if not self.available:
            return []
        params = (
            f"query={query}&max_results={min(max(limit, 10), 100)}"
            "&tweet.fields=created_at,author_id,public_metrics"
            "&expansions=author_id&user.fields=public_metrics,created_at,username"
        )
        d = get_json(f"{self.BASE}?{params}", headers={"Authorization": f"Bearer {self.bearer}"}, tries=2)
        if not d:
            return []
        users = {u["id"]: u for u in ((d.get("includes") or {}).get("users") or [])}
        out: list[Mention] = []
        for t in d.get("data") or []:
            u = users.get(t.get("author_id"), {})
            pm = u.get("public_metrics") or {}
            out.append(Mention(
                text=t.get("text", ""),
                author=u.get("username") or str(t.get("author_id")),
                author_followers=int(pm.get("followers_count") or 0),
                author_created_at=parse_iso(u.get("created_at")),
                at=parse_iso(t.get("created_at")) or now(),
                url=f"https://x.com/i/status/{t.get('id')}",
                source="x",
            ))
        return out


class GenericHttpProvider(SocialProvider):
    """Any JSON search endpoint, described by configuration.

    Third-party X-search vendors come and go and all return slightly different
    shapes. Rather than a class per vendor, point this at a URL template with
    `{query}` and give it a field map.
    """

    name = "generic_http"

    def __init__(
        self,
        url_template: str | None = None,
        headers: dict[str, str] | None = None,
        items_key: str = "tweets",
        field_map: dict[str, str] | None = None,
    ) -> None:
        self.url_template = url_template or os.getenv("DEGEN_SOCIAL_URL", "")
        self.headers = headers or ({"X-API-Key": os.getenv("DEGEN_SOCIAL_KEY", "")}
                                   if os.getenv("DEGEN_SOCIAL_KEY") else {})
        self.items_key = items_key
        self.fm = field_map or {
            "text": "text", "author": "author.userName",
            "followers": "author.followers", "created": "createdAt",
            "author_created": "author.createdAt", "url": "url",
        }

    @property
    def available(self) -> bool:
        return bool(self.url_template)

    @staticmethod
    def _dig(obj: Any, path: str) -> Any:
        cur = obj
        for part in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    def search(self, query: str, limit: int = 50) -> list[Mention]:
        if not self.available:
            return []
        from urllib.parse import quote

        d = get_json(self.url_template.format(query=quote(query)), headers=self.headers or None, tries=2)
        if not d:
            return []
        items = d.get(self.items_key) if isinstance(d, dict) else d
        out: list[Mention] = []
        for it in (items or [])[:limit]:
            out.append(Mention(
                text=str(self._dig(it, self.fm["text"]) or ""),
                author=str(self._dig(it, self.fm["author"]) or "?"),
                author_followers=int(self._dig(it, self.fm["followers"]) or 0),
                author_created_at=parse_iso(self._dig(it, self.fm["author_created"])),
                at=parse_iso(self._dig(it, self.fm["created"])) or now(),
                url=self._dig(it, self.fm["url"]),
                source="generic",
            ))
        return out


class TelegramProvider(SocialProvider):
    """Reads channels you have already joined, via Telethon.

    Requires DEGEN_TG_API_ID, DEGEN_TG_API_HASH and an existing session file.
    Telethon is an optional dependency and is imported lazily.
    """

    name = "telegram"

    def __init__(self, channels: Iterable[str] | None = None, session: str = "degen") -> None:
        self.channels = list(channels or [c for c in os.getenv("DEGEN_TG_CHANNELS", "").split(",") if c])
        self.session = session
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.channels and os.getenv("DEGEN_TG_API_ID") and os.getenv("DEGEN_TG_API_HASH"))

    def _connect(self):
        if self._client is not None:
            return self._client
        try:
            from telethon.sync import TelegramClient
        except ImportError:
            log.warning("telethon not installed; telegram provider disabled")
            return None
        self._client = TelegramClient(
            self.session, int(os.getenv("DEGEN_TG_API_ID")), os.getenv("DEGEN_TG_API_HASH")
        )
        self._client.connect()
        return self._client

    def search(self, query: str, limit: int = 50) -> list[Mention]:
        if not self.available:
            return []
        client = self._connect()
        if client is None:
            return []
        out: list[Mention] = []
        for ch in self.channels:
            try:
                for msg in client.iter_messages(ch, limit=limit, search=query):
                    if not msg.message:
                        continue
                    out.append(Mention(
                        text=msg.message, author=str(ch), author_followers=0,
                        at=msg.date.timestamp() if msg.date else now(),
                        source="telegram",
                    ))
            except Exception as exc:
                log.debug("telegram channel %s failed: %s", ch, exc)
        return out


@dataclass
class SocialFeatures:
    available: bool = False
    n_mentions: int = 0
    n_unique_authors: int = 0
    weighted_mentions: float = 0.0
    max_author_followers: int = 0
    velocity_per_min: float = 0.0
    first_mention_lag_s: float | None = None
    bot_ratio: float = 0.0
    breadth: float = 0.0          # unique authors / mentions
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {f"social_{k}": v for k, v in self.__dict__.items()}


class SocialTracker:
    """Aggregates mentions per token and turns them into features."""

    def __init__(self, providers: list[SocialProvider] | None = None, window_s: float = 900.0) -> None:
        if providers is None:
            providers = [XApiProvider(), GenericHttpProvider(), TelegramProvider()]
        self.providers = [p for p in providers if p.available] or [NullProvider()]
        self.window_s = window_s
        self.seen: dict[str, deque[Mention]] = defaultdict(lambda: deque(maxlen=800))
        log.info("social providers active: %s", [p.name for p in self.providers])

    @property
    def available(self) -> bool:
        return any(p.available for p in self.providers)

    def poll(self, mint: str, symbol: str | None = None) -> int:
        """Fetch mentions for a token. Searching the contract address is much
        cleaner than the symbol, which collides constantly."""
        if not self.available:
            return 0
        found = 0
        for p in self.providers:
            for m in p.search(mint, limit=50):
                self.seen[mint].append(m)
                found += 1
        return found

    def features(self, mint: str, launched_at: float | None = None) -> SocialFeatures:
        if not self.available:
            return SocialFeatures(available=False)
        t = now()
        recent = [m for m in self.seen.get(mint, ()) if t - m.at <= self.window_s]
        if not recent:
            return SocialFeatures(available=True)

        authors = {m.author for m in recent}
        weighted = sum(m.quality() for m in recent)
        low_quality = sum(1 for m in recent if m.quality() < 0.15)
        span_min = max(1.0, (t - min(m.at for m in recent)) / 60.0)
        first_lag = (min(m.at for m in recent) - launched_at) if launched_at else None

        f = SocialFeatures(
            available=True,
            n_mentions=len(recent),
            n_unique_authors=len(authors),
            weighted_mentions=round(weighted, 3),
            max_author_followers=max((m.author_followers for m in recent), default=0),
            velocity_per_min=round(len(recent) / span_min, 3),
            first_mention_lag_s=first_lag,
            bot_ratio=round(low_quality / len(recent), 3),
            breadth=round(len(authors) / len(recent), 3),
        )

        # Breadth is what distinguishes organic arrival from a single account
        # spamming, so the score is dominated by distinct credible authors.
        import math

        base = math.log1p(weighted) / math.log1p(60.0)
        f.score = float(max(0.0, min(1.0, base * f.breadth * (1.0 - 0.6 * f.bot_ratio))))
        return f


# --- disqualifier checks -------------------------------------------------

# An account older than this that only started posting crypto recently, after a
# long silence, is the classic repurposed-or-hacked handle signature.
REPURPOSED_MIN_AGE_DAYS = 365.0
REPURPOSED_MAX_CRYPTO_AGE_DAYS = 14.0

# Bot-amplification proxy, standing in for Botometer, which is no longer
# available. Any one of these tripping is enough to distrust the promotion.
BOT_FRESH_ACCOUNT_FRAC = 0.50      # share of engagers created within 90 days
BOT_FOLLOW_RATIO = 0.10            # followers/following below this reads automated
BOT_DUPLICATE_TEXT_FRAC = 0.30     # near-identical mention text share


def looks_repurposed(account_age_days: float, first_crypto_post_age_days: float,
                     silence_gap_days: float = 0.0) -> bool:
    """Old handle, new crypto content, a gap in between."""
    return (
        account_age_days >= REPURPOSED_MIN_AGE_DAYS
        and first_crypto_post_age_days <= REPURPOSED_MAX_CRYPTO_AGE_DAYS
        and silence_gap_days >= 30.0
    )


def bot_amplification(mentions: list[Mention]) -> dict[str, Any]:
    """Judge whether a mention set is a manufactured campaign."""
    if not mentions:
        return {"flagged": False, "reasons": [], "fresh_frac": 0.0, "dup_frac": 0.0}
    reasons: list[str] = []
    fresh = sum(1 for m in mentions if 0 < m.author_age_days < 90) / len(mentions)
    if fresh > BOT_FRESH_ACCOUNT_FRAC:
        reasons.append(f"{fresh:.0%} of engagers are accounts under 90 days old")
    # Near-duplicate text: normalise and count repeats.
    seen: dict[str, int] = {}
    for m in mentions:
        key = " ".join(sorted(set((m.text or "").lower().split())))[:120]
        seen[key] = seen.get(key, 0) + 1
    dup = sum(c for c in seen.values() if c > 1) / len(mentions)
    if dup > BOT_DUPLICATE_TEXT_FRAC:
        reasons.append(f"{dup:.0%} of mentions are near-duplicate text")
    return {"flagged": bool(reasons), "reasons": reasons,
            "fresh_frac": round(fresh, 3), "dup_frac": round(dup, 3)}


def extract_contract_addresses(text: str) -> list[str]:
    """Pull candidate Solana mints out of free text (a call channel message).

    Deliberately permissive; callers should confirm each candidate resolves to a
    real mint before acting, since base58 words occasionally match.
    """
    return [m for m in CA_RE.findall(text or "") if not m.isdigit()]
