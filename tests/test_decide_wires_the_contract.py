"""The tolerance `_decide` uses, and the fields `_audit_finish` writes.

Both gaps here were found by mutations that survived. The tests for this feature
asserted on the pieces -- `freshness_status` with a tolerance passed in by hand,
and the API reading columns filled in by hand -- and never on the wiring
between them. So:

    _decide reading default_ttl_hours instead of max_vintage_hours   survived
    _audit_finish writing NULL for the threshold                     survived

which is the same shape as the defect in #145: a guard that exists, is tested
directly, and is called by nobody. These two exercise the call.
"""
import pytest

from app.ingesters.classification import (
    FRESH, NOT_CONFIGURED, STALE, classify_run,
)
from app.ingesters import runner


class _Ingester:
    """Only the attributes _decide reads."""

    def __init__(self, name, *, ttl=None, max_vintage=None):
        self.name = name
        if ttl is not None:
            self.default_ttl_hours = ttl
        if max_vintage is not None:
            self.max_vintage_hours = max_vintage


HOUR = 3600.0
DAY = 24 * HOUR


def _clean():
    return classify_run(received=10, accepted=10, rejected=0)


def test_the_polling_interval_is_not_used_as_a_tolerance(monkeypatch):
    """rosstat's production shape: a 590-day vintage, a 180-day poll, no
    declared tolerance. Reading `default_ttl_hours` here makes every clean run
    escalate forever."""
    monkeypatch.setattr(runner, "source_vintage",
                        lambda name: (590 * DAY, runner.VINTAGE_MEASURED))

    action, vintage, freshness, max_vintage = runner._decide(
        _Ingester("rosstat", ttl=24 * 180), _clean())

    assert freshness == NOT_CONFIGURED, (
        "the polling interval is standing in for a vintage contract"
    )
    assert max_vintage is None
    assert action == "none"
    assert vintage == pytest.approx(590 * DAY)


def test_a_declared_tolerance_is_the_one_used(monkeypatch):
    """And it is `max_vintage_hours`, not whatever else is on the class."""
    monkeypatch.setattr(runner, "source_vintage",
                        lambda name: (40 * DAY, runner.VINTAGE_MEASURED))

    action, _v, freshness, max_vintage = runner._decide(
        _Ingester("openaq", ttl=1, max_vintage=30 * 24), _clean())

    assert max_vintage == pytest.approx(30 * DAY), (
        "the tolerance came from somewhere other than max_vintage_hours"
    )
    assert freshness == STALE
    assert action == "escalate"


def test_a_source_with_no_axis_reports_not_applicable(monkeypatch):
    monkeypatch.setattr(runner, "source_vintage",
                        lambda name: (None, runner.VINTAGE_NOT_APPLICABLE))

    action, vintage, freshness, _m = runner._decide(
        _Ingester("sber_veb_baseline", ttl=8760), _clean())

    assert freshness == "not_applicable"
    assert vintage is None
    assert action == "none"


def test_within_tolerance_is_fresh(monkeypatch):
    monkeypatch.setattr(runner, "source_vintage",
                        lambda name: (2 * HOUR, runner.VINTAGE_MEASURED))

    action, _v, freshness, _m = runner._decide(
        _Ingester("openmeteo", ttl=1, max_vintage=6), _clean())

    assert freshness == FRESH
    assert action == "none"
