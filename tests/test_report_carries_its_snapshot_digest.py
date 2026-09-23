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
import random

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
