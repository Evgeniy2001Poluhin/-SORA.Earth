from __future__ import annotations
import asyncio, logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Iterable
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

    # Appended with defaults so every existing positional constructor keeps
    # meaning what it meant. `observed` is the default because that is what the
    # honest sources already emit -- openmeteo carries real observation times,
    # and it must not be reclassified by a change aimed at the two that do not.
    #
    # A missing `observed_at` is NOT quietly reinterpreted as `not_applicable`.
    # Inferring the kind from an absent field is exactly how `now` came to be
    # written; a source with no observation time has to say which kind it is.
    temporal_kind: str = "observed"
    period_start: datetime | None = None
    period_end: datetime | None = None
    source_revision: str | None = None

class BaseIngester(ABC):
    name: str = "base"
    default_ttl_hours: int = 720
    """How long a fetched value stays usable before this source is polled again.

    A cache and scheduling concern. It is **not** how old the newest observation
    may be: for openmeteo the two coincide (hourly polls, hourly observations),
    and for rosstat they do not (a 180-day poll of an annual statistic, whose
    newest observation is legitimately over a year old). Reusing this as a
    vintage tolerance made every clean rosstat run escalate forever -- measured
    on production, vintage 590 days against a 180-day ttl (#74).
    """

    max_vintage_hours: Optional[int] = None
    """How old the newest *observation* may be before the source is unusable.

    `None` means no contract has been declared, and freshness then takes no part
    in the verdict: it can neither raise an action nor certify one. That is
    deliberate -- a threshold nobody wrote down is a threshold nobody agreed to,
    and inventing one produces alerts that are noise and silences that are
    unearned. The run record carries `freshness_status="not_configured"` so a
    `none` is never read as proven freshness.
    """
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
