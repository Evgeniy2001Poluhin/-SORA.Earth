"""`## Now` must not list work the tree shows is finished.

`docs/DEVELOPMENT_ROADMAP.md` has a `## Now` table and an `## Immediate
sequence` block. They are the first thing anyone reads to decide what to do
next, and on 2026-09-21 six of the nine rows were already done:

```
Finish the #199 review        P0   #199 closed COMPLETED 2026-09-19T11:28:09Z
#199 Phase 0                  P0   the same issue
#199 Phase 1                  P1   the same issue
Design #191 with staged/active P1  #191 closed COMPLETED 2026-09-19T11:28:16Z
Build the evaluation harness  P1   scripts/evaluate_forecast.py shipped in #356
Watch M3 coverage             P1   auto_observation_coverage shipped in #350
```

and `## Immediate sequence` still opened with `#199 Phase 0 → #199 Phase 1 →
#191 + seed/staged/active`.

This is the defect the whole September queue was about, in the one document
that says what to do next. `docs/WHERE_THE_PROJECT_STANDS.md` §3B put it
exactly: a document that lists a decision already taken invites it to be taken
again, differently.

## Why witnesses in the tree rather than issue state

A test cannot ask GitHub whether #199 is closed: CI runs the suite without
credentials, and a check that needs the network fails for reasons that have
nothing to do with the claim. So each row is bound to something in this
repository that exists **because** the work was done, and the assertion runs
both ways:

- the witness holds  -> the row must not be listed as outstanding;
- the witness is gone -> the row may be listed, and the check says so rather
  than passing silently on a repository where the work was reverted.

That is the shape `tests/test_where_we_stand_is_current.py` already uses for the
confidence bound: read the source, and require the prose to agree in whichever
direction it points.

## The limit, stated rather than papered over

A row with no witness here is not checked. `Specialist interviews and UX
scenarios` has no artefact in the tree, and inventing one would be worse than
leaving it uncovered -- so `test_every_witness_is_reachable` asserts that the
witnesses that *are* declared still resolve, and the docstring says the rest
are on a human.
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(REPO, "docs", "DEVELOPMENT_ROADMAP.md")


def _read(*parts):
    path = os.path.join(REPO, *parts)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def roadmap_text():
    text = _read("docs", "DEVELOPMENT_ROADMAP.md")
    assert text, "docs/DEVELOPMENT_ROADMAP.md is missing or empty"
    return text


def section(name, text=None):
    """The body of a `## name` section, up to the next `## `."""
    text = roadmap_text() if text is None else text
    match = re.search(
        r"^##\s+" + re.escape(name) + r"\s*$(.*?)(?=^##\s|\Z)",
        text, re.M | re.S)
    assert match, f"docs/DEVELOPMENT_ROADMAP.md has no '## {name}' section"
    return match.group(1)


#: The `## Now` table is identified by its own header, not by being the first
#: table in the section. The section also carries a table recording what was
#: removed from it -- and that table necessarily names the finished work, so a
#: check over the whole section matched its own explanation and could never go
#: green. Measured while writing this file.
_TASK_HEADER = "| Priority | Task | Result |"


def planned_tasks():
    """What the roadmap lists as work to start -- the rows, not the prose.

    Two sources: the Task column of the `## Now` priority table, and the steps
    inside the fenced block under `## Immediate sequence`. Anything else in
    either section is commentary, including the record of what used to be here.
    """
    tasks = []

    now = section("Now")
    start = now.find(_TASK_HEADER)
    assert start != -1, (
        "the '## Now' section has no '" + _TASK_HEADER + "' table; this check "
        "reads the task rows and cannot find them"
    )
    for line in now[start:].splitlines()[2:]:
        if not line.strip().startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2:
            tasks.append(cells[1])

    seq = section("Immediate sequence")
    fence = re.search(r"```(.*?)```", seq, re.S)
    assert fence, "the '## Immediate sequence' section has no fenced block"
    for line in fence.group(1).splitlines():
        step = line.strip().lstrip("\u2192").strip()
        if step:
            tasks.append(step)

    return tasks


def what_is_planned():
    """The planned rows joined, for a substring search over tasks only."""
    return "\n".join(planned_tasks())


def _seed_is_read_only():
    """#191: models/ is a seed the runtime cannot write."""
    compose = _read("docker-compose.prod.yml") or ""
    guarded = _read("tests", "test_seed_is_immutable.py")
    return ("./models:/app/models:ro" in compose) and bool(guarded)


def _one_gate_exists():
    """#199: one promotion decision, reached by every path that promotes."""
    gate = _read("app", "promotion.py") or ""
    return "def gated_decision" in gate and "def evaluate_promotion" in gate


def _coverage_is_watched():
    """The M3 coverage job, shipped in #350."""
    return 'id="auto_observation_coverage"' in (_read("app", "scheduler.py") or "")


def _harness_exists():
    """The forecast evaluation harness, shipped in #356."""
    return _read("scripts", "evaluate_forecast.py") is not None


#: (phrase the roadmap uses, what in the tree proves it is done, why)
WITNESSES = [
    ("#199", _one_gate_exists,
     "app/promotion.py defines evaluate_promotion and gated_decision"),
    ("#191", _seed_is_read_only,
     "docker-compose.prod.yml mounts ./models read-only and "
     "tests/test_seed_is_immutable.py guards it"),
    ("Watch M3 coverage", _coverage_is_watched,
     'app/scheduler.py registers the job id "auto_observation_coverage"'),
    ("evaluation harness", _harness_exists,
     "scripts/evaluate_forecast.py exists"),
]


def test_the_parsers_find_what_they_judge():
    """Both sections must parse, or every assertion below is vacuous."""
    now = section("Now")
    seq = section("Immediate sequence")
    assert "|" in now, "the '## Now' section holds no table"
    assert len(seq.strip().splitlines()) > 3, (
        "the '## Immediate sequence' section is too short to hold a sequence"
    )
    tasks = planned_tasks()
    assert len(tasks) >= 5, (
        f"only {len(tasks)} task rows were parsed out of both sections; a parser "
        "that finds nothing makes every check below pass hollow"
    )
    assert any("pilot" in t for t in tasks), (
        "the sequence steps were not parsed: 'pilot' is its last step and is "
        "missing from what was read"
    )


def test_every_witness_is_reachable():
    """A witness that cannot resolve makes its row unfalsifiable.

    Without this, deleting `app/promotion.py` would turn the #199 check from
    "the work is done, stop planning it" into "no opinion", silently.
    """
    missing = [phrase for phrase, holds, _ in WITNESSES if not holds()]
    assert not missing, (
        "these roadmap rows have a witness in the tree that no longer resolves: "
        + ", ".join(missing)
        + ".\nEither the work was reverted -- in which case the row belongs in "
        "'## Now' again and this check should be updated deliberately -- or the "
        "witness moved and this file is now measuring nothing."
    )


@pytest.mark.parametrize("phrase,holds,evidence", WITNESSES,
                         ids=[w[0] for w in WITNESSES])
def test_finished_work_is_not_listed_as_what_to_do_next(phrase, holds, evidence):
    """Both directions, because either drift is a different wrong document."""
    planned = what_is_planned()
    mentioned = phrase in planned

    if holds():
        assert not mentioned, (
            f"'## Now' or '## Immediate sequence' still proposes {phrase!r}, "
            f"but {evidence}.\n"
            "A plan that lists finished work invites it to be done again, and "
            "it is the first thing read to decide what to start."
        )
    else:
        assert mentioned, (
            f"{evidence} -- no longer true -- and the roadmap does not list "
            f"{phrase!r} as outstanding either. Work that is neither done nor "
            "planned is work nobody owns."
        )
