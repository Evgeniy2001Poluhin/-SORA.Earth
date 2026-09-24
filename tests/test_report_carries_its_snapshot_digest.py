"""The phase 6 report names the snapshot it was computed from.

Phase 7's exit criterion in `docs/DEVELOPMENT_ROADMAP.md` is that *"every result
ties to a source and a snapshot"*. `СТАТУС-PHASE-7` records the report as the
place that misses it, and the code agrees: `build_report`'s `snapshot` block
counts points, days and observations and identifies none of them. Two runs over
different data can produce the same counts.

The pattern is already in the repository. `_do_retrain` digests the bytes of
`data/projects.csv` and writes `sha256:...` to both `meta.json` and
`retrain_log.data_version` (#354), so a champion can be tied to the file it
trained on. This is the same closure for the forecast side.

What a digest has to do to be worth adding:

- **identify**, not merely exist. A constant satisfies "the field is present"
  perfectly well, which is how `predictions_log.model_version` came to be the
  literal "v2.0" on every row.
- **not depend on the order rows arrive in.** The query that feeds this is
  `ORDER BY event_time`, but nothing forces that to stay true, and a report
  whose fingerprint changes when the SQL is reordered fingerprints the query
  rather than the data.
- **pair with the rule.** The series is a function of the rows *and* §3's
  coverage floor. `required_hours_per_day` is already in the snapshot; the two
  together determine the numbers.
"""
from datetime import datetime, timedelta, timezone

import math
import os
import random
import subprocess
import sys

import pytest

from app.services.forecasting.daily_target import snapshot_digest
from app.services.forecasting.report import build_report


def _rows(points=("DEU",), days=40, start=datetime(2026, 1, 1, tzinfo=timezone.utc),
          seed=7):
    rng = random.Random(seed)
    out = []
    for point in points:
        level = 10.0
        for d in range(days):
            level += rng.uniform(-0.5, 0.5)
            for hour in range(24):
                out.append((point, start + timedelta(days=d, hours=hour),
                            level + 5 * math.sin(d / 7 * 2 * math.pi) + hour / 24))
    return out


def test_the_report_names_the_snapshot_it_read():
    report = build_report(_rows(), target="openmeteo:temperature")

    digest = report["snapshot"].get("digest")
    assert digest, (
        "the snapshot block counts points, days and observations and identifies "
        "none of them; two runs over different data can report the same counts"
    )
    assert digest.startswith("sha256:"), digest
    assert len(digest) == len("sha256:") + 64, digest


def test_a_changed_snapshot_gets_a_different_digest():
    """The assertion that makes the first one mean anything.

    A constant passes "the field is present". `predictions_log.model_version` is
    the literal "v2.0" on every row precisely because nothing ever checked that
    it varied.
    """
    first = build_report(_rows(seed=7), target="t")["snapshot"]["digest"]
    second = build_report(_rows(seed=11), target="t")["snapshot"]["digest"]

    assert first != second, (
        f"two different snapshots produced {first}; the field cannot "
        f"distinguish one dataset from another and answers nothing"
    )


def test_the_digest_does_not_depend_on_the_order_rows_arrive_in():
    """It must fingerprint the data, not the query.

    The loader orders by `event_time` today. Nothing forces that to stay true --
    a later index change or a UNION would reorder it -- and a digest that moved
    would report a data change that did not happen.
    """
    rows = _rows()
    shuffled = list(rows)
    random.Random(3).shuffle(shuffled)
    assert shuffled != rows, "the shuffle did nothing, so this proves nothing"

    assert snapshot_digest(rows) == snapshot_digest(shuffled)


def test_one_changed_value_moves_the_digest():
    """Down to a single sample, so 'the same snapshot' means the same bytes."""
    rows = _rows()
    altered = list(rows)
    point, when, value = altered[17]
    altered[17] = (point, when, value + 0.001)

    assert snapshot_digest(rows) != snapshot_digest(altered)


def test_an_empty_snapshot_still_has_a_digest():
    """A report over nothing is still a report, and `null` here would read as
    "not recorded" rather than "nothing was read"."""
    report = build_report([], target="t")

    digest = report["snapshot"]["digest"]
    assert digest and digest.startswith("sha256:")
    assert report["snapshot"]["observations"] == 0


def test_the_digest_and_the_rule_travel_together(monkeypatch):
    """The numbers are a function of the rows *and* §3's floor.

    A digest alone would let two reports with the same fingerprint and different
    coverage floors look like the same measurement. The floor is already in the
    snapshot; this pins that both are there at once, so the pair is checkable.
    """
    snapshot = build_report(_rows(), target="t")["snapshot"]

    assert "digest" in snapshot and "required_hours_per_day" in snapshot
    assert snapshot["required_hours_per_day"] == 19


# The script the two subprocesses run. Module level so both of them, and the
# in-process comparison below, are demonstrably over the same rows.
_FRESH_PROCESS = """
import sys
from datetime import datetime, timedelta, timezone
from app.services.forecasting.daily_target import snapshot_digest
rows = [("DEU", datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=h),
         h * 1.5) for h in range(200)]
sys.stdout.write(snapshot_digest(rows))
"""

_FRESH_ROWS = [
    ("DEU", datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=h), h * 1.5)
    for h in range(200)
]


def _digest_from_a_fresh_interpreter(seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed,
               PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    done = subprocess.run([sys.executable, "-c", _FRESH_PROCESS],
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (
        f"the subprocess under PYTHONHASHSEED={seed} exited "
        f"{done.returncode}:\n{done.stderr}"
    )
    return done.stdout.strip()


def test_the_digest_is_the_same_in_a_new_interpreter():
    """The one property the six tests above cannot observe.

    They run in one interpreter under one `PYTHONHASHSEED`. Replace `sorted`
    with `set` and two interpreters digest one snapshot differently, because
    string hashing is randomised per process -- so two runs over one snapshot
    disagree, which is the entire claim the digest makes.

    Measured, because the first version of this docstring asserted that a
    set-based implementation "passes every one of them" and that is false: the
    mutation fails **two** tests, this one and the shuffle above. That second
    catch is incidental rather than a property being tested -- a set of tuples
    built from two insertion orders may or may not iterate alike, depending on
    collision layout -- so it is not something to rely on. This test names the
    property directly, and is the one that stays true when the fixture changes.

    The shape is asserted before the equality. Two subprocesses that both fail
    produce two identical empty strings, and the comparison would pass on them
    -- which is how a check comes to be unable to fail.
    """
    first = _digest_from_a_fresh_interpreter("0")
    second = _digest_from_a_fresh_interpreter("12345")

    assert first.startswith("sha256:") and len(first) == len("sha256:") + 64, (
        f"the subprocess printed {first!r} rather than a digest, so comparing "
        f"it with anything proves nothing"
    )
    assert first == second, (
        f"two interpreters digested one snapshot as {first} and {second}. The "
        f"fingerprint depends on this process rather than on the data, so no "
        f"two reports can be compared by it"
    )
    assert first == snapshot_digest(_FRESH_ROWS), (
        "the subprocesses agree with each other and not with the function this "
        "file tests, so they are exercising something else"
    )
