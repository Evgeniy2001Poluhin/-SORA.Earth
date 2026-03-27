import time
from collections import defaultdict
from fastapi import Request, HTTPException


class RateLimiter:
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = defaultdict(list)

    def check(self, key: str):
        now = time.time()
        self.requests[key] = [t for t in self.requests[key] if now - t < self.window]
        if len(self.requests[key]) >= self.max_requests:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        self.requests[key].append(now)

    def get_usage(self, key: str):
        now = time.time()
        self.requests[key] = [t for t in self.requests[key] if now - t < self.window]
        return {"used": len(self.requests[key]), "limit": self.max_requests, "window_seconds": self.window}


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
