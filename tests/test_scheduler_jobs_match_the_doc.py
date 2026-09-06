"""`CLAUDE.md`'s job table must be what `scheduler.add_job(...)` produces.

A list of scheduled jobs is exactly the kind of fact that drifts silently: it
changes in code, nobody re-reads the document, and the document keeps being
believed because it is specific.

That is not hypothetical here. Before this test, `CLAUDE.md` named three
functions that do not exist -- `check_drift_job`, `auto_retrain_on_drift_job`,
`refresh_external_data_job` -- and gave periods no trigger produces ("drift
detection every 6h", "retrain on drift every 12h"). On 2026-09-06 that cost an
operator a wait for a run that could not happen: the closed loop is daily at
03:00 UTC, and there is no separate drift job at all.

Same family as the `legacy_hash_count()` and `year`/`quarter` notes in the
document itself -- prose that has quietly stopped describing the code.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "CLAUDE.md"
SCHEDULER = ROOT / "app" / "scheduler.py"

BEGIN = "<!-- BEGIN SCHEDULED JOBS -->"
END = "<!-- END SCHEDULED JOBS -->"


def jobs_in_code() -> dict[str, str]:
    """Every `scheduler.add_job(...)`, by id, with its trigger source."""
    tree = ast.parse(SCHEDULER.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_job"):
            continue
        job_id = next(
            (ast.literal_eval(k.value) for k in node.keywords
             if k.arg == "id" and isinstance(k.value, ast.Constant)),
            None,
        )
        trigger = ast.unparse(node.args[1]) if len(node.args) > 1 else "?"
        if job_id:
            found[job_id] = trigger
    return found


def jobs_in_doc() -> set[str]:
    body = DOC.read_text(encoding="utf-8")
    assert BEGIN in body and END in body, "the generated block markers are gone"
    table = body.split(BEGIN, 1)[1].split(END, 1)[0]
    # First backticked cell of each table row.
    return {
        m.group(1)
        for line in table.splitlines()
        if line.startswith("|")
        for m in [re.match(r"\|\s*`([^`]+)`", line)]
        if m
    }


def test_the_document_lists_every_registered_job():
    in_code = set(jobs_in_code())
    in_doc = jobs_in_doc()

    assert in_code, "no jobs parsed — the collector itself is broken"
    missing = in_code - in_doc
    assert not missing, f"registered but undocumented: {sorted(missing)}"


def test_the_document_invents_no_jobs():
    extra = jobs_in_doc() - set(jobs_in_code())

    assert not extra, f"documented but not registered: {sorted(extra)}"


@pytest.mark.parametrize("job_id", sorted(jobs_in_code()))
def test_each_trigger_is_quoted_as_written(job_id: str):
    """The period is the part people plan against, so it has to match exactly."""
    trigger = jobs_in_code()[job_id]
    body = DOC.read_text(encoding="utf-8")
    row = next(
        (line for line in body.split(BEGIN, 1)[1].split(END, 1)[0].splitlines()
         if line.startswith(f"| `{job_id}`")),
        None,
    )
    assert row, f"{job_id} has no row"

    # Compare without whitespace and quote style: the document renders
    # `day_of_week="sun"` where the source has single quotes.
    normalise = lambda s: s.replace(" ", "").replace("'", '"')
    assert normalise(trigger) in normalise(row), (
        f"{job_id}: code says {trigger}, the document row says {row.strip()}"
    )


def test_every_function_the_document_names_exists():
    """It named three that did not.

    A name written with call parentheses reads as something you can go and
    find. Three in this document could not be found, and one of them was the
    reason an operator expected a run that was never scheduled.
    """
    doc = DOC.read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z_][a-z0-9_]*)\(\)`", doc))
    assert named, "no function names found — the pattern stopped matching"

    sources = "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in (ROOT / "app").rglob("*.py")
    )
    missing = sorted(n for n in named if f"def {n}" not in sources)

    assert not missing, f"named in CLAUDE.md but defined nowhere in app/: {missing}"
