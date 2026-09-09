"""The roadmap's "migrated" column must be true of the application.

`docs/API_CONTRACT_ROADMAP.md` is a live plan, and a live plan that records
progress will record it wrongly the first time somebody forgets. Two properties
keep it honest, and both are cheap:

    every route the document marks migrated declares a response_model
    the document cites no source line by number

**The second is not a style rule.** The file column carried
`api/infra.py:523` and thirteen more like it, and six of the fourteen already
pointed somewhere else -- measured on `main` before any of this: one landed
inside a comment, another on a different endpoint. A line number is a fact
about a file on the day somebody looked; the route's method and path identify
it and do not move. It is #292's defect, in a document that had no check.

**What is deliberately not checked.** That an unmarked route is *not* migrated.
Marking one early is the mistake this catches; forgetting to mark a finished
one leaves the plan pessimistic, which is the harmless direction, and the
ratchet's own count is what tracks coverage anyway.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "API_CONTRACT_ROADMAP.md"

#: `| ~~`GET /api/v1/model/drift`~~ | `api/drift.py` | migrated in #244 |`
_MIGRATED_ROW = re.compile(
    r"^\|\s*~~`(GET|POST|PUT|PATCH|DELETE) (/api/v1/[^`]+)`~~\s*\|.*\bmigrated\b",
    re.M,
)

#: Any surviving `file.py:123` pointer, which is the thing being removed.
_LINE_REF = re.compile(r"`?([\w/]+\.py):(\d+)(?:-\d+)?`?")


def prose_only(text: str) -> str:
    """The document with fenced blocks removed.

    A pasted command or sample output legitimately contains `file.py:12`, and
    it is not a pointer the reader is meant to follow. Same function, same
    reason, as `tests/test_docs_point_at_real_symbols.py`.
    """
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def migrated_routes() -> list[tuple[str, str]]:
    return _MIGRATED_ROW.findall(DOC.read_text())


def app_routes_with_models() -> dict[tuple[str, str], object]:
    import os

    os.environ.setdefault("SORA_OFFLINE", "1")
    os.environ.setdefault("RUN_SCHEDULER", "false")

    import app.main as main_module

    found = {}
    for route in main_module.app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in (getattr(route, "methods", set()) or set()):
            found[(method, path)] = getattr(route, "response_model", None)
    return found


def test_the_parser_finds_the_rows_it_judges():
    """Negative control. The assertion below passes over an empty list."""
    rows = migrated_routes()
    assert len(rows) >= 5, (
        f"only {len(rows)} migrated rows parsed out of {DOC.name} (nine when "
        "this was written); the table changed shape and this file judges nothing"
    )
    assert app_routes_with_models(), "no routes extracted from the application"


def test_every_route_marked_migrated_declares_a_contract():
    """The claim, checked against the thing it is a claim about.

    A row saying "migrated" and a handler with no `response_model` is the
    document describing work that did not happen -- and it is the plan people
    read to decide what is left.
    """
    routes = app_routes_with_models()
    faults = []
    for method, path in migrated_routes():
        model = routes.get((method, path), "MISSING")
        if model == "MISSING":
            faults.append(f"{method} {path}: no such route in the application")
        elif model is None:
            faults.append(f"{method} {path}: marked migrated, declares no response_model")

    assert not faults, (
        "the roadmap records migrations that did not happen:\n  " + "\n  ".join(faults)
    )


def test_no_line_number_pointers_survive():
    """Six of fourteen were already wrong. Allowing one back allows the class."""
    offenders = [f"{m}:{n}" for m, n in _LINE_REF.findall(prose_only(DOC.read_text()))]

    assert not offenders, (
        "the roadmap cites source lines by number in prose, and those drift the "
        "moment the file above them changes. Name the route instead. (Inside a "
        "fenced block a line number is sample output and is not counted.):\n  "
        + "\n  ".join(offenders)
    )


def test_a_line_number_inside_a_fence_is_output_not_a_pointer():
    """The discriminating case for `prose_only`.

    Without it the rule fails on a pasted traceback -- a correct edit with
    nothing to do with the defect.
    """
    doc = (
        "See `POST /api/v1/predict/v2`.\n"
        "\n"
        "```\n"
        'Traceback: File "app/main.py:521", line 521\n'
        "```\n"
        "\n"
        "And `app/telemetry.py:12` in prose, which is a pointer.\n"
    )

    found = [f"{m}:{n}" for m, n in _LINE_REF.findall(prose_only(doc))]

    assert found == ["app/telemetry.py:12"], found
