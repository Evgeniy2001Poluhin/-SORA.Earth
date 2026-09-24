"""The drift alert on `/drift/analyze` could not fire, for a type reason.

`app/drift_detection.py` → `population_stability_index` returns one dict per
column:

```python
results[col] = {"psi": round(psi, 4), "drift": psi > 0.2, "severity": ...}
```

`app/api/drift_monitor.py` reduced it with:

```python
max_psi = max((float(v) for v in psi.values() if isinstance(v, (int, float))),
              default=0.0)
```

`v` is a dict, so `isinstance(v, (int, float))` is False for every column, the
generator is empty, and the `default=0.0` is taken **every time**. `max_psi` was
0.0 on every call, `max_psi > threshold` was never true, and `send_alert` was
never reached.

Measured 2026-09-21 on a deliberately drifted frame -- reference N(100, 10),
current N(400, 10), four columns:

```
psi per column   {'psi': 11.5128, 'drift': True, 'severity': 'high'}
max_psi reported 0.0
alert_sent       False
max_psi correct  11.5128   -- 57 times the 0.2 threshold
```

The response carried both at once: a `psi` block saying every feature had
drifted badly, and `max_psi: 0.0, alert_sent: false` beside it.

Nothing read `max_psi` or `alert_sent` anywhere else in the repository, and no
test named `drift_monitor`, so nothing was in a position to notice.

## On the default

It is now `None`, not `0.0`. A reduction over no values means the PSI could not
be computed, and that is not the same as "no drift" -- the distinction #369 was
opened for, one module over. The alert is skipped in that case rather than
suppressed silently, and the payload says `max_psi: null`.
"""
import numpy as np
import pandas as pd
import pytest

from app.drift_detection import run_drift_analysis


def _drifted_frames(cols, shift=300.0, n=400):
    rng = np.random.default_rng(0)
    reference = pd.DataFrame({c: rng.normal(100, 10, n) for c in cols})
    current = pd.DataFrame({c: rng.normal(100 + shift, 10, n) for c in cols})
    return reference, current


@pytest.fixture(scope="module")
def psi_block():
    from app.api.drift_monitor import FEATURE_COLS
    reference, current = _drifted_frames(list(FEATURE_COLS))
    result = run_drift_analysis(reference, current, list(FEATURE_COLS))
    return result.get("psi", {})


def test_the_fixture_really_drifted(psi_block):
    """Without this the reduction below could be reducing over nothing."""
    assert psi_block, "run_drift_analysis returned no psi block at all"
    assert all(isinstance(v, dict) and "psi" in v for v in psi_block.values()), (
        "the psi block is no longer {column: {'psi': ...}}; the reduction in "
        "app/api/drift_monitor.py is written against that shape and this test "
        "with it"
    )
    assert all(v["psi"] > 1.0 for v in psi_block.values()), (
        f"the deliberately drifted frame produced {psi_block}, which is not "
        "drifted enough to tell a working alert from a dead one"
    )


def test_the_reduction_finds_the_drift(psi_block):
    """The defect itself: the reduction skipped every value and returned 0.0."""
    from app.api.drift_monitor import _max_psi

    value = _max_psi(psi_block)
    assert value is not None, "the reduction found nothing in a populated block"
    assert value > 1.0, (
        f"max_psi came back {value} from a block whose every column reports "
        f"psi above 1.0: {psi_block}. The response would carry that block and "
        "'max_psi: 0.0, alert_sent: false' at the same time."
    )


def test_an_empty_block_is_unknown_and_not_zero():
    """A reduction over nothing is not a measurement of no drift.

    0.0 compares below every threshold, so an unmeasurable analysis silently
    read as a clean one.
    """
    from app.api.drift_monitor import _max_psi

    assert _max_psi({}) is None
    assert _max_psi({"budget": {"drift": False}}) is None, (
        "a column carrying no psi value must not contribute a number"
    )


def test_the_alert_threshold_is_actually_crossed(psi_block):
    """End of the chain: with a correct reduction the alert would be sent."""
    import os

    from app.api.drift_monitor import _max_psi

    threshold = float(os.getenv("DRIFT_PSI_THRESHOLD", "0.2"))
    value = _max_psi(psi_block)
    assert value is not None and value > threshold, (
        f"max_psi {value} does not cross the {threshold} threshold on a frame "
        "shifted by thirty standard deviations; the alert would still never fire"
    )
