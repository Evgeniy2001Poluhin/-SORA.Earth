"""Every path in `docs/API.md` must exist in the application (#294).

`README.md` presents that file as the API overview. Measured against the live
route table, five of its 35 headings did not resolve:

    GET  /api/v1/system-metrics                     hyphen, not a slash --
                                                    the route is
                                                    /api/v1/system/metrics
    GET  `/api/v1/model/ики модели.                 heading destroyed
    POST `/ operational report.                     heading destroyed
    POST /api/v1/admin/ai-teammate/run?mode=...     query string in the heading
    (and two prefix mentions, which are prose)

The two destroyed headings date to the initial commit of 2026-05-07, the same
damage and the same day as the unclosed fence in README.md (#284). They were
removed rather than reconstructed: for `/api/v1/model/*` fifteen routes exist
and one is documented, so the surviving tail "ики модели." does not determine
which. An invented API entry is indistinguishable from a real one -- exactly
the defect in #282, where a list of five models carried two that do not exist.

The coverage figure is asserted too. A document describing 33 of 162 reads as
complete unless it says otherwise, so it says otherwise, and the number is
checked rather than trusted.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "API.md"

#: `### GET \`/api/v1/evaluate\`` -- the shape every endpoint entry uses.
_HEADING = re.compile(r"^### (GET|POST|PUT|DELETE|PATCH) `([^`\n]+)`", re.M)


def documented() -> list[tuple[str, str]]:
    """(method, path) pairs, query strings stripped.

    A `?mode=observe` in the heading is documentation of a parameter, not part
    of the path, and matching on it would reject a route that exists. It is
    kept out of the heading in the document as well, so the two agree.
    """
    return [(m, p.split("?")[0]) for m, p in _HEADING.findall(DOC.read_text())]


def app_routes() -> set[tuple[str, str]]:
    os.environ.setdefault("SORA_OFFLINE", "1")
    os.environ.setdefault("RUN_SCHEDULER", "false")
    import app.main as main_module

    return {
        (method, route.path)
        for route in main_module.app.routes
        if hasattr(route, "path")
        for method in (getattr(route, "methods", set()) or set())
        if method not in ("HEAD", "OPTIONS")
    }


def test_the_parser_finds_the_entries_it_judges():
    """Negative control. The assertions below pass over an empty list."""
    entries = documented()
    assert len(entries) >= 25, (
        f"only {len(entries)} endpoint headings parsed out of {DOC.name}; the "
        "heading format changed and this file judges nothing"
    )
    assert app_routes(), "no routes extracted from the application"


def test_every_documented_path_exists_in_the_application():
    """The defect: a documented endpoint that answers 404.

    Method and path together. `/api/v1/evaluate` exists for POST and not for
    GET, and a document that gets the verb wrong sends the reader to a 405.
    """
    routes = app_routes()
    missing = [f"{m} {p}" for m, p in documented() if (m, p) not in routes]

    assert not missing, (
        "docs/API.md documents endpoints the application does not serve:\n  "
        + "\n  ".join(missing)
    )


def test_no_heading_carries_a_query_string():
    """A parameter belongs in the description, not in the path.

    Kept as its own assertion because `documented()` strips query strings to
    resolve them -- without this, a heading could grow one back and the
    stripping would quietly hide it.
    """
    offenders = [
        f"{m} {p}" for m, p in _HEADING.findall(DOC.read_text()) if "?" in p
    ]
    assert not offenders, (
        "these headings put parameters in the path, where they cannot be "
        "checked against the route table:\n  " + "\n  ".join(offenders)
    )


def test_the_document_says_it_is_a_selection_and_the_figure_is_right():
    """A partial list reads as complete unless it says it is partial.

    The stated count is checked against both sides -- the document's own
    headings and the application's route table -- so adding an entry to one
    without the other reddens.
    """
    text = DOC.read_text()
    claimed = re.search(r"описаны (\d+) из (\d+) пар", text)
    assert claimed, (
        "docs/API.md no longer states its coverage; without it a list of 33 "
        "reads as the whole API"
    )

    stated_documented, stated_total = int(claimed.group(1)), int(claimed.group(2))
    api_routes = {r for r in app_routes() if r[1].startswith("/api/")}

    assert stated_documented == len(documented()), (
        f"the document says it describes {stated_documented} endpoints and "
        f"carries {len(documented())} headings"
    )
    assert stated_total == len(api_routes), (
        f"the document says there are {stated_total} (path, method) pairs under "
        f"/api/; the application has {len(api_routes)}"
    )
