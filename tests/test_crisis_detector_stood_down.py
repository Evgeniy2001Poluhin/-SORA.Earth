"""Crisis detection is stood down by default, and the reason must be stated.

This detector queries the `region_signals` table for OpenAQ metric names
(pm25_ugm3, no2_ugm3, o3_ugm3, so2_ugm3). Nothing has written to that table
since the ingester runner moved to `environmental_observations` on 2026-07-30
(#116), and OpenAQ itself was stood down because its stations stopped reporting
in September 2017 (#57). The job structurally finds zero violations on every
run and only logs.

Scheduling it would produce a job that runs every 6 hours and finds nothing:
not a signal, just noise.
"""
import importlib

import pytest



@pytest.fixture(autouse=True)
def _leave_the_scheduler_empty():
    """Remove the jobs init_scheduler registered on the shared module-level scheduler.

    tests/test_offline_isolation.py asserts that scheduler is empty, and this
    file sorts before it. Measured: without this, running the two files in that
    order fails it with 34 leftover jobs.
    """
    yield
    from app import scheduler as scheduler_module
    scheduler_module.scheduler.remove_all_jobs()

def test_crisis_detection_is_off_unless_explicitly_enabled(monkeypatch):
    from app.services.crisis_detector import crisis_detection_scheduling_refusal

    monkeypatch.delenv("SORA_CRISIS_DETECTION_ENABLED", raising=False)
    refusal = crisis_detection_scheduling_refusal()

    assert refusal is not None
    assert "region_signals" in refusal


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "enabled"])
def test_it_can_be_turned_on(monkeypatch, value):
    """The default must be overridable, or the flag is decoration."""
    from app.services.crisis_detector import crisis_detection_scheduling_refusal

    monkeypatch.setenv("SORA_CRISIS_DETECTION_ENABLED", value)
    assert crisis_detection_scheduling_refusal() is None


@pytest.mark.parametrize("value", ["", "off", "false", "no", "0", "maybe"])
def test_anything_else_leaves_it_off(monkeypatch, value):
    from app.services.crisis_detector import crisis_detection_scheduling_refusal

    monkeypatch.setenv("SORA_CRISIS_DETECTION_ENABLED", value)
    refusal = crisis_detection_scheduling_refusal()

    assert refusal is not None


def test_the_refusal_states_what_is_wrong():
    """The reason says why this cannot work, not only that it is off."""
    from app.services.crisis_detector import crisis_detection_scheduling_refusal

    refusal = crisis_detection_scheduling_refusal()

    assert refusal is not None
    # The table it reads.
    assert "region_signals" in refusal
    # When that table stopped being written.
    assert "2026-07-30" in refusal
    # What metric names it expects.
    assert "pm25_ugm3" in refusal or "OpenAQ" in refusal


def test_the_default_does_not_register_the_job(monkeypatch):
    """Flag off → no job registered."""
    import warnings
    from app import scheduler as scheduler_module

    monkeypatch.setenv("RUN_SCHEDULER", "true")
    monkeypatch.delenv("SORA_CRISIS_DETECTION_ENABLED", raising=False)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scheduler_module.init_scheduler(start=False)

    job_ids = {j.id for j in scheduler_module.scheduler.get_jobs()}
    assert "auto_crisis_detection" not in job_ids


def test_the_flag_on_registers_the_job(monkeypatch):
    """The gate must be able to open, or the test above proves nothing."""
    import warnings
    from app import scheduler as scheduler_module

    monkeypatch.setenv("RUN_SCHEDULER", "true")
    monkeypatch.setenv("SORA_CRISIS_DETECTION_ENABLED", "true")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scheduler_module.init_scheduler(start=False)

    job_ids = {j.id for j in scheduler_module.scheduler.get_jobs()}
    assert "auto_crisis_detection" in job_ids


def test_the_refusal_log_says_how_to_override(monkeypatch, caplog):
    """An operator reading one line must see what to set."""
    import logging
    import warnings
    from app import scheduler as scheduler_module

    monkeypatch.setenv("RUN_SCHEDULER", "true")
    monkeypatch.delenv("SORA_CRISIS_DETECTION_ENABLED", raising=False)

    with caplog.at_level(logging.INFO), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scheduler_module.init_scheduler(start=False)

    text = caplog.text
    assert "crisis detection not scheduled" in text
    assert "SORA_CRISIS_DETECTION_ENABLED" in text


def test_nothing_writes_to_region_signals_outside_tests():
    """The reason the detector finds nothing, verified in the code.

    This is not "the detector is broken"; it is "the table it reads has no
    current writer". If a writer appears, this test fails and says so, which is
    exactly when the job should be reconsidered rather than kept off.
    """
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[1]
    app_py_files = list((root / "app").rglob("*.py"))
    scripts_py_files = list((root / "scripts").rglob("*.py")) if (root / "scripts").exists() else []

    writers = []
    for path in app_py_files + scripts_py_files:
        if "/tests/" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Look for RegionSignal instantiations or INSERT INTO region_signals
        if re.search(r"RegionSignal\s*\(", text) or "INSERT INTO region_signals" in text:
            # Exclude the model definition itself
            if "class RegionSignal" not in text:
                writers.append(str(path.relative_to(root)))

    assert writers == [], (
        f"region_signals has writers in app/ or scripts/: {writers}. "
        "If a real writer exists, crisis detection should be reconsidered "
        "rather than kept stood down."
    )
