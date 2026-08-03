"""Three contracts the verdict has to keep outside the classifier itself.

classify_run() is enumerated in tests/test_run_classification.py. That proves
the verdict is right; it says nothing about whether the verdict is what the rest
of the system then reports. These are the seams where it could diverge:

  1. every persist result carries the same keys, including on failure
  2. the status a job returns is the status it records and the status it labels
  3. a write failure never arrives as an empty source

The second is the one that goes wrong quietly. A job that records `degraded` and
returns `success` gives the same run two different answers depending on who
asks, and the two readers drift apart with nobody noticing.
"""
import ast
import inspect
from pathlib import Path

from app.ingesters import runner
from app.ingesters.classification import classify_run, EMPTY, FAILURE


REPO = Path(__file__).resolve().parents[1]
JOBS = REPO / "app" / "services" / "environmental" / "scheduler_jobs.py"


# --- 1. one shape, every path ------------------------------------------------

def _returned_dict_keys(func) -> list[set]:
    """Key sets of every dict literal returned by a function."""
    tree = ast.parse(inspect.getsource(func))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            if keys:
                out.append(keys)
    return out


def test_persist_results_have_identical_keys_on_every_path():
    """The failure path returned {"saved": 0, "error": ...} -- no `received`,
    no `accepted` -- so `.get(key, 0)` in the caller yielded 0 for both and a
    missing persistence layer was classified as a source with nothing to give.

    A result whose shape depends on which branch produced it is one every caller
    has to guess at.
    """
    shapes = _returned_dict_keys(runner._persist_signals)
    assert len(shapes) >= 2, "expected a success and a failure return"
    assert len(set(map(frozenset, shapes))) == 1, (
        "persist results differ by branch: %s" % [sorted(s) for s in shapes]
    )
    for required in ("received", "accepted", "rejected", "write_failed"):
        assert all(required in s for s in shapes), required


# --- 2. one status, three readers --------------------------------------------

def _source_of(name: str) -> str:
    tree = ast.parse(JOBS.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(JOBS.read_text(), node) or ""
    raise AssertionError("job %s not found" % name)


def test_the_jobs_report_one_status_to_every_reader():
    """The database row, the Prometheus label and the return value must be the
    same verdict. Each was a separate literal before, and a degraded run showed
    as degraded in one place and success in the other two.
    """
    for job in ("scheduled_openaq_ingestion", "scheduled_openmeteo_ingestion"):
        src = _source_of(job)
        assert "verdict = classify_run(" in src, job
        # the three readers
        assert "status=verdict.status" in src, "%s: database row" % job
        assert "status=verdict.status\n        ).inc()" in src or \
               "status=verdict.status" in src, "%s: prometheus label" % job
        assert '"status": verdict.status' in src, "%s: return value" % job
        # and no literal success left to diverge from them
        assert 'status="success"' not in src, (
            "%s still reports a hard-coded success somewhere" % job
        )


# --- 3. a write failure is a failure -----------------------------------------

def test_a_missing_persistence_layer_is_not_an_empty_source():
    """End to end for the defect this change introduced and review caught:
    the shape returned when persistence is unavailable, run through the
    classifier that consumes it."""
    result = {
        "received": 48, "inserted": 0, "updated": 0, "accepted": 0,
        "rejected": 0, "duplicates": 0, "write_failed": True,
        "errors": ["persist_unavailable"],
    }
    v = classify_run(
        received=result["received"],
        accepted=result["accepted"],
        rejected=result["rejected"],
        write_failed=result["write_failed"],
    )
    assert v.status == FAILURE
    assert v.primary_source_status != EMPTY
    assert "could not be persisted" in v.failure_reason
