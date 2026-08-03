"""The air-quality ingester, offline.

The live call is what proved the source is worth adding -- 126 signals across 21
regions, Moscow at the current hour, where OpenAQ has been silent since 2017.
These tests pin the parts a live call cannot make fail on demand: a region that
errors, a response with no `current` block, a missing observation time, an
unparseable one.

The assertion that matters most is the dull one: every signal says it is
modelled. Where OpenAQ measured, this computes, and a number that cannot be told
apart from an instrument reading is a worse outcome than no number.
"""
from datetime import datetime, timezone

import pytest

from app.ingesters.openmeteo_air_quality import (
    OpenMeteoAirQualityIngester,
    POLLUTANTS,
    _parse_time,
)


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError("HTTP %d" % self._status)

    def json(self):
        return self._payload


class _Client:
    """Stands in for the httpx client, one scripted answer per region."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params))
        answer = self._answers.pop(0) if self._answers else _Response({"current": {}})
        if isinstance(answer, Exception):
            raise answer
        return answer


def _ingester(answers):
    ing = OpenMeteoAirQualityIngester()
    client = _Client(answers)
    ing.client = lambda: client          # type: ignore[method-assign]
    return ing, client


def _full_current(t="2026-08-03T12:00"):
    return _Response({"current": dict({"time": t}, **{p: 10.0 for p in POLLUTANTS})})


@pytest.mark.asyncio
async def test_every_signal_declares_that_it_is_modelled():
    """The honest cost of this source, asserted on every row rather than once."""
    ing, _ = _ingester([_full_current()])
    signals = await ing.fetch()

    assert signals
    for s in signals:
        assert s.metadata["measurement_kind"] == "modelled", s
        assert "CAMS" in s.metadata["model"], s


@pytest.mark.asyncio
async def test_one_failing_region_does_not_lose_the_others():
    """A region that errors costs its own records and nothing else.

    The alternative -- abandoning the run -- would turn a partial result into an
    empty one, which the classifier then reports as degraded for the wrong
    reason.
    """
    ing, _ = _ingester([RuntimeError("upstream 503"), _full_current(), _full_current()])
    signals = await ing.fetch()

    assert len(signals) == 2 * len(POLLUTANTS)
    assert len({s.region_code for s in signals}) == 2


@pytest.mark.asyncio
async def test_an_empty_current_block_yields_nothing_rather_than_zeroes():
    """Absent is not zero.

    Storing 0 µg/m³ for a region the source said nothing about would be a
    fabricated measurement, and one that looks entirely plausible.
    """
    ing, _ = _ingester([_Response({"current": {}}), _Response({})])
    assert await ing.fetch() == []


@pytest.mark.asyncio
async def test_a_pollutant_the_source_omitted_is_skipped_not_defaulted():
    payload = {"current": {"time": "2026-08-03T12:00", "pm10": 24.3}}
    ing, _ = _ingester([_Response(payload)])
    signals = await ing.fetch()

    assert [s.metric for s in signals] == ["pm10"]
    assert signals[0].value == 24.3


@pytest.mark.asyncio
async def test_the_observation_time_comes_from_the_source():
    ing, _ = _ingester([_full_current("2026-08-03T09:00")])
    signals = await ing.fetch()

    assert all(s.observed_at == datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc) for s in signals)


@pytest.mark.asyncio
async def test_all_six_pollutants_are_requested():
    """A silently shortened request would look like a source with less to give."""
    ing, client = _ingester([_full_current()])
    await ing.fetch()

    requested = client.calls[0][1]["current"].split(",")
    assert sorted(requested) == sorted(POLLUTANTS)


# --- the fallback for a missing period, which must stay visible ---------------

def test_a_missing_time_falls_back_and_says_so(caplog):
    """#58's defect is a fetch timestamp standing in for an observation period
    with nothing recording the substitution. Here the substitution happens --
    a timestamp is better than none -- but it is logged, never silent."""
    fallback = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with caplog.at_level("WARNING"):
        assert _parse_time(None, fallback, "RU-MOW") == fallback
    assert "no observation time" in caplog.text


def test_an_unparseable_time_falls_back_and_says_so(caplog):
    fallback = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with caplog.at_level("WARNING"):
        assert _parse_time("not-a-date", fallback, "RU-MOW") == fallback
    assert "unparseable" in caplog.text


def test_a_naive_time_is_read_as_utc():
    """The API is asked for UTC and returns times without an offset. Reading
    them as local would shift every observation by the host's timezone."""
    out = _parse_time("2026-08-03T12:00", datetime(2026, 1, 1, tzinfo=timezone.utc), "X")
    assert out == datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    assert out.tzinfo is timezone.utc
