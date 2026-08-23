"""Shared HTTP client: browser UA (Cloudflare fronts most of these APIs),
retry with jitter, and a token-bucket rate limiter per host."""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .log import get

log = get("degen.http")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Requests-per-minute budgets. Deliberately conservative: a ban mid-session
# costs far more than the throughput it buys.
DEFAULT_RPM = {
    "lite-api.jup.ag": 55,
    "api.jup.ag": 55,
    "api.dexscreener.com": 250,
    "api.geckoterminal.com": 28,
    "api.rugcheck.xyz": 55,
    "api.mainnet-beta.solana.com": 90,
    "data.solanatracker.io": 55,
    "public-api.birdeye.so": 55,
}


class RateLimiter:
    """Token bucket, thread-safe, one per host."""

    def __init__(self, rpm: float) -> None:
        self.capacity = max(1.0, rpm / 6.0)  # allow short bursts of ~10s worth
        self.tokens = self.capacity
        self.rate = rpm / 60.0
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                deficit = (1.0 - self.tokens) / self.rate
            time.sleep(min(deficit, 2.0))


_limiters: dict[str, RateLimiter] = {}
_limiters_lock = threading.Lock()


def limiter_for(host: str) -> RateLimiter:
    with _limiters_lock:
        lim = _limiters.get(host)
        if lim is None:
            lim = RateLimiter(DEFAULT_RPM.get(host, 60))
            _limiters[host] = lim
        return lim


@dataclass
class HttpStats:
    ok: int = 0
    err: int = 0
    retries: int = 0
    by_code: dict[int, int] = field(default_factory=dict)

    def note(self, code: int, ok: bool) -> None:
        self.by_code[code] = self.by_code.get(code, 0) + 1
        if ok:
            self.ok += 1
        else:
            self.err += 1


STATS = HttpStats()

_session_local = threading.local()

# 429/5xx are transient. 418/403 from Cloudflare usually means UA/fingerprint
# trouble and retrying immediately just deepens the hole, so back off harder.
RETRY_CODES = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 530}


def session() -> requests.Session:
    s = getattr(_session_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            }
        )
        adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=0)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _session_local.s = s
    return s


def request(
    method: str,
    url: str,
    *,
    tries: int = 4,
    timeout: float = 25.0,
    backoff: float = 1.6,
    rate_limit: bool = True,
    **kw: Any,
) -> requests.Response | None:
    """Returns the Response on success, None once retries are exhausted.

    Callers get None rather than an exception because every collector loop
    would otherwise need the same try/except; a missed poll is not fatal.
    """
    host = url.split("/", 3)[2]
    delay = 1.0
    last: requests.Response | None = None
    for attempt in range(tries):
        if rate_limit:
            limiter_for(host).acquire()
        try:
            resp = session().request(method, url, timeout=timeout, **kw)
        except requests.RequestException as exc:
            STATS.note(0, False)
            if attempt == tries - 1:
                log.debug("%s %s failed after %d tries: %s", method, url[:90], tries, exc)
                return None
            STATS.retries += 1
            time.sleep(delay + random.uniform(0, 0.4 * delay))
            delay *= backoff
            continue

        STATS.note(resp.status_code, resp.ok)
        if resp.ok:
            return resp
        last = resp
        if resp.status_code not in RETRY_CODES or attempt == tries - 1:
            log.debug("%s %s -> %s %s", method, url[:90], resp.status_code, resp.text[:120])
            return None
        STATS.retries += 1
        wait = delay
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            if ra and ra.isdigit():
                wait = max(wait, float(ra))
            wait = max(wait, 3.0)
        time.sleep(wait + random.uniform(0, 0.4 * wait))
        delay *= backoff
    return last if (last is not None and last.ok) else None


def get_json(url: str, **kw: Any) -> Any | None:
    resp = request("GET", url, **kw)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        log.debug("non-json body from %s: %s", url[:90], resp.text[:120])
        return None


def post_json(url: str, payload: Any, **kw: Any) -> Any | None:
    resp = request("POST", url, json=payload, **kw)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None
