import ipaddress
import os
import time
from collections import defaultdict
from fastapi import Request, HTTPException


class RateLimiter:
    """Fixed-window counter with a bounded key space.

    The bound matters: keys derived from anything a caller supplies -- a
    username, a header -- let an attacker mint a new bucket per request. With
    an unbounded dict that is a memory-exhaustion vector that costs the
    attacker nothing. Buckets are therefore dropped as soon as they empty, and
    the table is capped; past the cap the least recently touched buckets go
    first, which is exactly the set an attacker floods with.

    The counter lives in the process. With several workers the effective budget
    is multiplied by the worker count, so this is defence in depth rather than
    the primary control -- see GAP note in app/auth.py.
    """

    MAX_BUCKETS = 10_000

    def __init__(self, max_requests: int = 100, window_seconds: int = 60,
                 max_buckets: int | None = None):
        self.max_requests = max_requests
        self.window = window_seconds
        self.max_buckets = max_buckets or self.MAX_BUCKETS
        self.requests = defaultdict(list)

    def _drop_expired(self, now: float):
        for k in [k for k, hits in self.requests.items()
                  if not hits or now - hits[-1] >= self.window]:
            del self.requests[k]

    def _enforce_cap(self):
        """Cap after insertion, so the table never exceeds the bound.

        Oldest last-seen first: a flood of single-use keys is evicted before
        buckets belonging to callers who keep coming back, which is the
        opposite of what insertion order would give.
        """
        excess = len(self.requests) - self.max_buckets
        if excess <= 0:
            return
        for k, _ in sorted(self.requests.items(), key=lambda kv: kv[1][-1])[:excess]:
            del self.requests[k]

    def check(self, key: str):
        now = time.time()
        self._drop_expired(now)
        hits = [t for t in self.requests.get(key, ()) if now - t < self.window]
        if len(hits) >= self.max_requests:
            self.requests[key] = hits
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(self.window)},
            )
        hits.append(now)
        self.requests[key] = hits
        self._enforce_cap()

    def get_usage(self, key: str):
        now = time.time()
        self.requests[key] = [t for t in self.requests[key] if now - t < self.window]
        return {"used": len(self.requests[key]), "limit": self.max_requests, "window_seconds": self.window}



# nginx terminates TLS and proxies to the app, so request.client.host is
# nginx's address rather than the caller's. Keying a per-source limit on it
# would collapse every client into one bucket -- which stops being a control on
# abuse and becomes an outage for everyone else.
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
