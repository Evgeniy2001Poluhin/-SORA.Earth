"""The scheduler's counters must be real, and importing it must stay cheap.

Both halves were broken by one line. `app/scheduler.py` imported six counters
from `app.main`, which defines none of them; the ImportError was caught and all
six became None, so no scheduler metric was recorded at all -- retrains,
refreshes, drift, promotions, rejections. The cost was still paid, because the
exception is raised only after app.main has finished importing torch, SHAP and
transformers.

Three of the names differ between the two modules: the `_total` suffix belongs
to the Prometheus metric name, not the Python variable. That is why this asserts
the objects rather than the import statement.
"""

import sys

import pytest

COUNTERS = [
    "sora_retrain_total",
    "sora_refresh_total",
    "sora_full_pipeline_total",
    "sora_drift_detected_total",
    "sora_model_promoted_total",
    "sora_model_rejected_total",
]


def test_every_counter_is_a_real_counter():
    import app.scheduler as scheduler

    missing = [name for name in COUNTERS if getattr(scheduler, name, None) is None]
    assert not missing, (
        "these are None, so the scheduler records nothing for them: %s" % missing)


# Which counters carry a status label, taken from app/prom_metrics.py rather than
# assumed: the three run-outcome counters do, the three event counters do not.
LABELLED = {"sora_retrain_total", "sora_refresh_total", "sora_full_pipeline_total"}


@pytest.mark.parametrize("name", COUNTERS)
def test_each_counter_works_with_its_real_call_pattern(name):
    """Called the way the call sites call it, not merely inspected.

    An earlier version asserted `hasattr(counter, "inc") or hasattr(counter,
    "labels")`. That passes for a counter whose labels do not match what the call
    site passes -- it checks that an attribute exists, not that the call works.
    """
    import app.scheduler as scheduler

    counter = getattr(scheduler, name)
    if name in LABELLED:
        counter.labels(status="test").inc()
    else:
        counter.inc()


def test_importing_the_scheduler_does_not_pull_in_the_application():
    """The other half: this is why the scheduler cost minutes to import.

    Run in a subprocess because the surrounding suite may already have imported
    app.main for its own reasons, which would make an in-process check pass for
    the wrong reason.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c",
         "import app.scheduler, sys; print('app.main' in sys.modules)"],
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "False", (
        "importing app.scheduler loaded app.main; the heavy import chain is back")
