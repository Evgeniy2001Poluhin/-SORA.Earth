"""Four ways to get zero, and the instrument must tell them apart.

`scripts/measure_ambiguity_window.py` answers the roadmap's remaining P0. The
number it reports is the kind that lies by accident: `0 rows without
registry_ok` is produced by a healthy deployment, by an empty table, by a table
whose rows all predate the field, and by a deployment running code that never
writes it.

The fourth was the real case on 2026-09-21: 49 rows locally, none carrying the
field, newest 2026-09-20 -- five weeks after the writer landed -- because the
checkout the containers mount was 266 commits behind `origin/main`.

Each verdict is driven here rather than described, and
`test_the_classifier_is_not_constant` exists because four assertions about four
inputs prove nothing if the function returns the same thing for everything.
"""
import json
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from measure_ambiguity_window import (  # noqa: E402
    MEASURED, NONE_AFTER_BOUNDARY, NO_ROWS, WRITER_LANDED, WRITER_NEVER_RAN,
    classify,
)

BEFORE = WRITER_LANDED - timedelta(days=30)
AFTER = WRITER_LANDED + timedelta(days=30)

CARRIES = json.dumps({"auc_roc": 0.9, "registry_ok": True})
FAILED = json.dumps({"auc_roc": 0.9, "registry_ok": False})
SILENT = json.dumps({"auc_roc": 0.9})


def test_an_empty_table_is_not_a_clean_bill():
    w = classify([])
    assert w.verdict == NO_ROWS
    assert w.total == 0
    assert "empty" in w.reason
    assert "not a result" in w.reason, (
        "the reason must say the zero is the size of the table; a caller "
        "reading only the count would take it for an answer"
    )


def test_rows_that_all_predate_the_writer_are_neither_defect_nor_evidence():
    w = classify([(BEFORE, SILENT), (BEFORE, None)])
    assert w.verdict == NONE_AFTER_BOUNDARY
    assert w.rows_after_boundary == 0
    assert w.ambiguous == 2, "both rows lack the field, and that is expected"
    assert "could have carried" in w.reason


def test_the_local_case_is_named_rather_than_counted():
    """Rows after the boundary, none carrying it: the writer never ran."""
    rows = [(AFTER, SILENT)] * 5 + [(BEFORE, None)]
    w = classify(rows)
    assert w.verdict == WRITER_NEVER_RAN
    assert w.rows_after_boundary == 5
    assert w.carrying == 0
    assert "predates the writer" in w.reason
    assert "revision actually serving" in w.reason, (
        "the reason must point at the deployment, which is where the answer is"
    )


def test_a_real_window_is_measured():
    """Some rows carry it, some do not: this is the number the roadmap wants."""
    rows = [(AFTER, CARRIES), (AFTER, SILENT), (AFTER, FAILED), (BEFORE, None)]
    w = classify(rows)
    assert w.verdict == MEASURED
    assert w.rows_after_boundary == 3
    assert w.ambiguous_after_boundary == 1, (
        "only the row with no registry_ok at all is ambiguous; "
        "registry_ok=False is an answer, not a silence"
    )
    assert w.carrying == 2


def test_a_false_value_counts_as_carrying_not_as_silence():
    """`is False` is a refusal; absent is not. The instrument must agree."""
    w = classify([(AFTER, FAILED), (AFTER, CARRIES)])
    assert w.carrying == 2
    assert w.ambiguous == 0
    assert w.ambiguous_after_boundary == 0


def test_unparseable_metrics_count_as_silence():
    """A row whose JSON is broken carries no opinion, and must not be read as one."""
    w = classify([(AFTER, "{not json"), (AFTER, CARRIES)])
    assert w.with_metrics == 2, "both have a non-empty metrics_json"
    assert w.ambiguous == 1
    assert w.ambiguous_after_boundary == 1


def test_timestamps_that_arrive_as_strings_are_still_compared():
    """SQLite hands back a string, and `str > datetime` raises.

    Found by running the script against a real table rather than by reading
    it: every test above fed datetimes, so the comparison was never exercised
    the way a driver actually returns the column.
    """
    rows = [(AFTER.isoformat(), CARRIES), (AFTER.isoformat(), SILENT),
            (BEFORE.isoformat(), None)]
    w = classify(rows)
    assert w.rows_after_boundary == 2, (
        "string timestamps were not placed relative to the boundary"
    )
    assert w.ambiguous_after_boundary == 1
    assert w.verdict == MEASURED


def test_a_timestamp_that_cannot_be_parsed_does_not_crash():
    """Better an uncounted row than a traceback in an instrument."""
    w = classify([("not a date", SILENT), (AFTER, CARRIES)])
    assert w.total == 2
    assert w.rows_after_boundary == 1, (
        "an unparseable timestamp cannot be placed, so it is not counted as "
        "after the boundary"
    )


def test_the_classifier_is_not_constant():
    """Without this, every assertion above passes on a function that returns one value."""
    verdicts = {
        classify([]).verdict,
        classify([(BEFORE, SILENT)]).verdict,
        classify([(AFTER, SILENT)]).verdict,
        classify([(AFTER, CARRIES), (AFTER, SILENT)]).verdict,
    }
    assert verdicts == {NO_ROWS, NONE_AFTER_BOUNDARY, WRITER_NEVER_RAN, MEASURED}, (
        f"the four inputs produced {sorted(verdicts)}; each must reach its own "
        "verdict or the distinctions are decorative"
    )


@pytest.mark.parametrize("rows,expected_exit_is_zero", [
    ([(AFTER, CARRIES), (AFTER, SILENT)], True),
    ([], False),
    ([(AFTER, SILENT)], False),
])
def test_only_a_measured_window_is_a_success(rows, expected_exit_is_zero):
    """The exit status must not let "not measurable" pass as "measured"."""
    assert (classify(rows).verdict == MEASURED) is expected_exit_is_zero
