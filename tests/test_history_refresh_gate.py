"""The scheduled history refresh is off unless switched on.

`auto_refresh_external_data` is in the scheduler's immediate-run set, so a
deployment starts it within seconds of the container coming up. With the
series write path enabled by default, the first mass ingestion would therefore
happen *as a side effect of deploying* -- unobserved, and racing anyone running
it by hand. The gate exists so the first run can be a deliberate one.

What matters is the default and the fact that `refresh_live_data` consults it,
so both are asserted here rather than left to a reading of the source.
"""

from unittest.mock import MagicMock

import pytest

import app.external_data as ed


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "on", "On", "yes", " on "])
def test_values_that_enable_it(monkeypatch, value):
    monkeypatch.setenv("SORA_HISTORY_REFRESH", value)
    assert ed.history_refresh_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["0", "off", "false", "no", "", "  ", "maybe", "onn", "enable", "2"],
)
def test_values_that_do_not(monkeypatch, value):
    """Everything that is not an explicit yes.

    Enumerated rather than sampled: a gate that guards a mass write path must
    not be opened by a typo, and "enable" or "onn" reading as true is exactly
    how that happens.
    """
    monkeypatch.setenv("SORA_HISTORY_REFRESH", value)
    assert ed.history_refresh_enabled() is False


def test_the_default_is_off(monkeypatch):
    """Unset must mean off. This is the whole protection."""
    monkeypatch.delenv("SORA_HISTORY_REFRESH", raising=False)
    assert ed.history_refresh_enabled() is False


def _stub_refresh(monkeypatch, calls):
    """Enough of refresh_live_data's surroundings to call it without a database."""
    monkeypatch.setattr(ed, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(ed, "DataRefreshLog", MagicMock())
    monkeypatch.setattr(ed, "refresh_all_countries",
                        lambda: {"fetched": 1, "total": 1, "countries": {}})
    monkeypatch.setattr(
        ed, "refresh_indicator_history",
        lambda **kw: calls.append(kw) or {
            "fetched": 0, "inserted": 0, "unchanged": 0,
            "revised": 0, "no_value": 0, "no_period": 0,
        },
    )


def test_the_scheduled_refresh_skips_history_when_off(monkeypatch):
    """The case that makes a deployment safe."""
    calls = []
    _stub_refresh(monkeypatch, calls)
    monkeypatch.delenv("SORA_HISTORY_REFRESH", raising=False)

    ed.refresh_live_data(trigger_source="test")

    assert calls == [], "the history refresh ran with the gate off"


def test_the_scheduled_refresh_runs_history_when_on(monkeypatch):
    """And the gate is not simply a permanent off switch."""
    calls = []
    _stub_refresh(monkeypatch, calls)
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")

    ed.refresh_live_data(trigger_source="test")

    assert len(calls) == 1
