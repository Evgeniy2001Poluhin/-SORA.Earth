"""A project's place on the map does not depend on which process drew it.

`/evaluate` nudged a project's marker off its country's centre by
`(hash(project.name) % 10 - 5) * 0.3` degrees. Python randomises `hash()` of a
string per process, and nothing here sets `PYTHONHASHSEED`: production runs
four gunicorn workers (`WORKERS: "4"`). Measured 2026-09-24, one name, four
processes: offsets 1.2, 0.3, 0.0, -0.6 -- up to 2.7 degrees, about 300 km, and
the coordinates are written to `evaluations`. The same project, evaluated twice,
landed in two places depending on the worker, and moved after every restart.

The property shows only across processes -- within one, `hash()` is stable -- so
the first test runs the function in two fresh interpreters under different
seeds. The shape is asserted before the equality: two failing subprocesses
print two identical empty strings, and the comparison would pass on them.
"""
import os
import subprocess
import sys

import pytest

from app.map_position import marker_offset

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROBE = "from app.map_position import marker_offset; print(repr(marker_offset('Solar Farm Almaty')))"


def _offset_in_a_fresh_interpreter(seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=REPO_ROOT)
    done = subprocess.run([sys.executable, "-c", _PROBE], env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_the_offset_is_the_same_in_every_process():
    values = {seed: _offset_in_a_fresh_interpreter(seed) for seed in ("0", "1", "2", "12345")}

    for seed, v in values.items():
        float(v)  # raises on anything that is not a number, so empty cannot pass
    assert len(set(values.values())) == 1, (
        f"one project name gets different map offsets per process: {values}. "
        f"With four workers the same project lands in different places."
    )


def test_the_offset_keeps_its_old_range():
    """The nudge exists to un-stack markers; it must stay a small nudge."""
    names = [f"project {i}" for i in range(500)]
    offsets = {marker_offset(n) for n in names}
    assert min(offsets) >= -1.5 - 1e-9 and max(offsets) <= 1.2 + 1e-9, offsets
    assert len(offsets) >= 8, f"only {len(offsets)} distinct offsets over 500 names; markers would stack again"


def test_evaluate_stores_the_marker_at_that_offset(client):
    """Wired, not only defined: the row the map reads carries this offset."""
    from app.main import COUNTRIES

    name = "Solar Farm Almaty"
    r = client.post("/api/v1/evaluate", json={"name": name, "budget": 100000, "co2_reduction": 50,
                                              "social_impact": 7, "duration_months": 24, "region": "Germany"})
    assert r.status_code == 200, r.text
    items = client.get("/api/v1/history").json()["items"]
    row = next(i for i in items if i.get("name") == name)
    assert row["lat"] == pytest.approx(COUNTRIES["Germany"]["lat"] + marker_offset(name))
    assert row["lon"] == pytest.approx(COUNTRIES["Germany"]["lon"] + marker_offset(name))
