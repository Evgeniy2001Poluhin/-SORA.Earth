"""No test may count a server error as a pass.

Seventeen assertions in `tests/` read like

    assert r.status_code in [200, 422, 500]

and a test written that way cannot fail on the thing a test of an endpoint most
needs to catch: the endpoint breaking. Measured 2026-09-22 by recording every
response those tests received:

    test_explain_beeswarm         500 on every run -- and passed
    test_calibration_endpoints    404 on all six paths it requests; none of them
                                  exists (no `/api/v1` prefix, and no such routes)
    test_ab_split                 422: it posted a project to an endpoint that
                                  takes `{"model_a_pct": ...}`
    test_calibration_recalibrate  422: a project posted where a set of
                                  probabilities and labels is expected

The first was a real defect the suite was hiding: `GET /api/v1/explain/beeswarm`
could not answer anything but 500, because the rows it sampled from
`data/projects.csv` carried `social_impact` on a 0-100 scale and the validator it
ran them through accepted 0-10. Fixed: the conversion now happens at the model
boundary, so training rows are converted to API scale before validation. The other
three tested nothing, green.

Each of the seventeen now asserts the answer it should get.

This file keeps the rule: any `status_code in (...)` whose set admits a 5xx is
reported, with no allowance list. A test that genuinely expects a server error
names it with `==`, where the expectation is visible.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def admits_a_server_error(source: str) -> list[tuple[int, str]]:
    """`(line, assertion)` for every membership test of a status that admits a 5xx."""
    found = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        is_status = (isinstance(left, ast.Attribute) and left.attr in ("status_code", "status")) or (
            isinstance(left, ast.Name) and left.id in ("status_code", "status", "code"))
        if not is_status:
            continue
        for op, right in zip(node.ops, node.comparators):
            if not (isinstance(op, ast.In) and isinstance(right, (ast.Tuple, ast.List, ast.Set))):
                continue
            codes = [e.value for e in right.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, int)]
            if any(code >= 500 for code in codes):
                found.append((node.lineno, ast.get_source_segment(source, node) or ""))
    return found


def test_the_detector_sees_the_shapes_it_is_for():
    """Controls: what it must report, and what it must leave alone."""
    assert admits_a_server_error("assert r.status_code in [200, 422, 500]")
    assert admits_a_server_error("assert r.status_code in (200, 503)")
    assert admits_a_server_error("assert status_code in {404, 502}")
    assert not admits_a_server_error("assert r.status_code == 200")
    assert not admits_a_server_error("assert r.status_code == 503")
    assert not admits_a_server_error("assert r.status_code in (200, 404)")
    assert not admits_a_server_error("assert r.status_code != 500")
    assert not admits_a_server_error("assert r.status_code not in (500, 502)")


def test_no_test_counts_a_server_error_as_a_pass():
    offenders = []
    files = sorted(TESTS.glob("*.py"))
    assert len(files) > 100, "the walk over tests/ found almost nothing"
    for path in files:
        if path.name == Path(__file__).name:
            continue
        for line, text in admits_a_server_error(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{line}  {text}")
    assert not offenders, (
        "these assertions pass when the endpoint answers with a server error, so "
        "they cannot catch it breaking. Assert the status the request should "
        "get; if a 5xx is the expected answer, say so with `==`:\n  "
        + "\n  ".join(offenders)
    )
