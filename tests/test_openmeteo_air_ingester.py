"""Air quality ingester: the values, the units and the observation time.

Exists because OpenAQ has nothing recent for these regions -- every station
within 25 km of Moscow stopped reporting in September 2017, which is why
openaq_ingestion produced zero records across 333 runs (#56, #57).
"""

from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingesters.openmeteo_air import AIR_VARIABLES, OpenMeteoAirIngester

RESPONSE = {
    "current": {
        "time": "2026-07-31T21:00",
        "interval": 3600,
        "pm10": 28.4,
        "pm2_5": 21.7,
        "carbon_monoxide": 180.0,
        "nitrogen_dioxide": 12.3,
        "sulphur_dioxide": 3.1,
        "ozone": 64.0,
    },
    "current_units": {"pm2_5": "μg/m³"},
}


def _client_returning(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_every_pollutant_becomes_a_signal():
    ingester = OpenMeteoAirIngester()
    with patch.object(ingester, "client", return_value=_client_returning(RESPONSE)):
        signals = await ingester.fetch()

    metrics = {s.metric for s in signals}
    assert {"pm10", "pm25", "ozone", "nitrogen_dioxide",
            "sulphur_dioxide", "carbon_monoxide"} <= metrics
    assert all(s.source == "openmeteo_air" for s in signals), (
        "source must distinguish modelled CAMS values from OpenAQ station readings")


@pytest.mark.asyncio
async def test_the_observation_time_comes_from_the_api():
    """Not from the clock.

    Dating a value by when it was fetched rather than when it applies is the
    defect that left 87,005 indicator rows undated (#58). Not repeating it here.
    """
    ingester = OpenMeteoAirIngester()
    with patch.object(ingester, "client", return_value=_client_returning(RESPONSE)):
        signals = await ingester.fetch()

    assert signals
    for s in signals:
        assert s.observed_at.tzinfo is timezone.utc
        assert s.observed_at.isoformat().startswith("2026-07-31T21:00")


@pytest.mark.asyncio
async def test_a_missing_pollutant_is_skipped_not_zeroed():
    """A null is absence, not a reading of zero.

    Storing 0 for a pollutant the model did not report would be a measurement
    that never happened, and downstream nothing could tell the two apart.
    """
    payload = {"current": dict(RESPONSE["current"], pm2_5=None)}
    ingester = OpenMeteoAirIngester()
    with patch.object(ingester, "client", return_value=_client_returning(payload)):
        signals = await ingester.fetch()

    assert "pm25" not in {s.metric for s in signals}
    assert "pm10" in {s.metric for s in signals}, "the other pollutants must survive"


@pytest.mark.asyncio
async def test_an_empty_current_block_yields_nothing_and_does_not_raise():
    ingester = OpenMeteoAirIngester()
    with patch.object(ingester, "client", return_value=_client_returning({"current": {}})):
        signals = await ingester.fetch()
    assert signals == []


def test_units_are_stated_for_every_variable():
    """No pollutant may fall through to 'unknown'.

    Carbon monoxide in particular is reported in mg/m³ by some sources and µg/m³
    here; recording the wrong unit is worse than recording no value.
    """
    for var in AIR_VARIABLES:
        _, unit = OpenMeteoAirIngester._map_variable(var)
        assert unit == "ug/m3", "%s mapped to %r" % (var, unit)
