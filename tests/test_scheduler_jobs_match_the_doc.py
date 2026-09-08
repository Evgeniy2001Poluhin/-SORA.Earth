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


#: Written out, because the paragraph this checks is prose. Only the intervals
#: this scheduler actually uses.
_IN_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 6: "six", 8: "eight",
             12: "twelve", 24: "twenty-four"}


def startup_jobs() -> set[str]:
    """`RUN_IMMEDIATELY_ON_STARTUP`, read from the code."""
    tree = ast.parse(SCHEDULER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(x, ast.Name) and x.id == "RUN_IMMEDIATELY_ON_STARTUP"
                   for x in node.targets):
            continue
        return {
            e.value for e in getattr(node.value, "elts", [])
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        }
    return set()


def test_the_startup_tuple_is_readable_and_not_empty():
    """Negative control. The assertion below passes over an empty set."""
    jobs = startup_jobs()
    assert len(jobs) >= 3, (
        f"only {sorted(jobs)} parsed out of RUN_IMMEDIATELY_ON_STARTUP; the "
        "check below judges nothing"
    )
    assert jobs <= set(jobs_in_code()), (
        f"RUN_IMMEDIATELY_ON_STARTUP names jobs that are not registered: "
        f"{sorted(jobs - set(jobs_in_code()))}"
    )


def test_the_forecast_blind_window_the_document_states_is_the_one_the_code_produces():
    """The document claims a six-hour gap after every deployment. Both halves
    of that claim are in the code, and both can move without anyone noticing.

    `sora_forecast_mae_current` and its three neighbours are labelled gauges:
    a series exists only after `.labels(...).set(...)` runs, a restart empties
    the registry, and the only caller is this job. So the window is exactly
    "the interval, unless the job also runs at startup" -- and #284 read a
    `series=0` taken inside it as a metric nobody writes.
    """
    assert "auto_pretrain_forecast" not in startup_jobs(), (
        "auto_pretrain_forecast now runs at startup, so the forecast gauges "
        "are set right after a deployment and CLAUDE.md's paragraph about a "
        "six-hour blind window is no longer true. See #156 for whether that "
        "is the right trade -- this test only says the two must agree."
    )

    trigger = jobs_in_code()["auto_pretrain_forecast"]
    hours = re.search(r"hours=(\d+)", trigger)
    assert hours, f"auto_pretrain_forecast no longer runs on an hourly interval: {trigger}"

    stated = _IN_WORDS.get(int(hours.group(1)))
    assert stated, f"no word for an interval of {hours.group(1)} hours"
    assert f"up to {stated} hours after every" in DOC.read_text(), (
        f"the job runs every {hours.group(1)}h, and CLAUDE.md does not say the "
        f"gauges are absent for up to {stated} hours after a deployment"
    )
