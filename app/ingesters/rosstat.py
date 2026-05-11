from __future__ import annotations
import logging
from datetime import datetime, timezone
from app.ingesters.base import BaseIngester, Signal

log = logging.getLogger(__name__)


class RosstatIngester(BaseIngester):
    """Offline snapshot: Rosstat + Minfin + Mincifry 2024.
    Emits S/G metrics for RF regions. Refresh ~half-year."""

    name = "rosstat"
    default_ttl_hours = 24 * 180

    async def fetch(self):
        try:
            from data.rosstat_snapshot_2024 import (
                UNEMPLOYMENT, INCOME, LIFE_EXP,
                BUDGET_TRANSPARENCY, DIGITAL_GOV,
            )
        except ImportError as e:
            log.error("[rosstat] snapshot missing: %s", e)
            return []

        now = datetime.now(timezone.utc)
        out: list[Signal] = []

        for code, v in UNEMPLOYMENT.items():
            out.append(Signal(code, self.name, "unemployment_rate", float(v), "%", now))
        for code, v in INCOME.items():
            out.append(Signal(code, self.name, "avg_income_rub", float(v) * 1000, "RUB", now))
        for code, v in LIFE_EXP.items():
            out.append(Signal(code, self.name, "life_expectancy", float(v), "years", now))
        for code, v in BUDGET_TRANSPARENCY.items():
            out.append(Signal(code, self.name, "budget_transparency", float(v), "0-100", now))
        for code, v in DIGITAL_GOV.items():
            out.append(Signal(code, self.name, "digital_gov_index", float(v), "0-100", now))

        log.info("[rosstat] emitted %d signals across %d regions",
                 len(out), len({s.region_code for s in out}))
        return out
