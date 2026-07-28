import ipaddress
import os
import threading
import time
from collections import OrderedDict
from fastapi import Request, HTTPException


class RateLimiter:
    """Fixed-window counter over a bounded, LRU-ordered key space.

    The bound matters: keys derived from anything a caller supplies -- a
    username, a header -- let an attacker mint a bucket per request. Unbounded,
    that is memory exhaustion for free.

    The *cost* of the bound matters just as much. Scanning or sorting the whole
    table on each request turns a memory cap into a CPU amplifier: once the
    table is full, every further request pays for the whole table. So the
    entries are held in an OrderedDict kept in least-recently-touched order,
    which makes both eviction and expiry O(1) amortised -- the front is always
    the oldest, so a short sweep from the front is enough and a full scan is
    never needed.

    Time comes from a monotonic clock; a wall-clock adjustment would otherwise
    either free every budget at once or freeze them.

    The counter lives in the process. With several workers the effective budget
    is multiplied by the worker count, so this is defence in depth rather than
    the primary control -- see issue #36.
    """

    MAX_BUCKETS = 10_000
    SWEEP_PER_CALL = 8

    def __init__(self, max_requests: int = 100, window_seconds: int = 60,
                 max_buckets: int | None = None):
        self.max_requests = max_requests
        self.window = window_seconds
        self.max_buckets = max_buckets or self.MAX_BUCKETS
        self.requests: "OrderedDict[str, list]" = OrderedDict()
        self._lock = threading.Lock()
        # Buckets examined during cleanup, cumulative. Asserted in tests: a
        # timing comparison would be flaky, but the count is exact.
        self.inspected = 0

    def _sweep(self, now: float):
        """Retire a bounded number of the oldest buckets.

        Every touch moves its bucket to the back, so the front is the least
        recently seen. The moment the front is still inside the window,
        everything behind it is too, and the sweep can stop.
        """
        for _ in range(self.SWEEP_PER_CALL):
            if not self.requests:
                return
            _, hits = next(iter(self.requests.items()))
            self.inspected += 1
            if hits and now - hits[-1] < self.window:
                return
            self.requests.popitem(last=False)

    def check(self, key: str):
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            hits = [t for t in self.requests.get(key, ()) if now - t < self.window]
            if len(hits) >= self.max_requests:
                self.requests[key] = hits
                self.requests.move_to_end(key)
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(self.window)},
                )
            hits.append(now)
            self.requests[key] = hits
            self.requests.move_to_end(key)
            # At most one eviction per insertion, from the front: a flood of
            # single-use keys is discarded before buckets belonging to callers
            # who keep coming back.
            while len(self.requests) > self.max_buckets:
                self.requests.popitem(last=False)

    def get_usage(self, key: str):
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self.requests.get(key, ()) if now - t < self.window]
            if hits:
                self.requests[key] = hits
                self.requests.move_to_end(key)
            elif key in self.requests:
                del self.requests[key]
        return {"used": len(hits), "limit": self.max_requests, "window_seconds": self.window}



# nginx terminates TLS and proxies to the app, so request.client.host is
# nginx's address rather than the caller's. Keying a per-source limit on it
# would collapse every client into one bucket -- which stops being a control on
# abuse and becomes an outage for everyone else.
#
# The default trusts the RFC1918 ranges because in this deployment *external*
# traffic can only arrive through nginx: docker-compose.prod.yml publishes
# ports for nginx alone, the backend declares none, and nginx overwrites
# X-Real-IP with $remote_addr. That is weaker than trusting nginx's exact
# address, though -- any other container on the same private network could also
# reach the backend and claim an address. Narrowing this to the compose subnet,
# or setting SORA_TRUSTED_PROXIES explicitly, is the stronger configuration and
# is tracked as hardening rather than done here.
#
# The proxy-supplied header is only believed when the peer is itself a trusted
# proxy. A peer address cannot be forged over TCP, so that check is what makes
# the header trustworthy; believing it unconditionally would let any caller
# mint a fresh identity per request and skip the limit entirely.
TRUSTED_PROXY_NETWORKS = os.getenv(
    "SORA_TRUSTED_PROXIES",
    "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
)


def _trusted_networks():
    nets = []
    for raw in TRUSTED_PROXY_NETWORKS.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return nets


def client_address(request) -> str:
    """The caller's address, believing proxy headers only from a trusted peer."""
    peer = request.client.host if getattr(request, "client", None) else ""
    if not peer:
        return "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return "unknown"
    if not any(peer_ip in net for net in _trusted_networks()):
        return peer

    # X-Real-IP is what this deployment's nginx sets, and it carries exactly
    # one address. X-Forwarded-For is a list a client can prepend to, so only
    # its last hop -- the one our own proxy appended -- would be meaningful.
    forwarded = ""
    headers = getattr(request, "headers", None)
    if headers is not None:
        forwarded = (headers.get("x-real-ip") or "").strip()
    if not forwarded:
        return peer
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return peer


rate_limiter = RateLimiter(max_requests=100, window_seconds=60)

# Aliases for legacy imports
limiter = rate_limiter

class RateLimitExceeded(Exception):
    pass

class SlowAPIMiddleware:
    """Stub middleware for compatibility."""
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)

def rate_limit_handler(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
