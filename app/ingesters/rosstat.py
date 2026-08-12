from __future__ import annotations
import logging
from datetime import datetime, timezone
from app.ingesters.base import BaseIngester, Signal
from app.ingesters import temporal

log = logging.getLogger(__name__)


#: The snapshot's reference year, as bounds. The docstring of this module
#: calls it "Rosstat + Minfin + Mincifry 2024"; nothing narrows it further, so
#: nothing here pretends to.
PERIOD_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


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

        # The 2024 reference year, as an interval. Not a date inside it: naming
        # 2024-01-01 would replace one false precision with another, which is
        # the defect this change removes. `now` used to be stamped here and
        # recorded as when the numbers were observed (#121).
        revision = temporal.content_revision(
            self.name,
            {"UNEMPLOYMENT": UNEMPLOYMENT, "INCOME": INCOME, "LIFE_EXP": LIFE_EXP,
             "BUDGET_TRANSPARENCY": BUDGET_TRANSPARENCY, "DIGITAL_GOV": DIGITAL_GOV},
        )
        out: list[Signal] = []

        def _period(code, metric, value, unit):
            return Signal(code, self.name, metric, value, unit,
                          observed_at=None,
                          temporal_kind=temporal.PERIOD,
                          period_start=PERIOD_START, period_end=PERIOD_END,
                          source_revision=revision)

        for code, v in UNEMPLOYMENT.items():
            out.append(_period(code, "unemployment_rate", float(v), "%"))
        for code, v in INCOME.items():
            out.append(_period(code, "avg_income_rub", float(v) * 1000, "RUB"))
        for code, v in LIFE_EXP.items():
            out.append(_period(code, "life_expectancy", float(v), "years"))
        for code, v in BUDGET_TRANSPARENCY.items():
            out.append(_period(code, "budget_transparency", float(v), "0-100"))
        for code, v in DIGITAL_GOV.items():
            out.append(_period(code, "digital_gov_index", float(v), "0-100"))

        log.info("[rosstat] emitted %d signals across %d regions",
                 len(out), len({s.region_code for s in out}))
        return out
