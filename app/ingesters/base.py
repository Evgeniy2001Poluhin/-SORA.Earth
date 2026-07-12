from __future__ import annotations
import asyncio, logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
import httpx

log = logging.getLogger(__name__)

@dataclass
class Signal:
    region_code: str
    source: str
    metric: str
    value: float | None
    unit: str | None = None
    observed_at: datetime | None = None
    metadata: dict | None = None

class BaseIngester(ABC):
    name: str = "base"
    default_ttl_hours: int = 720
    max_retries: int = 3
    timeout_s: float = 30.0

    @abstractmethod
    async def fetch(self) -> Iterable[Signal]: ...

    async def fetch_with_retry(self) -> list[Signal]:
        for attempt in range(1, self.max_retries + 1):
            try:
                return list(await self.fetch())
            except Exception as e:
                wait = 2 ** attempt
                log.warning(f"[{self.name}] attempt {attempt} failed: {e}; wait {wait}s")
                if attempt == self.max_retries:
                    log.error(f"[{self.name}] giving up, using stale data")
                    return []
                await asyncio.sleep(wait)
        return []

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True,
                                 headers={"User-Agent": "SORA-Earth/1.0 (ESG research)"})
